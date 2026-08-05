"""各功能界面（报告/PPT）skill 库（V1.1.3+）：按界面分目录存放、读取与注入。

- V1.1.3：从 backend/app/skills/ 读取 skill，供前端选择并按名注入提示词。
- V1.2.2：按功能界面物理分离 — 每个界面的 skill 存于 backend/app/skills/{scope}/ 子目录
  （report/ 与 ppt/），只扫描这些子目录；根目录下的杂项文件/目录（如 agent 型 skill 包）
  自动忽略，杜绝跨界面污染。

注意：本应用直接调用第三方大模型 API，无 agent 工具/脚本执行能力，只注入 skill 的指令文本；
依赖脚本执行或联网抓取的 skill 部分不生效。
"""
import os
import logging

from .config import settings

logger = logging.getLogger("app.skills")

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")

# 各功能界面（子目录名）→ scope
SCOPE_DIRS = ("report", "ppt")


def _strip_frontmatter(text: str) -> str:
    """剥离开头的 YAML frontmatter（---...---）。"""
    stripped = text.lstrip("﻿")
    if stripped.startswith("---"):
        idx = stripped.find("\n---", 3)
        if idx != -1:
            return stripped[idx + 4:].lstrip()
    return stripped


def _parse_frontmatter(content: str, fname: str, default_scope: str = "report") -> tuple[str, str, str]:
    """简易解析 frontmatter 的 name/description/scope（无 PyYAML 依赖）。

    - name 缺省用文件名；
    - scope 缺省用 default_scope（由所在子目录决定，frontmatter 显式声明可覆盖）。
    """
    name = os.path.splitext(os.path.basename(fname))[0]
    description = ""
    scope = default_scope
    stripped = content.lstrip("﻿")
    if stripped.startswith("---"):
        idx = stripped.find("\n---", 3)
        if idx != -1:
            fm = stripped[3:idx]
            for line in fm.splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("scope:"):
                    scope = line.split(":", 1)[1].strip().strip("\"'") or default_scope
    return name, description, scope


def _read_skill_file(path: str) -> str | None:
    """读取单个 skill 文件内容，失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        logger.warning("读取 skill 失败 %s: %s", path, e)
        return None


def _parse_skill(content: str, fname: str, scope_override: str | None = None) -> dict:
    """解析 skill 内容 → {name, description, scope, body}（body 已剥 frontmatter）。

    scope_override 为所在子目录的界面名；frontmatter 未显式声明 scope 时用它归属。
    """
    name, description, scope = _parse_frontmatter(content, fname, scope_override or "report")
    return {"name": name, "description": description, "scope": scope, "body": _strip_frontmatter(content)}


def _read_skills(scope: str | None = None) -> list[dict]:
    """读取 skill，返回 [{name, description, scope, body}]。

    scope 为 "report"/"ppt" 时只读对应子目录；None 返回全部子目录的并集。
    只扫描 skills/{scope}/ 子目录，根目录杂项（含 agent 型 skill 包）自动忽略。
    """
    if not os.path.isdir(SKILLS_DIR):
        return []
    scopes = [scope] if scope in SCOPE_DIRS else list(SCOPE_DIRS)
    items: list[dict] = []
    for sc in scopes:
        sub = os.path.join(SKILLS_DIR, sc)
        if not os.path.isdir(sub):
            continue
        for fname in sorted(os.listdir(sub)):
            if not fname.endswith((".md", ".txt")):
                continue
            content = _read_skill_file(os.path.join(sub, fname))
            if content:
                items.append(_parse_skill(content, fname, scope_override=sc))
    return items


def list_skills(scope: str | None = None) -> list[dict]:
    """返回 skill 的 {name, description}，供前端 / 菜单展示。

    scope 传入时只返回该界面（"report" / "ppt"）子目录的 skill；None 返回全部。
    """
    return [{"name": it["name"], "description": it["description"]} for it in _read_skills(scope)]


def get_skills_block(names: list[str], header: str = "## 报告编制技能规则（必须遵循）\n\n",
                     max_chars: int | None = None) -> str:
    """返回选中 skill 的指令块（未知名忽略，超限截断）。未选返回空串。

    header / max_chars 可配置，供 PPT 等其它功能复用（默认沿用报告值）。
    """
    if not names:
        return ""
    by_name = {it["name"]: it for it in _read_skills()}
    parts: list[str] = []
    for name in names:
        it = by_name.get(name)
        if it and it["body"].strip():
            parts.append(it["body"])
    if not parts:
        return ""
    block = header + "\n\n".join(parts)
    cap = max_chars if max_chars is not None else settings.REPORT_SKILLS_MAX_CHARS
    if len(block) > cap:
        block = block[:cap] + "\n\n...（技能规则过长，已截断）"
    return block
