"""报告总结编制路由：图片/文档上传、多轮对话（SSE）、docx 导出下载。

版本演进：
- V1.1：报告总结功能上线。复用已有大模型配置与知识库向量检索；支持上传图片/文档，
  图片以 Markdown 语法注入上下文，导出 docx 时由 docx_service 落盘嵌入。
- V1.1.1：kb_id 允许为空（不选知识库，纯资料编制）；支持上传 docx/txt/md/pdf 文档。
- V1.1.2：两阶段生成 — 先流式输出要点（显示在对话框），再静默生成完整报告（供导出）。
- V1.1.3：引入 skill 库（skill_library），用户可选用技能注入 system prompt 指令块；
  报告输出 token 上限取 max(LLM 配置, REPORT_MAX_TOKENS) 防止输出被截断。
"""
import os
import json
import uuid
import logging
from typing import Optional
from urllib.parse import urlsplit, unquote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, SessionLocal
from ..models import LLMConfig, KnowledgeBase, ReportRecord
from ..schemas import (
    ReportChatRequest, ReportExportRequest, ReportRecordCreate,
)
from ..security import decrypt
from ..llm_provider import factory as llm_factory
from ..rag_service import retrieve_with_hybrid, _build_structured_context
from ..docx_service import generate_docx
from ..skill_library import get_skills_block, list_skills
from .admin_docs import _extract_text

logger = logging.getLogger("app.report")

router = APIRouter(prefix="/api/report", tags=["report"])

# 报告上传图片目录：{UPLOAD_DIR}/report/{session_id}/文件名
REPORT_UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "report")

# 允许上传的图片类型
IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp", "bmp"}
# V1.1.1：允许上传的文档类型（复用 admin_docs._extract_text 提取文本）
DOC_EXT = {"docx", "txt", "md", "pdf"}
# 注入 LLM 上下文的文档文本上限取 settings.REPORT_DOC_TEXT_CAP（可配置）

# 报告编制 System Prompt（不含上下文，上下文按轮次注入 user 消息）
REPORT_SYSTEM_PROMPT = """你是一名专业的报告编制助手，负责基于用户提供的报告、参考资料（含图片）以及知识库检索到的规范条文，归纳总结并编制结构化的总结报告。

## 工作步骤
1. 仔细阅读用户输入的报告/参考资料、上传的参考图片，以及「知识库检索到的相关规范条文」。
2. 提炼核心要点、关键数据与规范要求，按清晰的结构编制总结报告。

## 输出要求
1. 使用规范的 Markdown 结构：一级标题用 `##`，二级标题用 `###`，正文条目化。
2. 报告通常包含：概述、主要内容与要点、规范依据（如有）、结论与建议等章节，可根据资料内容灵活调整。
3. 引用知识库规范时标注出处：《规范名称》。
4. 若用户上传的参考图片能辅助说明，请在报告中用 Markdown 图片语法在对应位置嵌入该图片：`![图片说明](/api/report/uploads/...)`。仅可使用提供的图片地址，禁止编造。
5. 不得编造资料中不存在的内容；资料不足时明确说明缺什么。

## 多轮修订
用户就报告提出修改意见（如精简、补充、调整结构）时，仅针对性地修订报告，保持整体结构完整。
"""

# V1.1.2：要点生成提示词（阶段一，只输出要点，不输出完整正文）
KEY_POINTS_SYSTEM_PROMPT = """你是一名专业的报告编制助手。你负责根据用户提供的报告、参考资料（含图片）以及知识库检索到的规范条文，先输出一份报告**要点**。

## 输出要求
1. 用条目化输出报告的要点（- 或 1. 开头），覆盖：报告主题、主要章节与结论、关键规范依据（注明条款号）、需特别关注的事项。
2. 每条要点简洁明确，不展开长篇正文；数量通常 8-15 条。
3. 引用规范时标注出处（《规范名称》+条款号）。
4. 不得编造资料中不存在的内容。
"""


def _build_system_prompt(base: str, skills_block: str = "") -> str:
    """在基础 system prompt 后追加已选 skill 指令块（未选则不追加）。"""
    if not skills_block:
        return base
    return f"{base}\n\n{skills_block}"


