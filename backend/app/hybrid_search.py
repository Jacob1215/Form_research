"""RAG 检索引擎（V1.2.3：默认 hybrid 混合检索）。

检索流程：
1. 查询改写（可选，用 LLM 将口语化查询扩展为专业术语，改写词只喂 BM25 关键词路径）
2. 向量语义检索（基于 pgvector 余弦相似度，bge 系查询侧自动加官方指令前缀）
3. BM25 分块 + 向量 RRF 融合（RETRIEVE_MODE=hybrid 默认）→ 截断、返回 top_k

设计原则：
- 默认使用混合检索（BM25 分块 + 向量 RRF），精确表名/条款号类查询更稳
- vector_only（纯向量）保留为可选项：无向量时抛异常提示重新索引，不静默降级
- hybrid 模式无向量时退化为 BM25 分块检索（不抛异常）
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
    strip_markdown,
    substring_rank,
)

logger = logging.getLogger("app.hybrid_search")


def _merge_bm25_pool(
    items: list[tuple[int, str, str]],
    ranked: list[tuple[int, float, str, str]],
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int,
    raw_query: str | None,
) -> list[tuple[int, float, str, str]]:
    """并行合并 BM25 + 分词级子串补漏 + 整句字面命中提权，构成 BM25 候选池。

    V1.2.5：分词级子串只做**召回补漏**（把 bm25 未命中的字面块加入池子），
    不再覆盖/抬高已由 bm25 打分的块，避免关键字重复块以纯子串频次刷榜；
    只有「原始整句」连续出现才强提权（对齐预览界面的 RegExp 子串高亮行为）。
    最终按 score 降序取 top_k。

    Args:
        items: [(chunk_id, content, file_name), ...]
        ranked: bm25_rank 结果 [(chunk_id, score, text, file_name), ...]
        query_keywords: 分词关键词
        spec_codes: 规范编号
        top_k: 返回条数
        raw_query: 原始查询整句（预览搜索框同一输入），用于字面命中扫描

    Returns:
        [(chunk_id, score, text, file_name)]，按 score 降序，仅保留 score > 0。
        注意：字面命中块的 score 可能为子串/整句得分（bm25_score 语义放宽，
        仅供展示与调参）。
    """
    item_map = {iid: (text, fname) for iid, text, fname in items}
    pool: dict[int, float] = {iid: score for iid, score, _t, _f in ranked}

    if settings.ENABLE_SUBSTRING_BOOST:
        # ① 分词级子串：仅作为召回补漏——把 bm25 漏掉的字面命中块加入候选池
        #    （保留 V1.2.4 对"精确表名块进不了池"的修复），但不再覆盖/抬高
        #    已由 bm25 打分的块，避免关键字重复块以纯子串频次刷榜。
        for iid, s in substring_rank(items, query_keywords, spec_codes, top_k=top_k):
            if iid not in pool:
                pool[iid] = s

        # ② 整句字面命中：全块扫描原始整句连续出现即强提权（对齐预览）。
        # 独立于 top_k 扫描全部块，不受 substring_rank 截断影响。
        phrase = strip_markdown((raw_query or "").strip().lower())
        if phrase:
            for iid, text, _f in items:
                hits = strip_markdown((text or "").lower()).count(phrase)
                if hits > 0:
                    pool[iid] = max(
                        pool.get(iid, 0.0),
                        settings.SUBSTRING_PHRASE_WEIGHT * hits,
                    )

    merged = sorted(
        ((iid, s) for iid, s in pool.items() if s > 0),
        key=lambda x: x[1],
        reverse=True,
    )[:top_k]
    return [
        (iid, s, item_map[iid][0], item_map[iid][1])
        for iid, s in merged
        if iid in item_map
    ]


def _bm25_search_chunks(
    db: Session,
    kb_id: int,
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
    raw_query: str | None = None,
    doc_ids: list[int] | None = None,
) -> list[tuple[int, int, int, float, str, str | None, str | None]]:
    """BM25 关键词检索分块。

    V1.2.4：候选池经 _merge_bm25_pool 合并 BM25 + 子串 + 整句字面命中，
    使精确表名/章节名/条款号这类字面命中块能可靠进入混合检索的 RRF 融合。
    V1.2.5：doc_ids 非空时仅检索指定文档。

    返回: list[(chunk_id, doc_id, chunk_index, bm25_score, chunk_content, doc_file_name, section_title)]
    打分复用 text_utils.bm25_rank / substring_rank，行为与重构前一致。
    """
    # 获取知识库中所有分块（V1.2.5：doc_ids 非空时只取指定文档）
    stmt = (
        select(DocumentChunk, Document.file_name)
        .join(Document, DocumentChunk.doc_id == Document.id)
        .where(
            DocumentChunk.kb_id == kb_id,
            DocumentChunk.content.isnot(None),
        )
    )
    if doc_ids:
        stmt = stmt.where(DocumentChunk.doc_id.in_(doc_ids))
    rows = db.execute(stmt).all()
    if not rows:
        return []

    items = [(chunk.id, chunk.content or "", file_name) for chunk, file_name in rows]
    ranked = _merge_bm25_pool(
        items,
        bm25_rank(items, query_keywords, spec_codes, top_k=top_k),
        query_keywords, spec_codes, top_k, raw_query,
    )
    if not ranked:
        return []

    meta = {
        chunk.id: (chunk.doc_id, chunk.chunk_index, chunk.section_title)
        for chunk, _ in rows
    }
    return [
        (
            chunk_id, meta[chunk_id][0], meta[chunk_id][1],
            score, text, file_name, meta[chunk_id][2],
        )
        for chunk_id, score, text, file_name in ranked
        if chunk_id in meta
    ]


def _bm25_search_docs(
    db: Session,
    kb_id: int,
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
    doc_ids: list[int] | None = None,
) -> list[tuple[Document, float]]:
    """BM25 关键词检索文档（降级方案，当没有分块时使用）。

    返回: list[(Document, score)]
    打分复用 text_utils.bm25_rank / substring_rank，行为与重构前一致。
    V1.2.5：doc_ids 非空时仅检索指定文档。
    """
    stmt = select(Document).where(
        Document.kb_id == kb_id,
        Document.content_text.isnot(None),
    )
    if doc_ids:
        stmt = stmt.where(Document.id.in_(doc_ids))
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


def _should_apply_query_prompt() -> bool:
    """判断当前 embedding 模型是否属于需要查询指令前缀的 bge 系。

    V1.2.3：BAAI/bge-small-zh-v1.5 官方要求查询侧加"为这个句子生成表示以用于
    检索相关文章："前缀以提升短查询/同义查询召回。仅查询侧加，文档侧（入库）
    embedding 不加，故无需重索引。远程 text-embedding-3 等模型应排除。
    """
    prompt = (settings.EMBEDDING_QUERY_PROMPT or "").strip()
    if not prompt:
        return False
    subs = [
        s.strip().lower()
        for s in (settings.EMBEDDING_QUERY_PROMPT_MODELS or "").split(",")
        if s.strip()
    ]
    if not subs:
        return False
    model = (
        settings.EMBEDDING_MODEL
        if settings.EMBEDDING_API_URL
        else settings.LOCAL_EMBEDDING_MODEL
    ) or ""
    return any(s and s in model.lower() for s in subs)


def _vector_search(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 10,
    doc_ids: list[int] | None = None,
) -> list[tuple[int, int, str, float, str | None, str | None]]:
    """向量语义检索。

    V1.2.5：doc_ids 非空时仅检索指定文档。

    返回: list[(doc_id, chunk_index, content, similarity, file_name, section_title)]
    """
    vs = VectorStore(db)

    if vs.chunk_count(kb_id, doc_ids=doc_ids) == 0:
        logger.info("知识库 %d 无向量化分块，跳过向量检索", kb_id)
        return []

    try:
        query_for_embedding = query
        if _should_apply_query_prompt():
            query_for_embedding = f"{settings.EMBEDDING_QUERY_PROMPT}{query}"
        query_embedding = embedding_service.embed_one(query_for_embedding)
    except Exception as e:
        logger.warning("查询向量化失败，跳过向量检索: %s", e)
        return []

    if query_embedding is None:
        logger.info("向量化服务不可用，跳过向量检索")
        return []

    return vs.vector_search(
        kb_id, query_embedding,
        top_k=top_k,
        score_threshold=settings.VECTOR_SCORE_THRESHOLD,
        doc_ids=doc_ids,
    )


def _rrf_fusion(
    bm25_results: list[tuple[int, int, int, float, str, str | None, str | None]],
    vector_results: list[tuple[int, int, str, float, str | None, str | None]],
    k: int = 60,
    top_k: int = 10,
) -> list[dict]:
    """RRF (Reciprocal Rank Fusion) 融合 BM25 和向量检索结果。

    V1.2.3：BM25 分块与向量分块统一按 (doc_id, chunk_index) 作为块身份 key，
    修复旧版两侧 key 不一致（BM25 用 chunk_id、向量用 doc_id/chunk_index）
    导致同一块永远无法融合的 bug。

    返回: list[dict]，每个字典包含 doc_id, chunk_index, content, score, file_name,
    section_title（所属章节，重索引后回填）
    """
    bm25_weight = settings.HYBRID_BM25_WEIGHT
    vector_weight = settings.HYBRID_VECTOR_WEIGHT

    # key: (doc_id, chunk_index)
    fusion: dict[tuple[int, int], dict] = {}

    # BM25 贡献
    for rank, (chunk_id, doc_id, chunk_index, bm25_score, content, file_name, section_title) in enumerate(bm25_results):
        rrf_score = bm25_weight / (k + rank + 1)
        key = (doc_id, chunk_index)
        if key not in fusion:
            fusion[key] = {
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": rrf_score,
                "file_name": file_name,
                "bm25_score": bm25_score,
                "vector_score": 0.0,
                "section_title": section_title,
            }
        else:
            fusion[key]["score"] += rrf_score
            fusion[key]["bm25_score"] = bm25_score
            fusion[key]["section_title"] = section_title

    # 向量检索贡献
    for rank, (doc_id, chunk_index, content, vec_score, file_name, section_title) in enumerate(vector_results):
        rrf_score = vector_weight / (k + rank + 1)
        key = (doc_id, chunk_index)
        if key not in fusion:
            fusion[key] = {
                "chunk_id": None,
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": rrf_score,
                "file_name": file_name,
                "bm25_score": 0.0,
                "vector_score": vec_score,
                "section_title": section_title,
            }
        else:
            fusion[key]["score"] += rrf_score
            fusion[key]["vector_score"] = vec_score
            fusion[key]["section_title"] = section_title

    # 按融合分数排序
    merged = sorted(fusion.values(), key=lambda x: x["score"], reverse=True)
    return merged[:top_k]


def hybrid_retrieve(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 5,
    query_keywords: list[str] | None = None,
    spec_codes: list[str] | None = None,
    doc_ids: list[int] | None = None,
) -> list[dict]:
    """检索主入口（V1.2.3：默认 hybrid，保留 vector_only 兼容）。

    流程：
    1. 检查知识库是否有向量化分块
    2. 按 RETRIEVE_MODE 分支：
       - hybrid（默认）：BM25 分块 + 向量语义检索 RRF 融合；
         无向量时退化为 BM25 分块检索（不抛异常）。
       - vector_only：纯向量，无向量时抛异常提示用户重新索引（V1.0.7 行为）。
    3. 截断、返回 top_k

    返回: list[dict]，每个字典包含:
        - doc_id / chunk_index / content / score / file_name / chunk_id
        - bm25_score（关键词分） / vector_score（余弦相似度）
    """
    query = query.strip()
    if not query:
        return []

    # V1.2.3：向量检索始终使用原始 query（query_keywords/spec_codes 仅供 BM25 路径，
    # 由上游从"原问题+改写词"分词得到，避免改写稀释向量信号）
    vs = VectorStore(db)
    has_vectors = vs.chunk_count(kb_id, doc_ids=doc_ids) > 0

    if settings.RETRIEVE_MODE != "hybrid":
        # ---- 纯向量模式（V1.0.7 方案 A）：完全保留原行为 ----
        if not has_vectors:
            logger.warning("知识库 %d 无向量化分块，纯向量检索不可用", kb_id)
            raise RuntimeError(
                "当前知识库尚未生成向量索引，请先上传文档或点击「重新索引」。"
                "（纯向量模式：不再降级 BM25）"
            )

        vector_results = _vector_search(db, kb_id, query, top_k=top_k, doc_ids=doc_ids)
        if not vector_results:
            logger.info("向量检索返回空: query=%r kb_id=%d", query[:50], kb_id)
            return []

        results = [
            {
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": float(similarity),
                "file_name": file_name,
                "bm25_score": 0.0,
                "vector_score": float(similarity),
                "section_title": section_title,
            }
            for doc_id, chunk_index, content, similarity, file_name, section_title in vector_results[:top_k]
        ]
        logger.info(
            "纯向量检索完成: query=%r kb_id=%d vec=%d returned=%d",
            query[:50], kb_id, len(vector_results), len(results),
        )
        return results

    # ---- hybrid 模式（V1.2.3 默认）：BM25 分块 + 向量 RRF 融合 ----
    kw = list(query_keywords) if query_keywords is not None else []
    sc = list(spec_codes) if spec_codes is not None else []
    if not kw and not sc:
        from .text_utils import build_query_terms

        kw, sc = build_query_terms(query)

    if not has_vectors:
        # hybrid 但知识库无向量索引：退化为 BM25 分块检索（不抛异常）
        logger.warning("hybrid 模式无向量索引，退化为 BM25 分块检索: kb_id=%d", kb_id)
        bm25_results = _bm25_search_chunks(db, kb_id, kw, sc, top_k=top_k, raw_query=query, doc_ids=doc_ids)
        return [
            {
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": float(score),
                "file_name": file_name,
                "bm25_score": float(score),
                "vector_score": 0.0,
                "section_title": section_title,
            }
            for _chunk_id, doc_id, chunk_index, score, content, file_name, section_title in bm25_results
        ]

    bm25_results = _bm25_search_chunks(db, kb_id, kw, sc, top_k=top_k * 3, raw_query=query, doc_ids=doc_ids)
    vector_results = _vector_search(db, kb_id, query, top_k=top_k * 3, doc_ids=doc_ids)

    if not bm25_results and not vector_results:
        logger.info("混合检索两侧均空: query=%r kb_id=%d", query[:50], kb_id)
        return []

    if not vector_results:
        # 仅 BM25 命中
        results = [
            {
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": float(score),
                "file_name": file_name,
                "bm25_score": float(score),
                "vector_score": 0.0,
                "section_title": section_title,
            }
            for _chunk_id, doc_id, chunk_index, score, content, file_name, section_title in bm25_results[:top_k]
        ]
        logger.info("混合检索(仅BM25): query=%r kb_id=%d returned=%d", query[:50], kb_id, len(results))
        return results

    if not bm25_results:
        # 仅向量命中
        results = [
            {
                "doc_id": doc_id,
                "chunk_index": chunk_index,
                "content": content,
                "score": float(similarity),
                "file_name": file_name,
                "bm25_score": 0.0,
                "vector_score": float(similarity),
                "section_title": section_title,
            }
            for doc_id, chunk_index, content, similarity, file_name, section_title in vector_results[:top_k]
        ]
        logger.info("混合检索(仅向量): query=%r kb_id=%d returned=%d", query[:50], kb_id, len(results))
        return results

    results = _rrf_fusion(bm25_results, vector_results, top_k=top_k)
    if settings.ENABLE_LITERAL_FORCE_INJECT and query:
        results = _inject_literal_hit(results, bm25_results, query)
    logger.info(
        "混合检索完成: query=%r kb_id=%d bm25=%d vec=%d returned=%d",
        query[:50], kb_id, len(bm25_results), len(vector_results), len(results),
    )
    return results


def _inject_literal_hit(
    results: list[dict],
    bm25_results: list[tuple[int, int, int, float, str, str | None, str | None]],
    query: str,
) -> list[dict]:
    """字面命中强制回插（V1.2.4，ENABLE_LITERAL_FORCE_INJECT 默认关）。

    若 query 整句在 bm25 候选池某块 content 中连续出现、且该块
    (doc_id, chunk_index) 未进入最终 results，则取整句命中数最高的块，
    替换 results 中 score 最低的一条，保持长度不变（= top_k）。
    仅作为 RRF 融合后的兜底：确保精确表名/条款号类字面块进入 LLM 上下文。
    """
    phrase = strip_markdown((query or "").strip().lower())
    if not phrase or not results:
        return results

    existing = {(r.get("doc_id"), r.get("chunk_index")) for r in results}
    literal: list[tuple[tuple[int, int], str, str | None, str | None, float]] = []
    for _chunk_id, doc_id, chunk_index, score, content, fname, stitle in bm25_results:
        key = (doc_id, chunk_index)
        if key in existing:
            continue
        if strip_markdown((content or "").lower()).count(phrase) > 0:
            literal.append((key, content, fname, stitle, float(score)))

    if not literal:
        return results

    # 整句命中数最多的块（必要时再按 bm25 score 打破平局）
    best = max(
        literal,
        key=lambda x: (
            strip_markdown((x[1] or "").lower()).count(phrase),
            x[4],
        ),
    )
    (_key, content, fname, stitle, score) = best

    # 替换 score 最低的一条，保持结果条数 = top_k
    new_results = list(results)
    new_results.sort(key=lambda r: r.get("score", 0.0))
    new_results[0] = {
        "chunk_id": None,
        "doc_id": best[0][0],
        "chunk_index": best[0][1],
        "content": content,
        "score": score,
        "file_name": fname,
        "bm25_score": score,
        "vector_score": 0.0,
        "section_title": stitle,
    }
    return new_results


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

    prompt = f"""你是一个工程规范领域的查询改写助手。请为下面的用户问题补充规范检索关键词。

要求：
1. 只输出需要补充的同义词、专业术语、规范编号，用空格分隔，不要任何解释和标点。
2. 不要重复输入中已经出现的词；输入中的规范名称、表名、章节名、数字编号必须原样保留，不得改写或删除。
3. 若输入已是精确术语（如表格名称「大变形分级标准表」、条款号），只输出少量补充术语即可。

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
