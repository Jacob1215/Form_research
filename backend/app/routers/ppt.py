"""PPT 制作路由（V1.2.1+；V1.2.6 起接入 ppt-master 容器）。

V1.2.6 变更：删除本地「大模型两阶段生成 Markdown → python-pptx 导出」链路，
改为调用 ppt-master Docker 容器（Claude Code + ppt-master skill）端到端生成
**原生可编辑 PPTX**。后端只负责：检索知识库、读取附件、组装 brief、下发大模型
凭据、轮询容器任务并把产物落盘，供前端 SSE 流式展示进度与下载。

事件：progress（进度）、ppt_file（download_url，生成完成）、done、error。
"""
import os
import re
import time
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, SessionLocal
from ..models import KnowledgeBase, PptRecord
from ..schemas import PptChatRequest, PptRecordCreate
from ..security import decrypt
from ..rag_service import retrieve_with_hybrid, _build_structured_context
from .. import ppt_master_client
# 复用报告路由的纯函数（SSE 格式 / 启用大模型 / 附件侧车读取）
from .report import _sse, _get_active_llm, _read_doc_sidecar

logger = logging.getLogger("app.ppt")

router = APIRouter(prefix="/api/ppt", tags=["ppt"])

# PPT 输出目录：{UPLOAD_DIR}/ppt/out/{task_id}.pptx（本地落盘，供下载与历史记录）
PPT_UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "ppt")
PPT_OUT_DIR = os.path.join(PPT_UPLOAD_DIR, "out")

# task_id 为容器返回的 32 位 hex（uuid4().hex），用于落盘文件名与下载路由防目录穿越
_TASK_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _record_to_dict(rec: PptRecord, include_content: bool = False) -> dict:
    """PptRecord → 字典。content 自 V1.2.6 起存 pptx 的 task_id（file_id）。"""
    d = {
        "id": rec.id,
        "title": rec.title,
        "kb_id": rec.kb_id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }
    if include_content:
        d["content"] = rec.content
        d["question"] = rec.question
    return d


