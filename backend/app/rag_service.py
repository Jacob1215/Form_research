"""RAG 编排：检索增强、结构化上下文构建、System/User 消息分离。

模块职责：
1. retrieve / retrieve_with_hybrid：检索入口（V1.0.7 纯向量语义检索优先，异常时降级 BM25）
2. _attach_images：为检索结果附带分块图片（V1.0.9，供大模型内嵌与对话界面展示）
3. _build_structured_context / build_prompt：结构化上下文 + System/User 消息分离
4. SYSTEM_PROMPT：角色定义 + 规则 + 输出格式（不含上下文）

分词 / Markdown 清洗 / 规范编号提取 / BM25 打分等基础工具统一在 text_utils，此处不再重复。
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Document
from .text_utils import (
    build_query_terms,
    strip_markdown,
    bm25_rank,
    substring_rank,
)

logger = logging.getLogger("app.rag")

# ============================================================
# System Prompt —— 角色定义 + 规则 + 输出格式（不含上下文）
# ============================================================
SYSTEM_PROMPT = """你是一名工程规范领域的智能问答助手，专门基于检索到的规范条文回答用户问题。

## 核心原则
1. **优先依据检索到的规范条文回答**。如果检索结果包含相关内容，必须基于检索内容回答。
2. **引用规范原文**，引用时使用「」符号包裹原文关键内容，尽量保留原文表述。
3. **标注出处**，格式：《规范名称》第X章第X条（或第X.X.X条）。如检索内容中未包含条款号，可仅标注文档名称。
4. 若检索结果与问题确实无关，回答"未在知识库中检索到相关规范条款"。

## 回答结构

### 一、直接回答
用1-3句话直接回答用户问题，语言简练。

### 二、规范原文引用
列出相关条款原文，每条格式如下：
> 「规范原文内容（引用原文关键内容，保留原标点）」
> ——《规范名称》第X.X.X条 【强制性条文 / 推荐性条文 / 一般条文】

### 三、条款说明（可选）
对引用条款做简要说明，帮助用户理解。

### 四、参考来源
- 《规范名称1》
- 《规范名称2》

## 特殊情况处理
- **多条相关条款**：按相关度从高到低全部列出。
- **条款冲突**：如不同规范对同一问题有不同规定，全部列出并注明差异。
- **强制性条文**：在出处后标注【强制性条文】。
- **检索内容不含条款号**：仍可基于检索到的原文内容回答，标注来源文档名称即可。
- **检索结果不足**：明确说明"未在知识库中检索到相关规范条款"，建议用户更换关键词。

- **相关图片展示**：若某个检索结果带有「相关图片」信息，且其中图片能直观说明用户问题，请在回答的对应位置用 Markdown 图片语法内嵌该图片：`![图片说明](/api/uploads/.../图片文件名)`，图片与文字排版一致、直接插入在文本流中。仅可使用检索结果中给出的图片地址，禁止编造。

