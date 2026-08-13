"""ppt-master HTTP wrapper（V1.2.6）。

下载 hugohe3/ppt-master 并包装为独立容器后，向 PPT 制作页面提供服务：
容器内运行 Claude Code CLI + ppt-master skill，端到端生成原生可编辑 PPTX。

接口（供后端 ppt.py 调用）：
- POST /generate            入参 {brief, title, base_url, api_key, model} → {task_id}
- GET  /tasks/{task_id}     任务状态 {status: queued|running|done|error, message}
- GET  /tasks/{task_id}/file 下载生成的 .pptx
- GET  /health              存活探针

说明：
- ppt-master 本身无 HTTP API，本文件是唯一自研胶水层；后端只跟它通信，不 docker exec。
- 大模型凭据由后端每次请求从 llm_configs 解密后经 /generate 下发；此处转成 Claude Code
  的 Anthropic 兼容环境变量（ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_MODEL）。
- 每个任务在 /work/<task_id> 下运行 claude，生成的项目落在 /app/ppt-master/projects/，
  完成后再把 exports/*.pptx 回传；项目目录仅作临时 scratch，无需持久化。
"""
import os
import uuid
import threading
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

REPO_DIR = Path(os.environ.get("PPT_MASTER_REPO_DIR", "/app/ppt-master"))
WORK_DIR = Path(os.environ.get("PPT_MASTER_WORK_DIR", "/work"))
PROJECTS_ROOT = REPO_DIR / "projects"
SKILL_SRC = REPO_DIR / "skills" / "ppt-master"

# 单任务最长运行时长（秒）。Quick Generate 为多步 agent（逐页写 SVG），耗时数分钟。
TASK_TIMEOUT = int(os.environ.get("PPT_MASTER_TASK_TIMEOUT", "1800"))

app = FastAPI(title="ppt-master-service")

_TASKS: dict[str, dict] = {}
_LOCK = threading.Lock()


class GenerateRequest(BaseModel):
    brief: str
    title: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""


def _build_prompt(brief: str, title: str, project_name: str) -> str:
    return (
        "请使用 ppt-master 技能（Quick Generate 模式）生成一份中文 PPTX 演示文稿。\n\n"
        "必须遵守：\n"
        "1. 使用 Quick Generate 模式：跳过策略/确认环节，全程不询问、不确认，"
        "一次性完成「初始化项目 → 逐页编写 SVG → 运行最终检查 → 导出 pptx」全流程。\n"
        f"2. 项目名固定为 {project_name}，画布格式 ppt169（viewBox 0 0 1280 720）。\n"
        "3. 不生成 AI 图片、不联网搜图，全部使用矢量图形、文字与原生图表/表格排版。\n"
        "4. 导出命令使用 --quick-generate --no-notes（不要演讲者备注）。\n"
        "5. 完成前确认 exports/ 目录下已生成 .pptx 文件。\n\n"
        f"演示文稿标题：{title or '未命名演示文稿'}\n\n"
        "以下是编制 PPT 的全部素材与内容依据：\n\n"
        f"{brief}"
    )


def _find_pptx(work: Path, project_name: str, started_at: float) -> str | None:
    """定位任务产出的 .pptx。

    claude 可能把项目建在 CWD（/work/<task_id>）下，也可能按 PROJECTS_ROOT 建，
    因此先递归搜任务工作目录，再兜底 PROJECTS_ROOT。只取 mtime 晚于任务启动时间的，
    避免把上一任务的产物误判为本任务。
    """
    candidates: list[str] = []
    if work.is_dir():
        for p in work.rglob("*.pptx"):
            try:
                if p.stat().st_mtime >= started_at:
                    candidates.append(str(p))
            except OSError:
                continue
    for exports_dir in PROJECTS_ROOT.glob("*/exports"):
        for p in exports_dir.glob("*.pptx"):
            try:
                if p.stat().st_mtime >= started_at:
                    candidates.append(str(p))
            except OSError:
                continue
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _ensure_skill_link(work: Path) -> None:
    """在任务工作目录建 .claude/skills/ppt-master 软链，确保 headless claude 可发现 skill。

    软链会解析到真实路径，因此 skill 内部的 PROJECTS_ROOT 仍指向 /app/ppt-master/projects，
    与 Dockerfile 里全局安装的 skill 保持一致。
    """
    if not SKILL_SRC.is_dir():
        return
    link = work / ".claude" / "skills" / "ppt-master"
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        try:
            link.symlink_to(SKILL_SRC, target_is_directory=True)
        except OSError:
            pass


def _run(task_id: str, req: GenerateRequest) -> None:
    task = _TASKS[task_id]
    started_at = time.time()
    project_name = f"ppt_{task_id}"
    work = WORK_DIR / task_id
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "claude.log"

    env = os.environ.copy()
    if req.base_url:
        env["ANTHROPIC_BASE_URL"] = req.base_url
    if req.api_key:
        env["ANTHROPIC_AUTH_TOKEN"] = req.api_key
        env["ANTHROPIC_API_KEY"] = req.api_key
    if req.model:
        env["ANTHROPIC_MODEL"] = req.model
        # 统一小模型/背景模型，避免代理只服务单一模型时后台 haiku 调用失败
        env["ANTHROPIC_SMALL_FAST_MODEL"] = req.model
        env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] = req.model
        env["ANTHROPIC_DEFAULT_SONNET_MODEL"] = req.model
    env.setdefault("CLAUDE_CODE_SKIP_MODEL_CHECK", "1")

    _ensure_skill_link(work)

    prompt = _build_prompt(req.brief, req.title, project_name)
    cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions", "--output-format", "text"]

    with _LOCK:
        task["status"] = "running"
        task["message"] = "Claude Code 正在生成 PPT（Quick Generate）..."

    try:
        with open(log_path, "w", encoding="utf-8") as logf:
            proc = subprocess.run(
                cmd,
                cwd=str(work),
                env=env,
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=TASK_TIMEOUT,
            )
        pptx = _find_pptx(work, project_name, started_at)
        if pptx:
            with _LOCK:
                task["status"] = "done"
                task["file"] = pptx
                task["message"] = "生成完成"
        else:
            tail = ""
            try:
                tail = log_path.read_text(encoding="utf-8", errors="ignore")[-2000:]
            except OSError:
                pass
            with _LOCK:
                task["status"] = "error"
                task["message"] = f"未找到生成的 .pptx（claude 退出码 {proc.returncode}）"
                task["error"] = tail
    except subprocess.TimeoutExpired:
        with _LOCK:
            task["status"] = "error"
            task["message"] = f"生成超时（>{TASK_TIMEOUT // 60} 分钟）"
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            task["status"] = "error"
            task["message"] = f"生成异常：{e}"


@app.post("/generate")
def generate(req: GenerateRequest):
    if not (req.brief or "").strip():
        raise HTTPException(status_code=400, detail="brief 不能为空")
    task_id = uuid.uuid4().hex
    with _LOCK:
        _TASKS[task_id] = {"id": task_id, "status": "queued", "message": "已排队"}
    threading.Thread(target=_run, args=(task_id, req), daemon=True).start()
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "task_id": task_id,
        "status": task.get("status"),
        "message": task.get("message", ""),
    }


@app.get("/tasks/{task_id}/file")
def get_file(task_id: str):
    task = _TASKS.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.get("status") != "done" or not task.get("file"):
        raise HTTPException(status_code=404, detail="任务尚未完成")
    path = task["file"]
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=os.path.basename(path),
    )


@app.get("/health")
def health():
    return {"status": "ok"}
