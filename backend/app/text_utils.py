"""检索文本公共工具：分词、Markdown 清洗、规范编号提取、BM25 打分与排序。

统一 rag_service 与 hybrid_search 的文本处理与 BM25 打分逻辑，避免重复。
本模块不依赖 ORM / DB，纯函数式工具。
"""
import re
import math
import logging
from collections import Counter

import jieba

logger = logging.getLogger("app.text_utils")

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

# Markdown 图片语法 ![alt](src)，URL 可能含嵌套括号（如 images/图(1).png）
MD_IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^()]*(?:\([^()]*\))*[^()]*)\)')

# BM25 参数
BM25_K1 = 1.5
BM25_B = 0.75


def strip_markdown(text: str) -> str:
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
    # 去除图片（保留 alt 文本，正则与 image_utils 共用）
    text = MD_IMAGE_RE.sub(r'\1', text)
    # 去除水平分割线
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)
    # 清理多余空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    """使用 jieba 分词，先去除 Markdown 格式，再过滤停用词和单字符。"""
    clean_text = strip_markdown(text.lower())
    words = jieba.lcut(clean_text)
    return [w for w in words if len(w) >= 2 and w not in _STOP_WORDS]


def extract_spec_codes(text: str) -> list[str]:
    """提取规范编号，如 GB 50205、GB/T 11345、JGJ 81 等。"""
    return [m.group().lower() for m in _SPEC_CODE_RE.finditer(text.upper())]


def build_query_terms(query: str) -> tuple[list[str], list[str]]:
    """将用户查询分解为 (分词关键词, 规范编号列表)。"""
    keywords = tokenize(query)
    spec_codes = extract_spec_codes(query)
    # 规范编号也作为关键词的一部分，但赋予更高权重
    return keywords, spec_codes


def _merge_query_terms(query_keywords: list[str], spec_codes: list[str]) -> list[str]:
    """合并分词关键词与规范编号为去重后的查询词列表。"""
    all_terms = list(query_keywords)
    for code in spec_codes:
        if code not in all_terms:
            all_terms.append(code)
    return all_terms


def bm25_score(
    freq: Counter,
    doc_len: int,
    avgdl: float,
    doc_count: int,
    term_doc_freq: dict[str, int],
    query_keywords: list[str],
    spec_codes: list[str],
    file_name: str = "",
    k1: float = BM25_K1,
    b: float = BM25_B,
) -> float:
    """BM25 单文档打分（idf + tf + 规范编号加权 + 文件名加权）。

    与重构前三个函数（rag_service.retrieve / hybrid_search 两个 BM25）的打分
    逻辑完全一致：
    - 对查询词（关键词 + 规范编号）逐项计算 idf × tf；
    - 命中规范编号的词 ×5；
    - 仅当词匹配分数 > 0 时，对文件名中命中关键词/规范编号附加 +2/+5。
    """
    all_query_terms = _merge_query_terms(query_keywords, spec_codes)
    if not all_query_terms:
        return 0.0

    score = 0.0
    for term in all_query_terms:
        tf = freq.get(term, 0)
        if tf == 0:
            continue
        # IDF
        n_qi = term_doc_freq.get(term, 0)
        idf = math.log((doc_count - n_qi + 0.5) / (n_qi + 0.5) + 1.0)
        # BM25 TF
        tf_score = (tf * (k1 + 1)) / (
            tf + k1 * (1 - b + b * doc_len / avgdl)
        )
        term_score = idf * tf_score
        # 规范编号匹配加权 x5
        if term in spec_codes:
            term_score *= 5.0
        score += term_score

    # 文件名匹配加权（仅当词匹配分数 > 0 时生效）
    if score > 0:
        doc_name_lower = (file_name or "").lower()
        for kw in query_keywords:
            if kw in doc_name_lower:
                score += 2.0
        for code in spec_codes:
            if code in doc_name_lower:
                score += 5.0

    return score


def bm25_rank(
    items: list[tuple[object, str, str]],
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
) -> list[tuple[object, float, str, str]]:
    """公共 BM25 排序管线。

    Args:
        items: [(item_id, text, file_name), ...]
        query_keywords: 分词关键词
        spec_codes: 规范编号列表
        top_k: 返回条数

    Returns:
        [(item_id, score, text, file_name)]，按分数降序，仅保留 score > 0。
    """
    if not query_keywords and not spec_codes:
        return []

    # 对每个文档分词并统计词频
    doc_data: list[tuple[object, str, str, Counter, int]] = []
    for item_id, text, file_name in items:
        text = text or ""
        if not text:
            continue
        tokens = tokenize(text)
        tokens.extend(extract_spec_codes(text))
        if not tokens:
            continue
        doc_data.append((item_id, text, file_name, Counter(tokens), len(tokens)))

    if not doc_data:
        return []

    # 平均文档长度
    avgdl = sum(d[4] for d in doc_data) / len(doc_data)
    doc_count = len(doc_data)

    # term -> 包含该词的文档数（用于 IDF）
    term_doc_freq: dict[str, int] = {}
    for _, _, _, freq, _ in doc_data:
        for term in freq:
            term_doc_freq[term] = term_doc_freq.get(term, 0) + 1

    scored: list[tuple[object, float, str, str]] = []
    for item_id, text, file_name, freq, doc_len in doc_data:
        score = bm25_score(
            freq, doc_len, avgdl, doc_count, term_doc_freq,
            query_keywords, spec_codes, file_name,
        )
        if score > 0:
            scored.append((item_id, score, text, file_name))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]


def substring_rank(
    items: list[tuple[object, str, str]],
    query_keywords: list[str],
    spec_codes: list[str],
    top_k: int = 10,
) -> list[tuple[object, float]]:
    """BM25 无结果时的子串匹配兜底。

    与重构前 retrieve / _bm25_search_docs 的兜底逻辑一致：对清洗后的纯文本
    统计查询词子串出现次数；仅统计「能分出有效词」的文档（与 BM25 词频过滤一致）。

    Returns:
        [(item_id, score)]，按分数降序，仅保留 score > 0。
    """
    all_query_terms = _merge_query_terms(query_keywords, spec_codes)
    if not all_query_terms:
        return []

    scored: list[tuple[object, float]] = []
    for item_id, text, _file_name in items:
        # 与 BM25 词频过滤一致：无法分出有效词的文档不参与兜底
        tokens = tokenize(text or "")
        if not tokens:
            continue
        text_lower = strip_markdown((text or "").lower())
        score = 0.0
        for term in all_query_terms:
            score += text_lower.count(term)
        if score > 0:
            scored.append((item_id, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:top_k]
