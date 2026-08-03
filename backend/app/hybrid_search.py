"""RAG 检索引擎（V1.0.7 方案 A：纯向量语义检索）。

检索流程：
1. 查询改写（可选，用 LLM 将口语化查询扩展为专业术语）
2. 向量语义检索（基于 pgvector 余弦相似度）
3. 截断、返回 top_k

设计原则：
- 默认使用纯向量检索（RETRIEVE_MODE=vector_only），不再自动降级 BM25
- 向量检索不可用时抛异常，提示用户重新索引，而非静默降级
- BM25 相关函数保留作为回滚备选（切换 RETRIEVE_MODE=hybrid 可恢复混合检索）
- BM25 打分复用 text_utils.bm25_rank / substring_rank，与 rag_service.retrieve 共享同一核心
- 规范编号精确匹配在 BM25 模式中给予高权重
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Document, DocumentChunk
from .config import settings
from .embedding_service import embedding_service
from .vector_store import VectorStore
from .text_utils import (
    bm25_rank,
    substring_rank,
)

logger = logging.getLogger("app.hybrid_search")


def _bm25_search_chunks(
    db: Session,
    kb_id: int,
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
) -> list[tuple[int, float, str, str | None]]:
    """BM25 关键词检索分块。

    返回: list[(chunk_id, bm25_score, chunk_content, doc_file_name)]
    打分复用 text_utils.bm25_rank，行为与重构前一致。
    """
    # 获取知识库中所有分块
    stmt = (
        select(DocumentChunk, Document.file_name)
        .join(Document, DocumentChunk.doc_id == Document.id)
        .where(
            DocumentChunk.kb_id == kb_id,
            DocumentChunk.content.isnot(None),
        )
    )
    rows = db.execute(stmt).all()
    if not rows:
        return []

    items = [(chunk.id, chunk.content or "", file_name) for chunk, file_name in rows]
    ranked = bm25_rank(items, query_keywords, spec_codes, top_k=top_k)
    return [(chunk_id, score, text, file_name) for chunk_id, score, text, file_name in ranked]


def _bm25_search_docs(
    db: Session,
    kb_id: int,
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
) -> list[tuple[Document, float]]:
    """BM25 关键词检索文档（降级方案，当没有分块时使用）。

    返回: list[(Document, score)]
    打分复用 text_utils.bm25_rank / substring_rank，行为与重构前一致。
    """
    stmt = select(Document).where(
        Document.kb_id == kb_id,
        Document.content_text.isnot(None),
    )
    docs = db.execute(stmt).scalars().all()
    if not docs:
        return []

    items = [(doc, doc.content_text or "", doc.file_name) for doc in docs]
    ranked = bm25_rank(items, query_keywords, spec_codes, top_k=top_k)
    scored = [(doc, score) for doc, score, _text, _fname in ranked]

    # 降级子串匹配
    if not scored:
        logger.warning("BM25 文档检索无结果，降级子串匹配")
        scored = substring_rank(items, query_keywords, spec_codes, top_k=top_k)

    return scored[:top_k]


def _vector_search(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 10,
) -> list[tuple[int, int, str, float, str | None]]:
    """向量语义检索。

    返回: list[(doc_id, chunk_index, content, similarity, file_name)]
    """
    vs = VectorStore(db)

    if vs.chunk_count(kb_id) == 0:
        logger.info("知识库 %d 无向量化分块，跳过向量检索", kb_id)
        return []

    try:
        query_embedding = embedding_service.embed_one(query)
    except Exception as e:
        logger.warning("查询向量化失败，跳过向量检索: %s", e)
        return []

    if query_embedding is None:
        logger.info("向量化服务不可用，跳过向量检索")
        return []

    return vs.vector_search(kb_id, query_embedding, top_k=top_k)


def _rrf_fusion(
    bm25_results: list[tuple[int, float, str, str | None]],
    vector_results: list[tuple[int, int, str, float, str | None]],
    k: int = 60,
    top_k: int = 10,
) -> list[dict]:
    """RRF (Reciprocal Rank Fusion) 融合 BM25 和向量检索结果。

    返回: list[dict]，每个字典包含 doc_id, chunk_index, content, score, file_name
    """
    bm25_weight = settings.HYBRID_BM25_WEIGHT
    vector_weight = settings.HYBRID_VECTOR_WEIGHT

    # key: (doc_id, chunk_index, content)
    fusion: dict[tuple[int, int, str], dict] = {}

    # BM25 贡献
    for rank, (chunk_id, bm25_score, content, file_name) in enumerate(bm25_results):
        rrf_score = bm25_weight / (k + rank + 1)
        key = (chunk_id, -1, content)  # chunk_id from BM25, chunk_index=-1
        if key not in fusion:
            fusion[key] = {
                "chunk_id": chunk_id,
                "chunk_index": -1,
                "content": content,
                "score": rrf_score,
                "file_name": file_name,
                "bm25_score": bm25_score,
                "vector_score": 0.0,
            }
        else:
            fusion[key]["score"] += rrf_score
            fusion[key]["bm25_score"] = bm25_score

    # 向量检索贡献
    for rank, (doc_id, chunk_index, content, vec_score, file_name) in enumerate(vector_results):
        rrf_score = vector_weight / (k + rank + 1)
        key = (doc_id, chunk_index, content)
        if key not in fusion:
            fusion[key] = {
                "chunk_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": rrf_score,
                "file_name": file_name,
                "bm25_score": 0.0,
                "vector_score": vec_score,
            }
        else:
            fusion[key]["score"] += rrf_score
            fusion[key]["vector_score"] = vec_score

    # 按融合分数排序
    merged = sorted(fusion.values(), key=lambda x: x["score"], reverse=True)
    return merged[:top_k]


def hybrid_retrieve(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 5,
) -> list[dict]:
    """向量检索主入口（V1.0.7 方案 A）。

    流程：
    1. 检查知识库是否有向量化分块
    2. 无向量时抛异常，提示用户重新索引（不降级 BM25）
    3. 执行向量语义检索，按余弦相似度排序
    4. 截断、返回 top_k

    返回: list[dict]，每个字典包含:
        - doc_id / chunk_index / content / score / file_name
        - bm25_score（纯向量模式下固定为 0.0）
        - vector_score（余弦相似度）
    """
    query = query.strip()
    if not query:
        return []

    # V1.0.7 方案 A：纯向量语义检索（不再调用 BM25）
    vs = VectorStore(db)
    has_vectors = vs.chunk_count(kb_id) > 0

    if not has_vectors:
        # 无向量化分块 = 向量检索不可用。方案 A 下不静默降级，抛异常让上游提示用户。
        logger.warning("知识库 %d 无向量化分块，纯向量检索不可用", kb_id)
        raise RuntimeError(
            "当前知识库尚未生成向量索引，请先上传文档或点击「重新索引」。"
            "（V1.0.7 纯向量模式：不再降级 BM25）"
        )

    vector_results = _vector_search(db, kb_id, query, top_k=top_k)
    if not vector_results:
        logger.info("向量检索返回空: query=%r kb_id=%d", query[:50], kb_id)
        return []

    # 向量检索结果直接作为最终结果（不再 RRF 融合）
    results = [
        {
            "doc_id": doc_id,
            "chunk_index": chunk_index,
            "content": content,
            "score": float(similarity),
            "file_name": file_name,
            "bm25_score": 0.0,
            "vector_score": float(similarity),
        }
        for doc_id, chunk_index, content, similarity, file_name in vector_results[:top_k]
    ]
    logger.info(
        "纯向量检索完成: query=%r kb_id=%d vec=%d returned=%d",
        query[:50], kb_id, len(vector_results), len(results),
    )
    return results


def query_rewrite(query: str, llm_api_url: str, llm_api_key: str, model_name: str) -> str:
    """使用 LLM 将用户口语化查询改写为专业术语关键词。

    用于增强 BM25 关键词匹配和向量语义检索。

    Args:
        query: 用户原始查询
        llm_api_url: LLM API 地址
        llm_api_key: API 密钥
        model_name: 模型名称

    Returns:
        扩展后的关键词字符串（空格分隔），失败时返回原查询
    """
    if not query.strip():
        return query

    prompt = f"""你是一个工程规范领域的查询改写助手。将用户的自然语言问题改写为规范检索用的关键词，补充同义词、专业术语、规范编号，用空格分隔。
只输出关键词，不要任何解释。

示例：
输入：焊缝要查多少
输出：焊缝探伤比例 焊缝检测要求 无损检测 超声波探伤 射线探伤 焊缝质量

请改写以下查询：
输入：{query}
输出："""

    try:
        import httpx
        url = llm_api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {llm_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 100,
            "temperature": 0.1,
            "stream": False,
        }
        with httpx.Client(timeout=httpx.Timeout(15.0)) as client:
            resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.warning("查询改写 LLM 返回 %s", resp.status_code)
            return query
        data = resp.json()
        rewritten = data["choices"][0]["message"]["content"].strip()
        logger.info("查询改写: %r → %r", query, rewritten)
        return rewritten
    except Exception as e:
        logger.warning("查询改写失败: %s", e)
        return query