def _get_active_llm(db) -> Optional[LLMConfig]:
    """获取当前启用的大模型配置（与 chat 路由一致）。"""
    from sqlalchemy import select
    return db.execute(
        select(LLMConfig).where(LLMConfig.is_active == True).limit(1)  # noqa: E712
    ).scalar_one_or_none()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _safe_join(base: str, *paths: str) -> str | None:
    """安全拼接路径，防止目录穿越。"""
    abs_base = os.path.abspath(base)
    candidate = os.path.abspath(os.path.join(abs_base, *paths))
    if not candidate.startswith(abs_base):
        return None
    return candidate


def _ext(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def _extract_report_doc_text(file_path: str, ext: str) -> str:
    """报告上传文档文本提取。

    md/txt 全量读取（不走 admin_docs 的 10 万字上限，避免超长规范文档尾部章节丢失）；
    pdf/docx 复用 admin_docs._extract_text。
    """
    if ext in ("txt", "md"):
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()
        except OSError as e:
            logger.warning("读取文档失败 %s: %s", file_path, e)
            return ""
    return _extract_text(file_path, ext) or ""


def _doc_sidecar_path(url: str) -> str | None:
    """文档 URL → 对应侧车 txt 路径（URL 形如 /api/report/uploads/{session}/{name}.{ext}）。"""
    path = unquote(urlsplit(url or "").path)
    prefix = "/api/report/uploads/"
    if not path.startswith(prefix):
        return None
    rel = path[len(prefix):]
    session, filename = os.path.split(rel)
    stem = os.path.splitext(filename)[0]
    return os.path.join(REPORT_UPLOAD_DIR, session, stem + ".txt")


def _read_doc_sidecar(url: str) -> str:
    """读取上传文档的侧车完整文本，失败返回空串。

    大文档不再在此截断——超过 REPORT_DOC_TEXT_CAP 的文档由 _stream_report
    走分块读取（map-reduce）以覆盖全文。
    """
    p = _doc_sidecar_path(url)
    if not p or not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except OSError:
        return ""


# ---------- 大文档分块读取（map-reduce） ----------

def _chunk_document(text: str, size: int) -> list[str]:
    """将长文档切分为约 size 字符的块。

    优先在 Markdown 标题（#{1,6}）处切分，使新块尽量从章节开始；
    无标题或标题间隔过大时按字符切分。
    """
    import re
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    head_re = re.compile(r"(?m)^(#{1,6})\s+\S")
    heads = [m.start() for m in head_re.finditer(text)]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        nxt = next((b for b in heads if b > start), None)
        if nxt is not None and nxt - start <= size:
            end = nxt  # 切到下一个标题，让新块从标题开始
        else:
            end = start + size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _summarize_chunk(chunk: str, idx: int, total: int, doc_name: str, cfg, provider) -> str:
    """map 步：让大模型提炼单块要点。失败时返回失败占位，不中断整体。"""
    prompt = (
        f"你是一名工程规范条文提炼助手。以下是《{doc_name}》文档的第 {idx}/{total} 部分"
        "（可能因分块在章节边界处截断）。\n\n"
        f"【文档片段 {idx}/{total}】\n{chunk}\n\n"
        "请提炼本部分的要点，用条目化中文输出，要求：\n"
        "1. 保留条款编号（如 9.2.1、16.7），每一条注明出处条款号。\n"
        "2. 保留关键强制性条文、施工要求、技术参数的关键原文表述。\n"
        "3. 条目化、简洁，不要遗漏本部分任何主要规定。\n"
        "4. 只提炼本部分内容，不要概括全文；不要编造。\n"
        "5. 输出控制在 500 字以内。"
    )
    messages = [
        {"role": "system", "content": "你是一名严谨的工程规范条文提炼助手。"},
        {"role": "user", "content": prompt},
    ]
    try:
        summary = "".join(provider.stream_chat(messages, cfg)).strip()  # type: ignore[arg-type]
        return summary[:2000] if summary else f"（第{idx}块提炼为空）"
    except Exception as e:  # noqa: BLE001
        logger.warning("文档第 %d/%d 块提炼失败：%s", idx, total, e)
        return f"（第{idx}块提炼失败）"


def _doc_summary_path(url: str) -> str | None:
    """文档要点汇总缓存路径（与侧车同目录的 .summary.txt）。"""
    p = _doc_sidecar_path(url)
    if not p:
        return None
    return os.path.splitext(p)[0] + ".summary.txt"


def _get_cached_summary(url: str) -> str | None:
    """读取已缓存的文档要点汇总；无缓存返回 None。"""
    p = _doc_summary_path(url)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read() or None
    except OSError:
        return None


def _write_summary_cache(url: str, summary: str) -> None:
    """写入文档要点汇总缓存（文档不可变，缓存长期有效）。"""
    p = _doc_summary_path(url)
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            f.write(summary)
    except OSError as e:
        logger.warning("写入文档要点缓存失败 %s: %s", p, e)


def _combine_chunk_summaries(parts: list[str]) -> str:
    """合并各块要点：过滤失败占位；超限时**首尾整块保留**，中间省略。

    从尾部截断会丢掉靠后章节（如规范第 16 章），因此超限时保留首部约 2/3 +
    尾部约 1/3 的完整分块要点，确保末尾章节内容不丢。
    """
    ok = [p for p in parts if p and not p.startswith("（第")]
    failures = [p for p in parts if p.startswith("（第")]
    full = "\n".join(ok)
    cap = settings.REPORT_CHUNK_SUMMARY_CAP
    if len(full) <= cap:
        summary = full
    else:
        budget = cap - 60  # 预留省略提示长度
        # 首部整块
        head_blocks: list[str] = []
        head_len = 0
        for b in ok:
            if head_len + len(b) <= budget * 2 // 3:
                head_blocks.append(b)
                head_len += len(b)
            else:
                break
        # 尾部整块
        tail_blocks: list[str] = []
        tail_len = 0
        for b in reversed(ok):
            if tail_len + len(b) <= budget // 3:
                tail_blocks.append(b)
                tail_len += len(b)
            else:
                break
        omitted = len(ok) - len(head_blocks) - len(tail_blocks)
        mid = f"\n\n...（中间 {omitted} 个分块的要点已省略，首尾章节完整保留）...\n\n"
        summary = "\n".join(head_blocks) + mid + "\n".join(reversed(tail_blocks))
    if failures:
        summary += "\n（有部分块提炼失败：" + "、".join(failures) + "）"
    return summary



# ---------- 图片 / 文档上传 ----------

@router.post("/upload")
async def upload_report_files(files: list[UploadFile] = File(...)):
    """上传报告编制界面的参考图片或文档（docx/txt/md/pdf）。

    文档上传时同步提取文本，存为 .txt 侧车文件，供后续对话注入 LLM 上下文。
    """
    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")
    max_files = getattr(settings, "REPORT_MAX_IMAGES", 20)
    if len(files) > max_files:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {max_files} 个文件")
    max_size = getattr(settings, "REPORT_MAX_IMAGE_SIZE_MB", 20) * 1024 * 1024

    session_dir = os.path.join(REPORT_UPLOAD_DIR, uuid.uuid4().hex)
    os.makedirs(session_dir, exist_ok=True)

    saved: list[dict] = []
    for upload in files:
        ext = _ext(upload.filename or "")
        if ext not in IMAGE_EXT and ext not in DOC_EXT:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{upload.filename}")
        data = await upload.read()
        if not data:
            continue
        if len(data) > max_size:
            raise HTTPException(status_code=400, detail=f"文件过大（>{max_size // (1024 * 1024)}MB）：{upload.filename}")
        name = f"{uuid.uuid4().hex}.{ext}"
        save_path = os.path.join(session_dir, name)
        with open(save_path, "wb") as f:
            f.write(data)

        item: dict = {
            "url": f"/api/report/uploads/{os.path.basename(session_dir)}/{name}",
            "name": upload.filename or name,
            "size": len(data),
            "kind": "image" if ext in IMAGE_EXT else "doc",
        }
        if ext in DOC_EXT:
            # 提取文本存侧车，供大模型阅读（md/txt 全量，防超长文档尾部章节丢失）
            text = _extract_report_doc_text(save_path, ext)
            txt_path = os.path.join(session_dir, os.path.splitext(name)[0] + ".txt")
            try:
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write(text)
            except OSError as e:
                logger.warning("写入文档侧车失败 %s: %s", name, e)
        saved.append(item)
    return {"items": saved}


@router.get("/skills")
def list_report_skills():
    """V1.1.3：报告 skill 清单（名称+描述），供输入框 / 菜单选择。"""
    return {"items": list_skills()}


@router.get("/uploads/{file_path:path}")
def serve_report_upload(file_path: str):
    """提供报告上传图片访问，仅允许 report 目录内。"""
    target = _safe_join(REPORT_UPLOAD_DIR, file_path)
    if target is None or not os.path.isfile(target):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    return FileResponse(target)


# ---------- 多轮对话（SSE） ----------

def _build_llm_messages(
    req: ReportChatRequest,
    context_block: str = "",
    doc_override: dict[str, str] | None = None,
    system_prompt: str | None = None,
    tail_instruction: str = "",
) -> list[dict]:
    """组装发送给大模型的消息列表。

    历史消息原样透传；user 消息先追加上传文档的提取文本（doc_override 中的大文档
    注入其分块要点汇总）、再追加上传图片的 Markdown 引用；最后一条 user 消息再追加
    知识库检索上下文与 tail_instruction（如"输出要点"或"据此生成完整报告"）。
    """
    doc_override = doc_override or {}
    system_prompt = system_prompt or _build_system_prompt(REPORT_SYSTEM_PROMPT)
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    user_msgs = [m for m in req.messages if m.role == "user"]
    last_user = user_msgs[-1] if user_msgs else None
    # 同一文档 URL 只注入一次完整内容，后续（多轮追问/重新生成会重复携带）只给短引用，
    # 避免文档文本在上下文中成倍膨胀、撑爆模型上下文
    seen_doc_urls: set[str] = set()
    for m in req.messages:
        content = m.content or ""
        if m.role == "user":
            # 上传文档的文本内容（供大模型先阅读再总结）
            if m.documents:
                doc_parts = []
                for ref in m.documents:
                    name = (ref.name or "").strip() or "参考文档"
                    if ref.url in seen_doc_urls:
                        doc_parts.append(f"【引用文档：{name}（内容已在上方提供）】")
                        continue
                    if ref.url in doc_override:
                        # 大文档：注入分块要点汇总
                        seen_doc_urls.add(ref.url)
                        doc_parts.append(f"【上传文档：{name}（分块要点汇总）】\n{doc_override[ref.url]}")
                        continue
                    text = _read_doc_sidecar(ref.url)
                    if not text:
                        continue
                    seen_doc_urls.add(ref.url)
                    doc_parts.append(f"【上传文档：{name}】\n{text}")
                if doc_parts:
                    content = f"{content}\n\n## 用户上传的参考文档\n\n" + "\n\n".join(doc_parts)
            # 上传图片的 Markdown 引用
            if m.images:
                img_marks = "\n".join(f"![参考图片{i + 1}]({u})" for i, u in enumerate(m.images))
                content = f"{content}\n\n【本次上传的参考图片】\n{img_marks}"
            # 知识库检索上下文 + 尾部指令（仅附加到最后一条 user 消息）
            if m is last_user:
                if context_block:
                    content = f"{content}\n\n## 知识库检索到的相关规范条文\n\n{context_block}"
                if tail_instruction:
                    content = f"{content}\n\n{tail_instruction}"
        messages.append({"role": m.role, "content": content})
    return messages


def _stream_report(req: ReportChatRequest):
    """生成 SSE 流：分块阅读大文档（可选）→ 检索知识库 → 流式编制报告。

    大文档（超过 REPORT_DOC_TEXT_CAP）走 map-reduce：按章节分块逐块提炼要点
    （发 progress 事件），再合并为要点汇总注入最终报告；要点汇总按文档 URL 缓存，
    多轮追问/重新生成不再重复整篇分块。
    """
    db = SessionLocal()
    try:
        # V1.1.1：kb_id 为空表示不选知识库（纯资料编制，不检索）
        kb = db.get(KnowledgeBase, req.kb_id) if req.kb_id is not None else None
        if req.kb_id is not None and kb is None:
            yield _sse({"type": "error", "message": "知识库不存在"})
            return

        llm = _get_active_llm(db)
        if llm is None:
            yield _sse({"type": "error", "message": "未配置启用的大模型，请联系管理员"})
            return
        api_key = decrypt(llm.api_key_enc)
        if not api_key:
            yield _sse({"type": "error", "message": "大模型密钥无法解密"})
            return

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.api_url = llm.api_url
        cfg.api_key = api_key
        cfg.model_name = llm.model_name
        cfg.temperature = llm.temperature
        # V1.1.3：报告输出 token 上限取 max(LLM 配置, REPORT_MAX_TOKENS)，防止完整报告被截断
        cfg.max_tokens = max(llm.max_tokens, settings.REPORT_MAX_TOKENS)
        cfg.timeout = llm.timeout
        provider = llm_factory(llm.provider)

        # === 大文档分块阅读（map 步）：仅对超过单次注入上限的文档启用 ===
        doc_override: dict[str, str] = {}
        for m in req.messages:
            if m.role != "user" or not m.documents:
                continue
            for ref in m.documents:
                if not ref.url or ref.url in doc_override:
                    continue
                text = _read_doc_sidecar(ref.url)
                if not text:
                    continue
                if len(text) <= settings.REPORT_DOC_TEXT_CAP:
                    continue  # 单次注入即可，无需分块
                summary = _get_cached_summary(ref.url)
                if summary is None:
                    name = (ref.name or "").strip() or "参考文档"
                    chunks = _chunk_document(text, settings.REPORT_CHUNK_SIZE)
                    overflow = len(chunks) > settings.REPORT_MAX_CHUNKS
                    if overflow:
                        # 保留首部 + 末尾 5 块，确保靠后章节（如规范第16章）覆盖
                        keep_tail = 5
                        head_count = settings.REPORT_MAX_CHUNKS - keep_tail
                        chunks = chunks[:head_count] + chunks[-keep_tail:]
                    parts: list[str] = []
                    total = len(chunks)
                    for i, chunk in enumerate(chunks, 1):
                        yield _sse({
                            "type": "progress",
                            "message": f"正在阅读文档「{name}」（{i}/{total}）...",
                        })
                        parts.append(_summarize_chunk(chunk, i, total, name, cfg, provider))
                    summary = _combine_chunk_summaries(parts)
                    if overflow:
                        summary += f"\n\n⚠️ 文档过长，仅处理了首部与末尾部分（共 {len(chunks)} 块），中间部分省略。"
                    _write_summary_cache(ref.url, summary)
                doc_override[ref.url] = summary

        # 取最后一条用户消息作为检索主题
        user_msgs = [m for m in req.messages if m.role == "user" and (m.content or "").strip()]
        query = (user_msgs[-1].content if user_msgs else "").strip()

        # 知识库检索（仅在选择知识库时；异常时降级：仍可基于用户输入编制）
        results: list = []
        context_block = ""
        if req.kb_id is not None and query:
            try:
                results = retrieve_with_hybrid(db, req.kb_id, query, top_k=settings.RAG_TOP_K)
                context_block = _build_structured_context(results, query)
            except Exception as e:  # noqa: BLE001
                logger.warning("报告知识库检索失败，降级为纯资料编制: %s", e)

        # V1.1.3：仅注入用户选中的 skill（未选则不注入任何 skill）
        skills_block = get_skills_block(req.skills or [])

        # ============ 阶段一：流式生成报告要点（显示在对话框） ============
        key_msgs = _build_llm_messages(
            req, context_block, doc_override,
            system_prompt=_build_system_prompt(KEY_POINTS_SYSTEM_PROMPT, skills_block),
            tail_instruction="请现在输出本报告的要点（条目化），不要展开为完整报告正文。",
        )
        key_parts: list[str] = []
        try:
            for token in provider.stream_chat(key_msgs, cfg):  # type: ignore[arg-type]
                if token:
                    key_parts.append(token)
                    yield _sse({"type": "token", "content": token})
        except Exception as e:  # noqa: BLE001
            logger.error("报告要点生成失败：%s", e)
            yield _sse({"type": "error", "message": f"报告生成失败：{e}"})
            return
        key_points = "".join(key_parts).strip()
        if not key_points:
            yield _sse({
                "type": "error",
                "message": (
                    "大模型未生成报告要点。可能原因：上下文超出模型可处理范围，或 max_tokens 过小。"
                    "建议：在后台调大 max_tokens；减少所选技能；用环境变量 REPORT_CHUNK_SUMMARY_CAP 调小要点汇总；"
                    "或换用更大上下文的模型。"
                ),
            })
            return

        # ============ 阶段二：静默生成完整报告（供 docx 导出与展开查看） ============
        yield _sse({"type": "progress", "message": "正在生成完整报告..."})
        report_msgs = _build_llm_messages(
            req, context_block, doc_override,
            system_prompt=_build_system_prompt(REPORT_SYSTEM_PROMPT, skills_block),
            tail_instruction=(
                "以下是本报告的要点（供你组织完整报告结构）：\n\n"
                f"{key_points[:4000]}\n\n"
                "请根据以上要点与资料，编写一份完整、详尽、可直接导出为 docx 的报告正文。"
            ),
        )
        report_parts: list[str] = []
        try:
            for token in provider.stream_chat(report_msgs, cfg):  # type: ignore[arg-type]
                if token:
                    report_parts.append(token)
        except Exception as e:  # noqa: BLE001
            logger.error("报告完整版生成失败：%s", e)
            yield _sse({"type": "error", "message": f"报告生成失败：{e}"})
            return
        full_report = "".join(report_parts).strip()
        if not full_report:
            total_chars = sum(len(m.get("content") or "") for m in report_msgs if m.get("role") == "user")
            logger.warning("报告完整版返回空内容，user 消息共 %d 字符", total_chars)
            yield _sse({
                "type": "error",
                "message": (
                    "大模型未生成完整报告。可能原因：要点汇总+上下文仍超出模型可处理范围，或 max_tokens 过小。"
                    "建议：在后台调大 max_tokens；减少所选技能；用环境变量 REPORT_CHUNK_SIZE 调小分块、"
                    "REPORT_CHUNK_SUMMARY_CAP 调小要点汇总；或换用更大上下文的模型。"
                ),
            })
            return
        yield _sse({"type": "report", "content": full_report})
        yield _sse({"type": "done"})
    except Exception as e:  # noqa: BLE001
        logger.exception("报告 SSE 处理异常")
        try:
            yield _sse({"type": "error", "message": f"内部错误：{e}"})
        except Exception:
            pass
    finally:
        db.close()


@router.post("/chat")
def report_chat(req: ReportChatRequest):
    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return StreamingResponse(
        _stream_report(req),
        media_type="text/event-stream",
        headers=headers,
    )


# ---------- docx 导出 ----------

@router.post("/export")
def export_report(req: ReportExportRequest):
    """将最终报告 Markdown 生成 docx 并下载。"""
    title = (req.title or "").strip() or "报告总结"
    out_dir = os.path.join(REPORT_UPLOAD_DIR, "out")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.docx"
    out_path = os.path.join(out_dir, filename)
    generate_docx(req.content, title, out_path)
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"{title}.docx",
    )


