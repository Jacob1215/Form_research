"""图片工具：Markdown 图片引用解析与 /api/uploads 绝对 URL 重写。

供文档预览（admin_docs.get_document）与 RAG 对话（检索结果图片展示）共用。

注意：本模块不 import ORM models，避免循环依赖；文档路径信息通过参数传入。
"""
import os
import re
import logging
import urllib.parse

from .config import settings
from .text_utils import MD_IMAGE_RE

logger = logging.getLogger("app.image_utils")

# Markdown 图片正则（![alt](src)，URL 可含嵌套括号）由 text_utils.MD_IMAGE_RE 共享
# HTML <img> 标签（双引号 / 单引号 src）
_IMG_TAG_DQ_RE = re.compile(r'(<img\s+[^>]*?src=)"([^"]+)"([^>]*?>)', re.IGNORECASE)
_IMG_TAG_SQ_RE = re.compile(r"(<img\s+[^>]*?src=)'([^']+)'([^>]*?>)", re.IGNORECASE)

# 绝对地址前缀：http(s)://、data:、/api/ 开头的路径不重写、不落盘校验
_ABSOLUTE_PREFIXES = ("http://", "https://", "data:", "/api/")

# 支持的 Markdown 文档类型（只有 md 才解析其中的图片引用）
_MD_TYPES = ("md", "markdown")


def _is_absolute(src: str) -> bool:
    return src.startswith(_ABSOLUTE_PREFIXES)


def doc_rel_dir(kb_id: int, file_path: str) -> str:
    """文档所在目录相对 uploads/{kb_id}/ 的路径（正斜杠、去掉 ./）。"""
    kb_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id))
    doc_dir_abs = os.path.dirname(os.path.abspath(file_path))
    try:
        rel = os.path.relpath(doc_dir_abs, os.path.abspath(kb_dir))
    except ValueError:
        rel = ""
    return rel.replace("\\", "/").lstrip("./")


def to_upload_url(src: str, kb_id: int, rel_dir: str) -> str:
    """相对图片路径 → 可访问的 /api/uploads/... URL（含百分号编码）。

    空格 → %20、括号 → %28%29、中文 → 百分号编码；保留 / 与已有编码。
    """
    clean_src = src.lstrip("./")
    new_src = f"/api/uploads/{kb_id}/{rel_dir}/{clean_src}" if rel_dir else f"/api/uploads/{kb_id}/{clean_src}"
    return urllib.parse.quote(new_src, safe="/%")


def _safe_abs_path(kb_id: int, rel_dir: str, src: str) -> str | None:
    """拼接磁盘绝对路径并校验在 uploads/{kb_id}/ 内（防目录穿越）。

    与 main.serve_upload 的安全拼接逻辑保持一致；越界返回 None。
    """
    base = os.path.abspath(os.path.join(settings.UPLOAD_DIR, str(kb_id)))
    parts = [p for p in rel_dir.split("/") if p and p not in (".", "..")] if rel_dir else []
    rel = src.lstrip("./")
    candidate = os.path.abspath(os.path.join(base, *parts, rel))
    if not candidate.startswith(base):
        return None
    return candidate


def rewrite_md_image_paths(content: str, kb_id: int, file_path: str, rel_dir: str | None = None) -> str:
    """将 Markdown 中的相对图片路径重写为后端可访问的 /api/uploads URL。

    仅重写相对路径（./xxx、xxx.png、images/xxx）；不处理 http(s)://、data:、
    以及已以 /api/ 开头的路径。rel_dir 可传入以复用已计算的目录，缺省时按
    kb_id + file_path 计算。

    原实现位于 admin_docs._rewrite_md_image_paths，迁移到此处后行为保持一致。
    """
    if rel_dir is None:
        rel_dir = doc_rel_dir(kb_id, file_path)

    def _to_url(src: str) -> str:
        return to_upload_url(src, kb_id, rel_dir)

    def _replace(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2).strip()
        if _is_absolute(src):
            return m.group(0)
        return f"![{alt}]({_to_url(src)})"

    content = MD_IMAGE_RE.sub(_replace, content)

    def _replace_img_tag(m: re.Match) -> str:
        before, src, after = m.group(1), m.group(2).strip(), m.group(3)
        if _is_absolute(src):
            return m.group(0)
        return f'{before}src="{_to_url(src)}"{after}'

    content = _IMG_TAG_DQ_RE.sub(_replace_img_tag, content)
    content = _IMG_TAG_SQ_RE.sub(_replace_img_tag, content)

    return content


def extract_images_from_content(
    content: str,
    kb_id: int,
    rel_dir: str,
    doc_file_type: str,
    max_images: int = 9,
) -> list[tuple[str, str]]:
    """从分块/文档文本中提取图片引用，重写为绝对 URL。

    仅对 markdown 文档提取（md / markdown）；PDF/txt/docx 文本里出现的字面
    ![]() 或 <img> 可能是原文误报，直接跳过。对重写后的相对地址做磁盘存在性
    校验，过滤掉不存在的图片，避免对话中出现裂图。

    Args:
        content: 分块或文档文本
        kb_id: 知识库 ID
        rel_dir: 文档目录相对 uploads/{kb_id}/ 的路径（doc_rel_dir 计算）
        doc_file_type: 文档类型（md / markdown / pdf ...）
        max_images: 返回的最大图片数

    Returns:
        [(alt_text, absolute_url), ...]，按出现顺序、URL 去重，最多 max_images 张。
    """
    if not content or not content.strip():
        return []
    if (doc_file_type or "").lower() not in _MD_TYPES:
        return []

    results: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(alt: str, src: str) -> None:
        src = src.strip()
        # 只提取本知识库内的相对图片；http(s)://、data:、/api/ 等绝对地址跳过
        if not src or _is_absolute(src):
            return
        disk = _safe_abs_path(kb_id, rel_dir, src)
        if disk is None or not os.path.isfile(disk):
            logger.debug("图片不存在，跳过: %s", src)
            return
        url = to_upload_url(src, kb_id, rel_dir)
        if url in seen:
            return
        seen.add(url)
        results.append((alt or "[图片]", url))

    for m in MD_IMAGE_RE.finditer(content):
        _add(m.group(1), m.group(2))
        if len(results) >= max_images:
            return results
    for m in _IMG_TAG_DQ_RE.finditer(content):
        _add("", m.group(2))
        if len(results) >= max_images:
            return results
    for m in _IMG_TAG_SQ_RE.finditer(content):
        _add("", m.group(2))
        if len(results) >= max_images:
            return results

    return results