## 禁止事项
- 不得编造未在检索结果中出现的规范编号或条款号。
- 不得将检索到的条文内容与自身知识混淆后输出。
- 不得编造图片地址或图片内容。
"""


# ============================================================
# 检索增强组件
# ============================================================

def _find_relevant_section(text: str, keywords: list[str], spec_codes: list[str], window: int = 1500) -> str:
    """在长文档中定位关键词最密集的区域，截取上下文窗口。"""
    if len(text) <= window * 2:
        return text

    text_lower = text.lower()

    # 收集所有关键词出现位置
    search_terms = keywords + spec_codes
    positions: list[int] = []
    for kw in search_terms:
        start = 0
        kw_lower = kw.lower()
        while True:
            idx = text_lower.find(kw_lower, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + len(kw_lower)

    if not positions:
        return text[:window * 2] + "\n...（内容过长，已截断）"

    # 找到关键词最密集的窗口
    positions.sort()
    best_center = positions[0]
    best_density = 0
    window_size = window * 2

    for i, pos in enumerate(positions):
        # 统计以 pos 为起点、window_size 范围内的关键词数
        count = 0
        for j in range(i, len(positions)):
            if positions[j] - pos <= window_size:
                count += 1
            else:
                break
        if count > best_density:
            best_density = count
            best_center = pos + (positions[min(i + count - 1, len(positions) - 1)] - pos) // 2

    start = max(0, best_center - window)
    end = min(len(text), best_center + window)
    section = text[start:end]
    if start > 0:
        section = "..." + section
    if end < len(text):
        section += "\n...（内容过长，已截取最相关段落）"
    return section

def retrieve(db: Session, kb_id: int, query: str, top_k: int = 5, doc_ids: list[int] | None = None) -> list[tuple[Document, float]]:
    """纯 BM25 关键词检索（保留作为降级方案）。

    返回 (document, score) 列表。当混合检索不可用时作为兜底。
    打分逻辑复用 text_utils.bm25_rank / substring_rank，行为与重构前一致。
    V1.2.5：doc_ids 非空时仅检索指定文档。
    """
    query_keywords, spec_codes = build_query_terms(query.strip())
    if not query_keywords and not spec_codes:
        return []

    # 获取知识库中所有有内容的文档（V1.2.5：doc_ids 非空时只取指定文档）
    stmt = select(Document).where(
        Document.kb_id == kb_id,
        Document.content_text.isnot(None),
    )
    if doc_ids:
        stmt = stmt.where(Document.id.in_(doc_ids))
    docs = db.execute(stmt).scalars().all()
    if not docs:
        return []

    # 所有查询词（普通关键词 + 规范编号，仅用于日志）
    all_query_terms = list(query_keywords)
    for code in spec_codes:
        if code not in all_query_terms:
            all_query_terms.append(code)

    items = [(doc, doc.content_text or "", doc.file_name or "") for doc in docs]

    # BM25 打分（公共核心，分数/排序与重构前完全一致）
    ranked = bm25_rank(items, query_keywords, spec_codes, top_k=top_k)
    scored = [(doc, score) for doc, score, _text, _fname in ranked]

    # BM25 无结果时，降级为子串匹配兜底
    if not scored:
        logger.warning(
            "BM25 无结果，降级子串匹配: query=%r terms=%r kb_id=%d docs=%d",
            query[:50], all_query_terms, kb_id, len(docs),
        )
        scored = substring_rank(items, query_keywords, spec_codes, top_k=top_k)
        logger.info("子串兜底结果: %d 条", len(scored))

    logger.info(
        "BM25 检索完成: query=%r terms=%r kb_id=%d docs=%d with_content=%d results=%d",
        query[:50], all_query_terms, kb_id, len(docs),
        len([d for d in docs if d.content_text and d.content_text.strip()]), len(scored),
    )
    return scored[:top_k]


def retrieve_with_hybrid(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 5,
    rewritten_query: str | None = None,
    bm25_query: str | None = None,
    doc_ids: list[int] | None = None,
) -> list[dict]:
    """混合检索入口：BM25 + 向量语义检索 + RRF 融合。

    V1.2.3：向量检索始终使用原始 query（避免查询改写词稀释精确表名信号）；
    BM25 关键词路径使用 bm25_query（原问题 + 改写补充词，更利于"分级标准表"
    这类字面表名命中）。不传 bm25_query 时回退用 rewritten_query / query，
    兼容 report.py / ppt.py 的旧调用。

    Args:
        db: 数据库会话
        kb_id: 知识库 ID
        query: 用户查询（向量路径与 RAG 上下文使用）
        top_k: 返回结果数
        rewritten_query: 改写后的查询文本（可选，旧接口，视为 BM25 文本）
        bm25_query: V1.2.3 BM25 路径检索文本（message + 改写词），优先于 rewritten_query

    Returns:
        list[dict]，每个字典包含:
        - doc_id / chunk_index / content / score / file_name
        - bm25_score / vector_score
    """
    from .hybrid_search import hybrid_retrieve
    from .text_utils import build_query_terms

    search_query = query  # 向量始终用原始 query
    bm25_text = (bm25_query or rewritten_query or query).strip() or query
    query_keywords, spec_codes = build_query_terms(bm25_text)

    try:
        results = hybrid_retrieve(
            db, kb_id, search_query, top_k=top_k,
            query_keywords=query_keywords, spec_codes=spec_codes,
            doc_ids=doc_ids,
        )
        if results:
            logger.info(
                "混合检索成功: query=%r bm25_text=%r kb_id=%d results=%d",
                query[:50], bm25_text[:50], kb_id, len(results),
            )
            # V1.0.9：为检索结果附带分块中的图片（alt, url），供 LLM 上下文与对话界面展示
            _attach_images(db, kb_id, results)
            return results
    except Exception as e:
        logger.warning("混合检索异常: %s，降级为纯 BM25", e)

    # 降级：纯 BM25 文档检索（同样用扩展后的 bm25_text，利于精确表名命中）
    bm25_results = retrieve(db, kb_id, bm25_text, top_k=top_k, doc_ids=doc_ids)
    results = [
        {
            "doc_id": doc.id,
            "chunk_index": -1,
            "content": doc.content_text or "",
            "score": score,
            "file_name": doc.file_name,
            "bm25_score": score,
            "vector_score": 0.0,
        }
        for doc, score in bm25_results
    ]
    # V1.0.9：BM25 降级路径同样附带图片
    _attach_images(db, kb_id, results)
    return results


def _attach_images(db: Session, kb_id: int, results: list[dict]) -> None:
    """为检索结果附带分块中的图片（alt, url）列表。

    V1.0.9：md 文档分块中的图片引用对 LLM 不可见，需在把上下文交给大模型之前，
    将图片提取为绝对 /api/uploads URL 挂在每个结果上。提取基于分块内容
    （result["content"]），仅对 md/markdown 文档生效（PDF/txt 文本中的字面
    ![]()/<img> 属于误报，跳过），并对重写后的地址做磁盘存在性校验。
    """
    from .image_utils import doc_rel_dir, extract_images_from_content

    doc_ids = {r.get("doc_id") for r in results if isinstance(r, dict) and r.get("doc_id")}
    if not doc_ids:
        return
    docs = db.execute(select(Document).where(Document.id.in_(doc_ids))).scalars().all()
    doc_map = {d.id: d for d in docs}
    for r in results:
        if not isinstance(r, dict):
            continue
        doc = doc_map.get(r.get("doc_id"))
        if doc is None:
            r["images"] = []
            continue
        rel_dir = doc_rel_dir(doc.kb_id, doc.file_path or "")
        r["images"] = extract_images_from_content(
            r.get("content", ""), kb_id, rel_dir,
            doc.file_type, max_images=9,
        )


def _build_structured_context(
    results: list[tuple[Document, float]] | list[dict],
    query: str,
) -> str:
    """将检索结果格式化为带元数据的结构化上下文块。

    支持两种输入格式：
    1. 旧格式: list[tuple[Document, float]] — 纯 BM25 文档检索
    2. 新格式: list[dict] — 混合检索结果，每个 dict 含 content/file_name/score 等

    每条包含：序号、规范名称（文档名）、相关度评分、原文内容。
    对长文档自动定位关键词最密集的段落截取。
    """
    keywords, spec_codes = build_query_terms(query)

    blocks: list[str] = []
    for i, item in enumerate(results, 1):
        if isinstance(item, dict):
            # 混合检索结果
            file_name = item.get("file_name", "未知文档")
            score = item.get("score", 0.0)
            bm25_score = item.get("bm25_score", 0.0)
            vector_score = item.get("vector_score", 0.0)

            # 使用分块内容（已是最相关段落）
            raw_text = item.get("content", "")
            clean_text = strip_markdown(raw_text)

            # 如果分块内容较长，进一步定位关键词密集区域
            if len(clean_text) > 2000:
                section = _find_relevant_section(clean_text, keywords, spec_codes)
            else:
                section = clean_text

            # 构建评分信息
            score_info = f"混合相关度：{score:.2f}"
            if vector_score > 0:
                score_info += f"（语义：{vector_score:.2f}，关键词：{bm25_score:.2f}）"

            # V1.2.3：所属章节标题（重索引后回填），便于大模型引用出处
            section_title = item.get("section_title")
            title_line = f"所属章节：{section_title}\n" if section_title else ""

            # V1.0.9：附带检索结果中的相关图片（最多 3 张），供大模型内嵌展示
            image_marks = [
                f"![{alt}]({url})"
                for alt, url in (item.get("images") or [])[:3]
            ]
            images_line = "\n相关图片：" + " ".join(image_marks) if image_marks else ""
        else:
            # 旧格式 (Document, float)
            doc, score = item
            file_name = doc.file_name
            raw_text = doc.content_text or ""
            clean_text = strip_markdown(raw_text)
            section = _find_relevant_section(clean_text, keywords, spec_codes)
            score_info = f"相关度：{score:.2f}"
            title_line = ""
            images_line = ""

        block = (
            f"【检索结果 {i}】\n"
            f"规范名称：《{file_name}》\n"
            f"{title_line}"
            f"{score_info}\n"
            f"原文内容：{section}"
            f"{images_line}"
        )
        blocks.append(block)

    return "\n\n".join(blocks)


def _build_user_message(context_block: str, query: str) -> str:
    """将结构化上下文和用户问题组装为 user message。

    System Prompt 作为独立的 system 角色消息发送，不与此混合。
    """
    if context_block:
        return (
            f"## 检索到的规范条文\n\n"
            f"{context_block}\n\n"
            f"---\n\n"
            f"## 用户问题\n{query}"
        )
    return (
        f"## 检索结果\n\n"
        f"未在知识库中检索到相关规范条款。\n\n"
        f"---\n\n"
        f"## 用户问题\n{query}"
    )


def build_prompt(
    query: str,
    results: list[tuple[Document, float]] | list[dict],
) -> list[dict]:
    """构造 RAG 消息列表：system（角色+规则）+ user（结构化上下文+问题）。

    支持混合检索结果（dict）和旧格式（tuple）。

    System Prompt 与上下文分离，确保指令不被证据稀释。
    """
    context_block = _build_structured_context(results, query)
    user_message = _build_user_message(context_block, query)

    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]


def build_chat_messages(
    query: str,
    results: list[dict],
    history: list[dict],
    system_prompt: str = SYSTEM_PROMPT,
    context_window: int = 0,
    max_tokens: int = 2048,
    include_context: bool = True,
) -> list[dict]:
    """构造带历史与上下文预算的对话消息列表（V1.2.3）。

    同一会话连续对话：结构 = system + 裁剪后的历史 + 当前 user（含 RAG 上下文）。
    检索上下文只附到当前轮 user 消息（仿 report._build_llm_messages），历史原样
    透传（旧检索上下文已隐含在历史 assistant 回答中，避免重复注入旧证据）。

    context_window 未配置（<=0）时不做裁剪，全量历史透传（兼容降级）。

    Args:
        query: 当前轮用户问题
        results: 当前轮检索结果（dict 格式）
        history: 历史消息（旧→新），每项 {"role", "content"}
        system_prompt: system 提示词
        context_window: 模型上下文窗口（tokens）
        max_tokens: LLM 输出上限
        include_context: 是否注入检索上下文（无知识库纯问答时为 False）
    """
    from .context_budget import trim_history_for_budget

    if include_context:
        context_block = _build_structured_context(results, query)
        user_message = _build_user_message(context_block, query)
    else:
        user_message = query

    if not context_window or int(context_window) <= 0:
        return [
            {"role": "system", "content": system_prompt},
            *list(history),
            {"role": "user", "content": user_message},
        ]
    return trim_history_for_budget(
        system_prompt, history, user_message, max_tokens, context_window,
    )