# ---------- 报告记录（手动保存） ----------

def _record_to_dict(rec: ReportRecord, include_content: bool = False) -> dict:
    """ReportRecord → 字典。"""
    d = {
        "id": rec.id,
        "title": rec.title,
        "kb_id": rec.kb_id,
        "created_at": rec.created_at.isoformat() if rec.created_at else None,
        "updated_at": rec.updated_at.isoformat() if rec.updated_at else None,
    }
    if include_content:
        d["content"] = rec.content
    return d


@router.get("/records")
def list_records(db: Session = Depends(get_db)):
    """已保存报告列表（按时间倒序）。"""
    rows = db.execute(
        select(ReportRecord).order_by(ReportRecord.created_at.desc())
    ).scalars().all()
    return {"items": [_record_to_dict(r) for r in rows]}


@router.post("/records")
def create_record(req: ReportRecordCreate, db: Session = Depends(get_db)):
    """手动保存一份已生成报告。"""
    rec = ReportRecord(
        title=(req.title or "").strip() or "报告总结",
        content=req.content,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _record_to_dict(rec, include_content=True)


@router.get("/records/{record_id}")
def get_record(record_id: int, db: Session = Depends(get_db)):
    """报告详情（含完整内容）。"""
    rec = db.get(ReportRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告记录不存在")
    return _record_to_dict(rec, include_content=True)


@router.delete("/records/{record_id}")
def delete_record(record_id: int, db: Session = Depends(get_db)):
    """删除报告记录。"""
    rec = db.get(ReportRecord, record_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="报告记录不存在")
    db.delete(rec)
    db.commit()
    return {"ok": True}
