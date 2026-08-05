"""公共对话路由：知识库列表、状态、SSE 流式对话、会话与消息查询。"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import LLMConfig, KnowledgeBase, Conversation, Message, Document
from ..schemas import (
    ChatRequest, StatusResponse,
    KnowledgeBaseOut, ConversationOut, MessageOut,
)
from ..security import decrypt
from ..llm_provider import factory as llm_factory
from ..rag_service import retrieve, retrieve_with_hybrid, build_chat_messages, SYSTEM_PROMPT
from ..config import settings

logger = logging.getLogger("app.chat")

router = APIRouter(prefix="/api", tags=["chat"])

# V1.1.1：不选知识库时使用的通用提示词（不复用 RAG 专用 SYSTEM_PROMPT）
GENERIC_SYSTEM_PROMPT = (
    "你是一名智能问答助手。请直接、准确、简洁地回答用户问题；"
    "当用户提供资料时，优先基于资料内容回答。"
)


def _get_active_llm(db: Session) -> Optional[LLMConfig]:
    return db.execute(
        select(LLMConfig).where(LLMConfig.is_active == True).limit(1)  # noqa: E712
    ).scalar_one_or_none()


@router.get("/knowledge-bases")
def list_knowledge_bases(db: Session = Depends(get_db)):
    rows = db.execute(
        select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    ).scalars().all()
    return {"items": [KnowledgeBaseOut.from_orm(r).model_dump() for r in rows]}


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    llm = _get_active_llm(db)
    return StatusResponse(
        llm_configured=llm is not None,
        active_model=llm.model_name if llm else None,
    ).model_dump()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_image_fallback_section(results: list[dict], assistant_text: str) -> str:
    """汇总检索结果中尚未出现在回答里的图片，构造 Markdown「相关图片」小节。

    V1.0.9：若大模型未在回答中内嵌检索到的图片，则追加该小节，保证查询结果
    与图片相关时对话界面必然展示图片。URL 用子串包含判断，容忍大模型断行/改写
    链接；最多追加 6 张，防止污染回答。
    """
    seen_urls: set[str] = set()
    picked: list[str] = []
    for r in results:
        images = r.get("images") if isinstance(r, dict) else None
        for alt, url in images or []:
            if url in seen_urls:
                continue
            seen_urls.add(url)
            if url in assistant_text:  # LLM 已内嵌该图，跳过
                continue
            picked.append(f"![{alt}]({url})")
            if len(picked) >= 6:
                break
        if len(picked) >= 6:
            break
    if not picked:
        return ""
    return "\n\n### 相关图片\n\n" + "\n".join(picked)


def _stream_chat(request: Request, kb_id: Optional[int], message: str, conversation_id: Optional[int]):
    """生成 SSE 流。使用独立的 DB 会话，避免与请求作用域冲突。"""
    db = SessionLocal()
    assistant_content_parts: list[str] = []
    references_payload: list[dict] = []
    try:
        # V1.1.1：kb_id 为空表示不选知识库（纯问答，不检索）
        kb = db.get(KnowledgeBase, kb_id) if kb_id is not None else None
        if kb_id is not None and kb is None:
            yield _sse({"type": "error", "message": "知识库不存在"})
            return

        conv: Optional[Conversation]
        if conversation_id:
            conv = db.get(Conversation, conversation_id)
            if conv is None or conv.kb_id != kb_id:
                yield _sse({"type": "error", "message": "会话不存在或不属于该知识库"})
                return
        else:
            conv = Conversation(kb_id=kb_id, title=(message[:30] + ("..." if len(message) > 30 else "")))
            db.add(conv)
            db.commit()
            db.refresh(conv)

        yield _sse({"type": "start", "conversation_id": conv.id})

        # V1.2.3：加载会话历史用于多轮连续对话。
        # 当前 user 消息尚未入库，天然排除本轮；仅取最近 200 条控制内存/时延，
        # 过长历史由 build_chat_messages 按 context_window 预算裁剪。
        history_rows = db.execute(
            select(Message)
            .where(Message.conv_id == conv.id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(200)
        ).scalars().all()
        history_rows.reverse()
        history: list[dict] = [
            {"role": m.role, "content": m.content}
            for m in history_rows
            if m.role in ("user", "assistant")
        ]

        user_msg = Message(conv_id=conv.id, role="user", content=message)
        db.add(user_msg)
        db.commit()

        llm = _get_active_llm(db)
        if llm is None:
            yield _sse({"type": "error", "message": "未配置启用的 LLM，请联系管理员"})
            return
        api_key = decrypt(llm.api_key_enc)
        if not api_key:
            yield _sse({"type": "error", "message": "LLM 密钥无法解密"})
            return

        # === RAG 检索（向量语义检索，retrieve_with_hybrid 内部异常时降级 BM25） ===
        # V1.1.1：不选知识库（kb_id 为空）时跳过检索，仅凭大模型回答
        results: list = []
        if kb_id is not None:
            bm25_query = message
            try:
                # 查询改写（可选，用 LLM 将口语化查询扩展为专业术语）
                if settings.ENABLE_QUERY_REWRITE:
                    from ..hybrid_search import query_rewrite
                    try:
                        rewritten = query_rewrite(
                            message, llm.api_url, api_key, llm.model_name
                        )
                        if rewritten and rewritten != message:
                            # V1.2.3：改写词只喂 BM25 关键词路径，向量仍用原始 message，
                            # 避免改写稀释"大变形分级标准表"这类精确表名的向量信号
                            bm25_query = f"{message} {rewritten}"
                            logger.info("查询改写: %r → %r", message, bm25_query[:80])
                    except Exception as e:
                        logger.debug("查询改写跳过: %s", e)

                # V1.2.3 默认 hybrid：向量用原始 message（retrieve_with_hybrid 内部分离）
                results = retrieve_with_hybrid(
                    db, kb_id, message,
                    top_k=settings.RAG_TOP_K, bm25_query=bm25_query,
                )
            except Exception as e:  # noqa: BLE001
                # 检索异常：直接报错让用户感知问题
                logger.warning("向量检索失败: %s", e)
                yield _sse({
                    "type": "error",
                    "message": f"检索失败：{e}",
                })
                return

        # 构建引用信息
        references_payload = [
            {
                "doc_name": r.get("file_name", "未知文档"),
                "score": float(r.get("score", 0)),
                "bm25_score": float(r.get("bm25_score", 0)),
                "vector_score": float(r.get("vector_score", 0)),
            }
            for r in results
        ]
        if references_payload:
            yield _sse({"type": "references", "references": references_payload})

        # V1.2.3：构造 prompt 并流式调用 LLM。
        # 有知识库走 RAG 提示词（历史 + 当前检索上下文）；无知识库走通用提示词（历史纯透传）。
        # 两者都按 llm.context_window 预算裁剪历史，避免撑爆模型上下文。
        if kb_id is not None:
            messages = build_chat_messages(
                message, results, history,
                system_prompt=SYSTEM_PROMPT,
                context_window=llm.context_window or 0,
                max_tokens=llm.max_tokens or 2048,
                include_context=True,
            )
        else:
            messages = build_chat_messages(
                message, [], history,
                system_prompt=GENERIC_SYSTEM_PROMPT,
                context_window=llm.context_window or 0,
                max_tokens=llm.max_tokens or 2048,
                include_context=False,
            )

        class _Cfg:
            pass
        cfg = _Cfg()
        cfg.api_url = llm.api_url
        cfg.api_key = api_key
        cfg.model_name = llm.model_name
        cfg.temperature = llm.temperature
        cfg.max_tokens = llm.max_tokens
        cfg.timeout = llm.timeout

        provider = llm_factory(llm.provider)
        try:
            for token in provider.stream_chat(messages, cfg):  # type: ignore[arg-type]
                if token:
                    assistant_content_parts.append(token)
                    yield _sse({"type": "token", "content": token})
        except Exception as e:  # noqa: BLE001
            logger.error("LLM 流式调用失败：%s", e)
            yield _sse({"type": "error", "message": f"LLM 调用失败：{e}"})
            return

        assistant_text = "".join(assistant_content_parts)

        # V1.0.9：检索结果相关图片兜底追加。若回答中未内嵌检索到的图片，则追加
        # 「相关图片」小节，保证查询结果与图片相关时对话界面必然展示图片。
        image_section = _build_image_fallback_section(results, assistant_text)
        if image_section:
            assistant_text += image_section
            yield _sse({"type": "token", "content": image_section})

        assistant_msg = Message(
            conv_id=conv.id,
            role="assistant",
            content=assistant_text,
            references_=references_payload if references_payload else None,
        )
        db.add(assistant_msg)
        db.commit()

        yield _sse({"type": "done", "conversation_id": conv.id})
    except Exception as e:  # noqa: BLE001
        logger.exception("SSE 处理异常")
        try:
            yield _sse({"type": "error", "message": f"内部错误：{e}"})
        except Exception:
            pass
    finally:
        db.close()


@router.post("/chat")
def chat(req: ChatRequest, request: Request):
    headers = {"X-Accel-Buffering": "no", "Cache-Control": "no-cache"}
    return StreamingResponse(
        _stream_chat(request, req.kb_id, req.message, req.conversation_id),
        media_type="text/event-stream",
        headers=headers,
    )


@router.get("/conversations")
def list_conversations(kb_id: Optional[int] = None, db: Session = Depends(get_db)):
    """会话列表。kb_id 为空时返回「不选知识库」的会话。"""
    stmt = select(Conversation)
    if kb_id is None:
        stmt = stmt.where(Conversation.kb_id.is_(None))
    else:
        stmt = stmt.where(Conversation.kb_id == kb_id)
    stmt = stmt.order_by(Conversation.updated_at.desc())
    rows = db.execute(stmt).scalars().all()
    return {"items": [ConversationOut.from_orm(r).model_dump() for r in rows]}


@router.get("/conversations/{conv_id}/messages")
def list_messages(conv_id: int, db: Session = Depends(get_db)):
    conv = db.get(Conversation, conv_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    rows = db.execute(
        select(Message).where(Message.conv_id == conv_id).order_by(Message.created_at.asc())
    ).scalars().all()
    return {"items": [MessageOut.from_orm(r).model_dump() for r in rows]}
