"""PPT 制作路由（V1.2.1+）：文本 + 附件文档（docx/txt/md/pdf） + 知识库 → 两阶段对话（SSE）→ pptx 导出下载。

以「报告总结」功能为模板，复用其纯函数（_sse / _get_active_llm / _build_system_prompt /
_build_llm_messages / 大文档分块）与 skill 库 / 知识库检索 / 大模型配置。

版本演进：
- V1.2.1：PPT 制作功能上线。两阶段生成 — 先流式输出大纲（显示在对话框），
  再静默生成完整演示文稿（供导出）；提供 skill 选择（scope: ppt）与历史记录。
- V1.2.2：支持附件文档上传（docx/txt/md/pdf）。上传复用报告端点 /api/report/upload
  （文档 URL/侧车共用 /api/report/uploads/ 命名空间，_build_llm_messages 无需改动即注入）；
  大文档走 map-reduce 分块阅读，避免上下文溢出。
"""
import os
import json
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db, SessionLocal
from ..models import KnowledgeBase, PptRecord
from ..schemas import PptChatRequest, PptExportRequest, PptRecordCreate
from ..security import decrypt
from ..llm_provider import factory as llm_factory
from ..rag_service import retrieve_with_hybrid, _build_structured_context
from ..pptx_service import generate_pptx
from ..skill_library import get_skills_block, list_skills
# 复用报告路由的纯函数（SSE 格式 / 启用大模型 / 提示词组装 / 消息组装 / 大文档分块）
from .report import (
    _sse, _get_active_llm, _build_system_prompt, _build_llm_messages,
    _read_doc_sidecar, _chunk_document, _summarize_chunk,
    _combine_chunk_summaries, _get_cached_summary, _write_summary_cache,
)

logger = logging.getLogger("app.ppt")

router = APIRouter(prefix="/api/ppt", tags=["ppt"])

# PPT 导出输出目录：{UPLOAD_DIR}/ppt/out
PPT_UPLOAD_DIR = os.path.join(settings.UPLOAD_DIR, "ppt")

# PPT skill 指令块标题（区别于报告的标题）
PPT_SKILLS_HEADER = "## PPT 编制技能规则（必须遵循）\n\n"

# V1.2.1：阶段一 — 大纲生成提示词（流式显示在对话框）
PPT_OUTLINE_SYSTEM_PROMPT = """你是一名专业的 PPT 制作助手。请根据用户提供的文字内容与知识库检索到的规范内容，先输出一份演示文稿大纲（分页提纲）。

## 输出要求
1. 按页列出：每页一行，格式为「第N页：<页面标题>」，标题凝练、不超过 15 字。
2. 每页下用 `- ` 列出 2-4 个要点，覆盖该页要讲的核心内容。
3. 覆盖：概述 → 主要要点（分页）→ 规范依据（如有）→ 结论与建议。
4. 引用规范时标注出处（《规范名称》+ 条款号）。
5. 不得编造资料中不存在的内容；资料不足时说明缺什么。
"""

# V1.2.1：阶段二 — 完整演示文稿生成提示词（静默收集，供 pptx 导出）
PPT_SYSTEM_PROMPT = """你是一名专业的 PPT 制作助手，负责根据用户提供的文字内容与知识库检索到的规范内容，制作结构清晰、要点突出、可直接导出为 pptx 的演示文稿。

## 幻灯片 Markdown 约定（必须严格遵守）
1. 每一页幻灯片以 `##` 开头（该行即为幻灯片标题），标题凝练、不超过 15 字。
2. `###` 表示当前页内的小节副标题（按需使用）。
3. 每页正文用条目化要点：`- ` 或 `1. `，每页 4-6 条；严禁把整段文字直接粘贴进幻灯片。
4. 页与页之间用独占一行的 `---` 分隔（必须单独一行，前后留空行）。
5. 如需表格，用 Markdown 表格语法（`| a | b |`）；资料中有图片地址时用 `![说明](url)`，仅可使用提供的地址，禁止编造。
6. 引用知识库或资料中的规范条款时标注出处：《规范名称》+ 条款号。
7. 不得编造资料中不存在的内容；资料不足时在相应页内说明。
8. 只输出符合上述约定的 Markdown 文本，不要输出任何解释性文字或代码块包裹。

## 结构参考
封面（标题）→ 概述/背景 → 主要要点（分页展开）→ 规范依据（如有）→ 结论与建议。可按内容灵活调整，但每页一主题。
"""


def _record_to_dict(rec: PptRecord, include_content: bool = False) -> dict:
    """PptRecord → 字典。"""
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


@router.get("/skills")
def list_ppt_skills():
    """V1.2.1：PPT skill 清单（scope: ppt），供输入框 / 菜单选择。"""
    return {"items": list_skills("ppt")}


