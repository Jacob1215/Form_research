"""RAG 编排：BM25 关键词检索、结构化上下文构建、System/User 消息分离。

优化版：
1. System Prompt 与 Context 分离（指令/证据解耦）
2. 要求逐字引用规范原文，禁止改写
3. 结构化输出（直接回答 + 原文引用 + 条款说明 + 参考来源）
4. 禁止「基于自身知识」补充规范内容，降低幻觉
5. 支持多条款、强制性条文标注、条款冲突处理
6. Context 结构化，携带元数据（规范名 / 相关度）
7. jieba 中文分词 + BM25 评分 + 规范编号提取 + 文件名加权
"""
import re
import math
import logging
from typing import Optional
from collections import Counter

import jieba

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Document

logger = logging.getLogger("app.rag")

# 添加工程规范领域常用词汇，提升 jieba 分词准确性
for _term in (
    "焊缝探伤", "超声波探伤", "射线探伤", "强制性条文", "推荐性条文",
    "一级焊缝", "二级焊缝", "三级焊缝", "钢结构", "焊接工程",
    "焊缝质量", "无损检测", "焊缝表面", "内部缺陷", "探伤比例",
    "工程质量", "验收标准", "施工质量", "设计要求", "全焊透",
    "角焊缝", "对接焊缝", "咬边", "夹渣", "气孔", "未焊满",
    "根部收缩", "弧坑裂纹", "电弧擦伤", "焊瘤",
    "混凝土", "钢筋", "模板工程", "脚手架", "基坑支护",
    "防水工程", "保温工程", "抗震设计", "防火设计", "承重结构",
):
    jieba.add_word(_term)

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

