"""公共对话路由：知识库列表、状态、SSE 流式对话、会话与消息查询。"""
import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..models import LLMConfig, KnowledgeBase, Conversation, Message
from ..schemas import (
    ChatRequest, StatusResponse,
    KnowledgeBaseOut, ConversationOut, MessageOut,
)
from ..security import decrypt
from ..llm_provider import factory as llm_factory
from ..rag_service import retrieve, build_prompt

logger = logging.getLogger("app.chat")

router = APIRouter(prefix="/api", tags=["chat"])


def _mineru_available() -> bool:
    from ..config import settings
    if not settings.MINERU_API_URL:
        return False
    try:
        import httpx
        with httpx.Client(timeout=httpx.Timeout(2.0)) as c:
            r = c.get(settings.MINERU_API_URL.rstrip("/") + "/")
            return r.status_code < 500
    except Exception:  # noqa: BLE001
        return False


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
        mineru_available=_mineru_available(),
    ).model_dump()


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _stream_chat(request: Request, kb_id: int, message: str, conversation_id: Optional[int]):
    """生成 SSE 流。使用独立的 DB 会话，避免与请求作用域冲突。"""
    db = SessionLocal()
    assistant_content_parts: list[str] = []
    references_payload: list[dict] = []
    try:
        # 1. 校验知识库
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            yield _sse({"type": "error", "message": "知识库不存在"})
            return

        # 2. 创建或复用对话
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

        # 3. 保存用户消息
        user_msg = Message(conv_id=conv.id, role="user", content=message)
        db.add(user_msg)
        db.commit()

        # 4. 检索 LLM 配置
        llm = _get_active_llm(db)
        if llm is None:
            yield _sse({"type": "error", "message": "未配置启用的 LLM，请联系管理员"})
            return
        api_key = decrypt(llm.api_key_enc)
        if not api_key:
            yield _sse({"type": "error", "message": "LLM 密钥无法解密"})
            return

        # 5. RAG 检索
        try:
            chunks = retrieve(db, kb_id, message, top_k=5)
        except Exception as e:  # noqa: BLE001
            logger.warning("检索失败：%s", e)
            chunks = []

        references_payload = [
            {"doc_name": doc.file_name, "chunk": chunk.content[:200], "score": float(score)}
            for chunk, doc, score in chunks
        ]
        if references_payload:
            yield _sse({"type": "references", "references": references_payload})

        # 6. 构造 prompt 并流式调用 LLM
        messages = build_prompt(message, chunks)
        # 用轻量包装对象把解密后的 api_key 传给 provider，避免污染 ORM 对象
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

        # 7. 保存助手消息
        assistant_text = "".join(assistant_content_parts)
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
def list_conversations(kb_id: int, db: Session = Depends(get_db)):
    rows = db.execute(
        select(Conversation)
        .where(Conversation.kb_id == kb_id)
        .order_by(Conversation.created_at.desc())
    ).scalars().all()
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