def _stream_ppt(req: PptChatRequest):
    """生成 SSE 流：检索知识库（可选）→ 阶段一大纲流式输出 → 阶段二完整演示文稿静默收集。

    事件：token（大纲）、progress（阶段二进行中）、ppt（完整演示文稿）、done、error。
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
        # 输出 token 上限取 max(LLM 配置, PPT_MAX_TOKENS)，防止完整演示文稿被截断
        cfg.max_tokens = max(llm.max_tokens, settings.PPT_MAX_TOKENS)
        cfg.timeout = llm.timeout
        provider = llm_factory(llm.provider)

        # 取最后一条用户消息作为检索主题与编制依据
        user_msgs = [m for m in req.messages if m.role == "user" and (m.content or "").strip()]
        query = (user_msgs[-1].content if user_msgs else "").strip()

        # 知识库检索（仅在选择知识库时；异常时降级：仍可基于用户文本编制）
        context_block = ""
        if req.kb_id is not None and query:
            try:
                results = retrieve_with_hybrid(db, req.kb_id, query, top_k=settings.RAG_TOP_K)
                context_block = _build_structured_context(results, query)
            except Exception as e:  # noqa: BLE001
                logger.warning("PPT 知识库检索失败，降级为纯文本编制: %s", e)

        # V1.2.2：大文档分块阅读（map 步）— 仅对超过单次注入上限的文档启用。
        # 附件上传复用报告端点 → 文档 URL/侧车位于 /api/report/uploads/ 命名空间，
        # _read_doc_sidecar / _build_llm_messages 可直接复用，无需改动。
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

        # 仅注入用户选中的 PPT skill（未选则不注入）
        skills_block = get_skills_block(
            req.skills or [], header=PPT_SKILLS_HEADER,
            max_chars=settings.PPT_SKILLS_MAX_CHARS,
        )

        # ============ 阶段一：流式生成大纲（显示在对话框） ============
        outline_msgs = _build_llm_messages(
            req, context_block, doc_override,
            system_prompt=_build_system_prompt(PPT_OUTLINE_SYSTEM_PROMPT, skills_block),
            tail_instruction="请现在输出本演示文稿的大纲（分页提纲），不要展开完整正文。",
        )
        outline_parts: list[str] = []
        try:
            for token in provider.stream_chat(outline_msgs, cfg):  # type: ignore[arg-type]
                if token:
                    outline_parts.append(token)
                    yield _sse({"type": "token", "content": token})
        except Exception as e:  # noqa: BLE001
            logger.error("PPT 大纲生成失败：%s", e)
            yield _sse({"type": "error", "message": f"PPT 生成失败：{e}"})
            return
        outline = "".join(outline_parts).strip()
        if not outline:
            yield _sse({
                "type": "error",
                "message": (
                    "大模型未生成 PPT 大纲。可能原因：上下文超出模型可处理范围，或 max_tokens 过小。"
                    "建议：在后台调大 max_tokens；减少所选技能；或换用更大上下文的模型。"
                ),
            })
            return

        # ============ 阶段二：静默生成完整演示文稿（供 pptx 导出与展开查看） ============
        yield _sse({"type": "progress", "message": "正在生成完整演示文稿..."})
        deck_msgs = _build_llm_messages(
            req, context_block, doc_override,
            system_prompt=_build_system_prompt(PPT_SYSTEM_PROMPT, skills_block),
            tail_instruction=(
                "以下是本演示文稿的大纲（供你组织每页结构）：\n\n"
                f"{outline[:4000]}\n\n"
                "请根据以上大纲与资料，编写完整、可直接导出为 pptx 的演示文稿，严格遵守「幻灯片 Markdown 约定」。"
            ),
        )
        deck_parts: list[str] = []
        try:
            for token in provider.stream_chat(deck_msgs, cfg):  # type: ignore[arg-type]
                if token:
                    deck_parts.append(token)
        except Exception as e:  # noqa: BLE001
            logger.error("PPT 完整版生成失败：%s", e)
            yield _sse({"type": "error", "message": f"PPT 生成失败：{e}"})
            return
        full_ppt = "".join(deck_parts).strip()
        if not full_ppt:
            total_chars = sum(len(m.get("content") or "") for m in deck_msgs if m.get("role") == "user")
            logger.warning("PPT 完整版返回空内容，user 消息共 %d 字符", total_chars)
            yield _sse({
                "type": "error",
                "message": (
                    "大模型未生成完整 PPT。可能原因：大纲+上下文仍超出模型可处理范围，或 max_tokens 过小。"
                    "建议：在后台调大 max_tokens；减少所选技能；或换用更大上下文的模型。"
                ),
            })
            return
        yield _sse({"type": "ppt", "content": full_ppt})
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


# ---------- pptx 导出 ----------

@router.post("/export")
def export_ppt(req: PptExportRequest):
    """将最终演示文稿 Markdown 生成 16:9 的 pptx 并下载。"""
    title = (req.title or "").strip() or "PPT演示"
    out_dir = os.path.join(PPT_UPLOAD_DIR, "out")
    os.makedirs(out_dir, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.pptx"
    out_path = os.path.join(out_dir, filename)
    generate_pptx(req.content, title, out_path)
    return FileResponse(
        out_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{title}.pptx",
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
    """手动保存一份已生成的演示文稿。"""
    rec = PptRecord(
        title=(req.title or "").strip() or "PPT",
        content=req.content,
        question=(req.question or "").strip() or None,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return _record_to_dict(rec, include_content=True)


@router.get("/records/{record_id}")
def get_ppt_record(record_id: int, db: Session = Depends(get_db)):
    """PPT 详情（含完整内容）。"""
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