## 禁止事项
- 不得编造未在检索结果中出现的规范编号或条款号。
- 不得将检索到的条文内容与自身知识混淆后输出。
"""


# ============================================================
# 检索增强组件
# ============================================================

# BM25 参数
_BM25_K1 = 1.5
_BM25_B = 0.75

# 规范编号正则：GB 50205、GB/T 11345、JGJ 81、DBJ/T 08 等
_SPEC_CODE_RE = re.compile(r'[A-Z]{2,4}/?[A-Z]?\s*\d{2,5}(?:[-.]?\d+)*')

# 中文停用词（移除了'规定'、'要求'等工程规范领域有意义的词）
_STOP_WORDS = frozenset({
    '的', '了', '在', '是', '我', '有', '和', '就', '不', '人', '都', '一',
    '一个', '上', '也', '很', '到', '说', '要', '去', '你', '会', '着', '没有',
    '看', '好', '自己', '这', '那', '它', '他', '她', '什么', '怎么', '哪',
    '哪些', '哪个', '为什么', '如何', '请问', '一下', '可以', '吗', '吧', '呢',
    '啊', '哦', '嗯', '么', '请', '帮', '帮忙', '告诉', '知道', '关于', '对于',
    '根据', '按照', '依据', '相关', '情况', '问题', '下面',
    '是不是', '能不能', '要不要', '这个', '那个',
    '这些', '那些', '他们', '她们', '它们', '我们', '你们', '一直', '一些',
})


def _strip_markdown(text: str) -> str:
    """去除 Markdown 格式符号，保留纯文本内容。

    解决 Markdown 文档中 #、**、|、> 等符号污染分词的问题。
    """
    # 去除代码块
    text = re.sub(r'```[\s\S]*?```', '', text)
    # 去除行内代码
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # 去除标题标记
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # 去除粗体/斜体
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,3}([^_]+)_{1,3}', r'\1', text)
    # 去除表格分隔行
    text = re.sub(r'^\|[\s\-:|]+\|$', '', text, flags=re.MULTILINE)
    # 去除表格管道符
    text = re.sub(r'\|', ' ', text)
    # 去除引用标记
    text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
    # 去除列表标记
    text = re.sub(r'^[\s]*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\s]*\d+\.\s+', '', text, flags=re.MULTILINE)
    # 去除链接，保留文本
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # 去除图片
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', r'\1', text)
    # 去除水平分割线
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _tokenize(text: str) -> list[str]:
    """使用 jieba 分词，先去除 Markdown 格式，再过滤停用词和单字符。"""
    clean_text = _strip_markdown(text.lower())
    words = jieba.lcut(clean_text)
    return [w for w in words if len(w) >= 2 and w not in _STOP_WORDS]


def _extract_spec_codes(text: str) -> list[str]:
    """提取规范编号，如 GB 50205、GB/T 11345、JGJ 81 等。"""
    return [m.group().lower() for m in _SPEC_CODE_RE.finditer(text.upper())]


def _build_query_terms(query: str) -> tuple[list[str], list[str]]:
    """将用户查询分解为 (分词关键词, 规范编号列表)。"""
    keywords = _tokenize(query)
    spec_codes = _extract_spec_codes(query)
    # 规范编号也作为关键词的一部分，但赋予更高权重
    return keywords, spec_codes


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

def retrieve(db: Session, kb_id: int, query: str, top_k: int = 5) -> list[tuple[Document, float]]:
    """纯 BM25 关键词检索（保留作为降级方案）。

    返回 (document, score) 列表。
    当混合检索不可用时作为兜底。
    """
    query_keywords, spec_codes = _build_query_terms(query.strip())
    if not query_keywords and not spec_codes:
        return []

    # 获取知识库中所有有内容的文档
    stmt = select(Document).where(
        Document.kb_id == kb_id,
        Document.content_text.isnot(None),
    )
    docs = db.execute(stmt).scalars().all()
    if not docs:
        return []

    # 所有查询词（普通关键词 + 规范编号）
    all_query_terms = list(query_keywords)
    for code in spec_codes:
        if code not in all_query_terms:
            all_query_terms.append(code)

    # 对每个文档分词并统计词频
    doc_data: list[tuple[Document, Counter, int]] = []
    for doc in docs:
        text = doc.content_text or ""
        if not text:
            continue
        tokens = _tokenize(text)
        tokens.extend(_extract_spec_codes(text))
        if not tokens:
            continue
        doc_data.append((doc, Counter(tokens), len(tokens)))

    if not doc_data:
        return []

    # 平均文档长度
    avgdl = sum(dl for _, _, dl in doc_data) / len(doc_data)
    doc_count = len(doc_data)

    # term -> 包含该词的文档数（用于 IDF）
    term_doc_freq: dict[str, int] = {}
    for _, freq, _ in doc_data:
        for term in freq:
            term_doc_freq[term] = term_doc_freq.get(term, 0) + 1

    scored: list[tuple[Document, float]] = []
    for doc, freq, doc_len in doc_data:
        score = 0.0

        for term in all_query_terms:
            tf = freq.get(term, 0)
            if tf == 0:
                continue

            # IDF
            n_qi = term_doc_freq.get(term, 0)
            idf = math.log((doc_count - n_qi + 0.5) / (n_qi + 0.5) + 1.0)

            # BM25 TF
            tf_score = (tf * (_BM25_K1 + 1)) / (
                tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * doc_len / avgdl)
            )

            term_score = idf * tf_score

            # 规范编号匹配加权 x5（从 3x 提升到 5x）
            if term in spec_codes:
                term_score *= 5.0

            score += term_score

        # 文件名匹配加权
        if score > 0:
            doc_name_lower = (doc.file_name or "").lower()
            for kw in query_keywords:
                if kw in doc_name_lower:
                    score += 2.0
            for code in spec_codes:
                if code in doc_name_lower:
                    score += 5.0

        if score > 0:
            scored.append((doc, score))

    scored.sort(key=lambda x: x[1], reverse=True)

    # BM25 无结果时，降级为子串匹配兜底
    if not scored:
        logger.warning(
            "BM25 无结果，降级子串匹配: query=%r terms=%r kb_id=%d docs=%d",
            query[:50], all_query_terms, kb_id, len(doc_data),
        )
        for doc, freq, doc_len in doc_data:
            text_lower = _strip_markdown((doc.content_text or "").lower())
            score = 0.0
            for term in all_query_terms:
                count = text_lower.count(term)
                if count > 0:
                    score += count
            if score > 0:
                scored.append((doc, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        logger.info("子串兜底结果: %d 条", len(scored))

    logger.info(
        "BM25 检索完成: query=%r terms=%r kb_id=%d docs=%d with_content=%d results=%d",
        query[:50], all_query_terms, kb_id, len(docs), len(doc_data), len(scored),
    )
    return scored[:top_k]


def retrieve_with_hybrid(
    db: Session,
    kb_id: int,
    query: str,
    top_k: int = 5,
    rewritten_query: str | None = None,
) -> list[dict]:
    """混合检索入口：BM25 + 向量语义检索 + RRF 融合。

    优先使用混合检索（需要分块且已向量化），不可用时自动降级为纯 BM25。

    Args:
        db: 数据库会话
        kb_id: 知识库 ID
        query: 用户查询（或已改写的查询）
        top_k: 返回结果数
        rewritten_query: 改写后的查询文本（可选）

    Returns:
        list[dict]，每个字典包含:
        - doc_id / chunk_index / content / score / file_name
        - bm25_score / vector_score
    """
    from .hybrid_search import hybrid_retrieve

    search_query = rewritten_query or query

    try:
        results = hybrid_retrieve(db, kb_id, search_query, top_k=top_k)
        if results:
            logger.info(
                "混合检索成功: query=%r kb_id=%d results=%d",
                query[:50], kb_id, len(results),
            )
            return results
    except Exception as e:
        logger.warning("混合检索异常: %s，降级为纯 BM25", e)

    # 降级：纯 BM25 文档检索
    bm25_results = retrieve(db, kb_id, search_query, top_k=top_k)
    return [
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
    keywords, spec_codes = _build_query_terms(query)

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
            clean_text = _strip_markdown(raw_text)

            # 如果分块内容较长，进一步定位关键词密集区域
            if len(clean_text) > 2000:
                section = _find_relevant_section(clean_text, keywords, spec_codes)
            else:
                section = clean_text

            # 构建评分信息
            score_info = f"混合相关度：{score:.2f}"
            if vector_score > 0:
                score_info += f"（语义：{vector_score:.2f}，关键词：{bm25_score:.2f}）"
        else:
            # 旧格式 (Document, float)
            doc, score = item
            file_name = doc.file_name
            raw_text = doc.content_text or ""
            clean_text = _strip_markdown(raw_text)
            section = _find_relevant_section(clean_text, keywords, spec_codes)
            score_info = f"相关度：{score:.2f}"

        block = (
            f"【检索结果 {i}】\n"
            f"规范名称：《{file_name}》\n"
            f"{score_info}\n"
            f"原文内容：{section}"
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