def _cap_doc_text(text: str) -> str:
    """上传文档文本超长时按首尾截断（中间省略），避免 brief 撑爆容器上下文。"""
    cap = settings.REPORT_DOC_TEXT_CAP
    if len(text) <= cap:
        return text
    head = text[: cap * 2 // 3]
    tail = text[-cap // 3:]
    return head + "\n\n...（文档过长，中间部分省略）...\n\n" + tail


def _gather_doc_texts(req: PptChatRequest) -> list[tuple[str, str]]:
    """收集上传文档文本（同 URL 去重，多轮追问不重复注入），返回 [(name, text)]。"""
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for m in req.messages:
        if m.role != "user" or not m.documents:
            continue
        for ref in m.documents:
            if not ref.url or ref.url in seen:
                continue
            text = _read_doc_sidecar(ref.url)
            if not text:
                continue
            seen.add(ref.url)
            name = (ref.name or "").strip() or "参考文档"
            out.append((name, _cap_doc_text(text)))
    return out


def _build_brief(req: PptChatRequest, context_block: str, doc_texts: list[tuple[str, str]]) -> str:
    """组装发给 ppt-master 容器的编制 brief（标题 + 主题 + 知识库上下文 + 附件文本）。"""
    user_msgs = [m for m in req.messages if m.role == "user" and (m.content or "").strip()]
    topic = (user_msgs[-1].content if user_msgs else "").strip()
    parts: list[str] = []
    title = (req.title or "").strip()
    if title:
        parts.append(f"标题：{title}")
    if topic:
        parts.append(f"主题与要求：\n{topic}")
    if context_block:
        parts.append(f"知识库检索到的相关规范条文：\n{context_block}")
    for name, text in doc_texts:
        parts.append(f"参考文档《{name}》：\n{text}")
    return "\n\n".join(parts)


def _resolve_anthropic(llm) -> tuple[str, str, str]:
    """确定传给容器 Claude Code 的 Anthropic 兼容凭据。

    优先用部署时显式配置的 PPT_MASTER_ANTHROPIC_*；未配则回退用 llm_configs 活动行
    的 api_url / api_key / model_name。
    """
    base_url = settings.PPT_MASTER_ANTHROPIC_BASE_URL or llm.api_url
    api_key = settings.PPT_MASTER_ANTHROPIC_AUTH_TOKEN or decrypt(llm.api_key_enc)
    model = settings.PPT_MASTER_ANTHROPIC_MODEL or llm.model_name
    return base_url, api_key, model


def _stream_ppt(req: PptChatRequest):
    """SSE 流：检索知识库 + 读附件 → 组 brief → 调 ppt-master 容器 → 轮询进度 → 落盘。

    事件：progress（进度）、ppt_file（download_url）、done、error。
    """
    db = SessionLocal()
    try:
        # kb_id 为空表示不选知识库（纯文本编制，不检索）
        kb = db.get(KnowledgeBase, req.kb_id) if req.kb_id is not None else None
        if req.kb_id is not None and kb is None:
            yield _sse({"type": "error", "message": "知识库不存在"})
            return

        llm = _get_active_llm(db)
        if llm is None:
            yield _sse({"type": "error", "message": "未配置启用的大模型，请联系管理员"})
            return
        base_url, api_key, model = _resolve_anthropic(llm)
        if not api_key:
            yield _sse({"type": "error", "message": "大模型密钥无法解密"})
            return

        # 取最后一条用户消息作为检索主题
        user_msgs = [m for m in req.messages if m.role == "user" and (m.content or "").strip()]
        query = (user_msgs[-1].content if user_msgs else "").strip()

        # 知识库检索（仅在选择知识库时；异常时降级：仍可基于用户文本编制）
        context_block = ""
        if req.kb_id is not None and query:
            try:
                results = retrieve_with_hybrid(db, req.kb_id, query, top_k=settings.RAG_TOP_K, doc_ids=req.doc_ids)
                context_block = _build_structured_context(results, query)
            except Exception as e:  # noqa: BLE001
                logger.warning("PPT 知识库检索失败，降级为纯文本编制: %s", e)

        # 上传附件文本（复用报告端点的侧车 txt）
        doc_texts = _gather_doc_texts(req)
        brief = _build_brief(req, context_block, doc_texts)
        title = (req.title or "").strip()

        # 提交 ppt-master 容器任务
        yield _sse({"type": "progress", "message": "正在提交 PPT 生成任务..."})
        try:
            task_id = ppt_master_client.generate(brief, title, base_url, api_key, model)
        except Exception as e:  # noqa: BLE001
            logger.error("提交 ppt-master 任务失败：%s", e)
            yield _sse({"type": "error", "message": f"提交 PPT 生成任务失败：{e}"})
            return

        # 轮询任务状态，回传进度（保持 SSE 长连接）
        deadline = time.time() + settings.PPT_MASTER_TIMEOUT
        last_msg = ""
        status = "running"
        while True:
            if time.time() > deadline:
                yield _sse({"type": "error", "message": "PPT 生成超时，请稍后重试"})
                return
            try:
                st = ppt_master_client.get_task(task_id)
            except Exception as e:  # noqa: BLE001
                logger.error("查询 ppt-master 任务失败：%s", e)
                yield _sse({"type": "error", "message": f"查询 PPT 任务失败：{e}"})
                return
            status = st.get("status")
            msg = st.get("message", "")
            if status in ("done", "error"):
                break
            if msg and msg != last_msg:
                yield _sse({"type": "progress", "message": msg})
                last_msg = msg
            time.sleep(settings.PPT_MASTER_POLL_INTERVAL)

        if status == "error":
            yield _sse({"type": "error", "message": msg or "PPT 生成失败"})
            return

        # 下载产物并落盘到本地 out/ 目录（下载与历史记录都读本地文件，不依赖容器保留）
        try:
            content = ppt_master_client.download(task_id)
        except Exception as e:  # noqa: BLE001
            logger.error("下载 ppt-master 产物失败：%s", e)
            yield _sse({"type": "error", "message": f"下载 PPT 失败：{e}"})
            return
        os.makedirs(PPT_OUT_DIR, exist_ok=True)
        out_path = os.path.join(PPT_OUT_DIR, f"{task_id}.pptx")
        try:
            with open(out_path, "wb") as f:
                f.write(content)
        except OSError as e:
            logger.error("PPT 产物落盘失败：%s", e)
            yield _sse({"type": "error", "message": f"保存 PPT 失败：{e}"})
            return

        yield _sse({"type": "ppt_file", "download_url": f"/api/ppt/download/{task_id}"})
        yield _sse({"type": "done"})
    except Exception as e:  # noqa: BLE001
        logger.exception("PPT SSE 处理异常")
        try:
            yield _sse({"type": "error", "message": f"内部错误：{e}"})
        except Exception:
            pass
    finally:
        db.close()


@router.post("/chat")
def ppt_chat(req: PptChatRequest):
    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return StreamingResponse(
        _stream_ppt(req),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------- pptx 下载 ----------

@router.get("/download/{task_id}")
def download_ppt(task_id: str):
    """下载已生成的 pptx（本地落盘文件）。task_id 为 32 位 hex，防目录穿越。"""
    if not _TASK_ID_RE.fullmatch(task_id):
        raise HTTPException(status_code=404, detail="文件不存在")
    path = os.path.join(PPT_OUT_DIR, f"{task_id}.pptx")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{task_id}.pptx",
    )


# ---------- PPT 记录（手动保存） ----------

@router.get("/records")
def list_ppt_records(db: Session = Depends(get_db)):
    """已保存 PPT 列表（按时间倒序）。"""
    rows = db.execute(
        select(PptRecord).order_by(PptRecord.created_at.desc())
    ).scalars().all()
    return {"items": [_record_to_dict(r) for r in rows]}


@router.post("/records")
def create_ppt_record(req: PptRecordCreate, db: Session = Depends(get_db)):
    """手动保存一份已生成的演示文稿。content 存 pptx 的 task_id（file_id）。"""
    file_id = (req.content or "").strip()
    src = os.path.join(PPT_OUT_DIR, f"{file_id}.pptx")
    if not _TASK_ID_RE.fullmatch(file_id) or not os.path.isfile(src):
        raise HTTPException(status_code=400, detail="PPT 文件不存在或非法")
    rec = PptRecord(
        title=(req.title or "").strip() or "PPT",
        content=file_id,
        question=(req.question or "").strip() or None,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _record_to_dict(rec, include_content=True)


@router.get("/records/{record_id}")
def get_ppt_record(record_id: int, db: Session = Depends(get_db)):
    """PPT 详情（content 为 pptx 的 file_id，下载走 /api/ppt/download/{file_id}）。"""
    rec = db.get(PptRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="PPT 记录不存在")
    return _record_to_dict(rec, include_content=True)


@router.delete("/records/{record_id}")
def delete_ppt_record(record_id: int, db: Session = Depends(get_db)):
    """删除 PPT 记录。"""
    rec = db.get(PptRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="PPT 记录不存在")
    db.delete(rec)
    db.commit()
    return {"ok": True}
