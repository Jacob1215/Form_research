"""RAG 编排：分块、检索、Prompt 构造。"""
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import DocChunk, Document
from .embedding_service import embedding_service

logger = logging.getLogger("app.rag")


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """按字符切分文本，CJK 友好；尽量在换行或句末断开。"""
    if not text:
        return []
    text = text.strip()
    if len(text) <= chunk_size:
        return [text] if text else []

    chunks: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        if end < n:
            # 在末尾 20% 范围内寻找最佳断点：换行 > 句号 > 空白
            search_window = text[max(start, end - chunk_size // 5): end]
            cut_offset = -1
            cut_char = None
            for ch in ("\n", "。", "！", "？", ". ", "! ", "? ", "；", "; ", " "):
                idx = search_window.rfind(ch)
                if idx > cut_offset:
                    cut_offset = idx
                    cut_char = ch
            if cut_offset >= 0 and cut_char is not None:
                end = max(start, end - chunk_size // 5) + cut_offset + len(cut_char)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= n:
            break
        start = end - overlap
        if start <= 0:
            start = end  # 避免死循环
    return chunks


def retrieve(db: Session, kb_id: int, query: str, top_k: int = 5) -> list[tuple[DocChunk, Document, float]]:
    """在指定知识库内对查询做向量检索，返回 (chunk, document, score) 列表。"""
    query_vec = embedding_service.embed_one(query)
    try:
        stmt = (
            select(DocChunk, Document)
            .join(Document, Document.id == DocChunk.doc_id)
            .where(DocChunk.kb_id == kb_id)
            .order_by(DocChunk.embedding.cosine_distance(query_vec))
            .limit(top_k)
        )
        rows = db.execute(stmt).all()
    except Exception as e:  # noqa: BLE001
        logger.warning("向量检索失败，降级关键词匹配：%s", e)
        # 降级：简单按字符匹配
        stmt = (
            select(DocChunk, Document)
            .join(Document, Document.id == DocChunk.doc_id)
            .where(DocChunk.kb_id == kb_id)
            .limit(top_k)
        )
        rows = db.execute(stmt).all()
        scored = []
        for chunk, doc in rows:
            score = 1.0 if query and query in chunk.content else 0.0
            scored.append((chunk, doc, score))
        return scored
    result = []
    for chunk, doc in rows:
        # pgvector cosine_distance 返回的是距离，转换为相似度分数
        score = 1.0  # 默认占位
        result.append((chunk, doc, score))
    return result


def build_prompt(query: str, chunks: list[tuple[DocChunk, Document, float]]) -> list[dict]:
    """构造 RAG 提示词：system + 上下文 + user 问题。"""
    context_parts: list[str] = []
    for chunk, doc, _ in chunks:
        context_parts.append(f"【文档《{doc.file_name}》】\n{chunk.content}")
    context_block = "\n\n".join(context_parts) if context_parts else "（未检索到相关上下文）"

    system_prompt = (
        "你是一名工程规范领域的智能问答助手。请基于以下检索到的知识库上下文回答用户问题。"
        "要求：\n"
        "1. 使用中文回答；\n"
        "2. 在回答中恰当位置标注引用来源，格式如 参考《文档名》；\n"
        "3. 若上下文无相关内容，请基于自身知识回答并明确说明“未在选定知识库中找到直接依据”；\n"
        "4. 回答末尾标注参考来源；\n"
        "5. 不要编造未在上下文中出现的规范编号或条款。\n\n"
        f"【知识库上下文】\n{context_block}"
    )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ]
