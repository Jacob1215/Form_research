"""报告 skill 库（V1.1.3）：读取 backend/app/skills/ 下的 skill，供前端选择并按名注入提示词。

支持两种来源：
1. 简单 `.md` 文件（手写 skill）→ 直接读取内容。
2. 从 GitHub 下载的 Claude Code skill 目录（含 `SKILL.md`）→ 剥离 YAML frontmatter 取正文；
   目录下若有 `references/*.md`，一并合并进该 skill。

注意：本应用直接调用第三方大模型 API，无 agent 工具/脚本执行能力，只注入 skill 的指令文本；
依赖脚本执行或联网抓取的 skill 部分不生效。
"""
import os
import logging

from .config import settings

logger = logging.getLogger("app.skills")

SKILLS_DIR = os.path.join(os.path.dirname(__file__), "skills")


def _strip_frontmatter(text: str) -> str:
    """剥离开头的 YAML frontmatter（---...---）。"""
    stripped = text.lstrip("﻿")
    if stripped.startswith("---"):
        idx = stripped.find("\n---", 3)
        if idx != -1:
            return stripped[idx + 4:].lstrip()
    return stripped


def _parse_frontmatter(content: str, fname: str) -> tuple[str, str]:
    """简易解析 frontmatter 的 name/description（无 PyYAML 依赖）；name 缺省用文件名。"""
    name = os.path.splitext(os.path.basename(fname))[0]
    description = ""
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
    return name, description


def _read_skill_file(path: str) -> str | None:
    """读取单个 skill 文件内容，失败返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        logger.warning("读取 skill 失败 %s: %s", path, e)
        return None


def _parse_skill(content: str, fname: str) -> dict:
    """解析 skill 内容 → {name, description, body}（body 已剥 frontmatter）。"""
    name, description = _parse_frontmatter(content, fname)
    return {"name": name, "description": description, "body": _strip_frontmatter(content)}


def _collect_dir_skills(path: str) -> list[dict]:
    """收集一个 skill 目录：SKILL.md 主体 + references/*.md 合并为一个 skill 项。"""
    items: list[dict] = []
    skill_md = os.path.join(path, "SKILL.md")
    if os.path.isfile(skill_md):
        content = _read_skill_file(skill_md)
        if content:
            item = _parse_skill(content, "SKILL.md")
            refs_dir = os.path.join(path, "references")
            if os.path.isdir(refs_dir):
                ref_parts: list[str] = []
                for fname in sorted(os.listdir(refs_dir)):
                    if fname.endswith(".md"):
                        rc = _read_skill_file(os.path.join(refs_dir, fname))
                        if rc:
                            ref_parts.append(f"## 参考：{fname}\n\n{rc}")
                if ref_parts:
                    item["body"] = item["body"] + "\n\n" + "\n\n".join(ref_parts)
            items.append(item)
    return items


def _read_skills() -> list[dict]:
    """解析 skills/ 下全部 skill，返回 [{name, description, body}]。"""
    if not os.path.isdir(SKILLS_DIR):
        return []
    items: list[dict] = []
    for entry in sorted(os.listdir(SKILLS_DIR)):
        entry_path = os.path.join(SKILLS_DIR, entry)
        if os.path.isfile(entry_path) and entry.endswith((".md", ".txt")):
            content = _read_skill_file(entry_path)
            if content:
                items.append(_parse_skill(content, entry))
        elif os.path.isdir(entry_path):
            items.extend(_collect_dir_skills(entry_path))
    return items


def list_skills() -> list[dict]:
    """返回全部 skill 的 {name, description}，供前端 / 菜单展示。"""
    return [{"name": it["name"], "description": it["description"]} for it in _read_skills()]


def get_skills_block(names: list[str]) -> str:
    """返回选中 skill 的指令块（未知名忽略，超限截断）。未选返回空串。"""
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
    block = "## 报告编制技能规则（必须遵循）\n\n" + "\n\n".join(parts)
    cap = settings.REPORT_SKILLS_MAX_CHARS
    if len(block) > cap:
        block = block[:cap] + "\n\n...（技能规则过长，已截断）"
    return block
