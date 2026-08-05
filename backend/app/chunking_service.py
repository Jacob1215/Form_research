"""语义文档分块服务：针对工程规范文档优化。

分块策略：
1. 按条款 / 章节边界分块（优先）
2. 固定大小 + 重叠兜底

适配中文工程规范的结构特征：
- 第X.X.X条 / 第X章 / 第X节
- 一、二、三、... / （一）（二）
- 数字编号：3.2.1 / 1.2.3
- 《规范名称》引用
"""
import re
import logging
from typing import Iterator
from dataclasses import dataclass, field

from .config import settings

logger = logging.getLogger("app.chunking")


@dataclass
class ChunkMeta:
    """分块元数据。"""
    index: int
    content: str
    token_count: int = 0
    section_title: str | None = None


class ChunkingService:
    """工程规范文档分块器。"""

    # 规范文档结构标记（按优先级排序）
    _CLAUSE_BOUNDARY_PATTERNS = [
        # 条款号：第5.2.4条、第3.1条
        re.compile(r'(?:^|\n)\s*第[\d.]+条\s'),
        # 章节号：第五章、第3章
        re.compile(r'(?:^|\n)\s*第[一二三四五六七八九十百\d]+章\s'),
        # 中文序号：一、二、三、
        re.compile(r'(?:^|\n)\s*[一二三四五六七八九十]+[、．.]\s'),
        # 带括号序号：（一）（二）
        re.compile(r'(?:^|\n)\s*（[一二三四五六七八九十]+）\s'),
        # 数字编号：3.2.1 / 1.2.3（规范常见格式）
        re.compile(r'(?:^|\n)\s*\d+\.\d+(?:\.\d+)?\s+(?=[一-鿿])'),
        # Markdown 标题：## 第X章
        re.compile(r'(?:^|\n)#{1,6}\s'),
    ]

    # 默认分块参数
    _CHUNK_SIZE = 600       # 字符数
    _CHUNK_OVERLAP = 150    # 重叠字符数
    _MIN_CHUNK_SIZE = 80    # 最小分块大小，小于此值的合并到前一块
    # V1.2.3：单文档最大分块数改为可配置（默认 1500），配合 DOC_TEXT_CAP 避免大规范靠后章节被丢
    _MAX_CHUNKS = settings.CHUNK_MAX_COUNT

    # V1.2.3：表格行识别。连续 Markdown 表格行（含 |）作为原子块不拆，
    # 分隔行 |---|---| 不算数据行。保证"表名/表头/表体"同块，检索不丢表体。
    _TABLE_ROW_RE = re.compile(r'^\s*\|.*\|\s*$')
    _TABLE_SEP_RE = re.compile(r'^\s*\|[\s\-:|]+\|\s*$')

    def chunk_text(self, text: str, file_name: str = "") -> list[ChunkMeta]:
        """将文档文本分割为语义连贯的分块。

        Args:
            text: 文档原始文本
            file_name: 文档名称（用于日志）

        Returns:
            按文档顺序排列的分块列表
        """
        if not text or not text.strip():
            return []

        clean_text = text.strip()

        # 策略1：按结构边界分块
        chunks = self._chunk_by_structure(clean_text)

        # 策略2：对过大的块进行二次拆分
        chunks = self._split_oversized(chunks)

        # 策略3：合并过小的块
        chunks = self._merge_undersized(chunks)

        # 限制最大分块数
        if len(chunks) > self._MAX_CHUNKS:
            logger.warning(
                "文档 %s 分块数 %d 超过上限 %d，已截断",
                file_name, len(chunks), self._MAX_CHUNKS,
            )
            chunks = chunks[:self._MAX_CHUNKS]

        # 重新编号
        for i, chunk in enumerate(chunks):
            chunk.index = i
            # 估算 token 数（中文约 1 字 ≈ 1.5 token）
            chunk.token_count = int(len(chunk.content) * 1.5)

        logger.info(
            "文档 %s 分块完成: %d 个分块，平均 %d 字符",
            file_name, len(chunks),
            sum(len(c.content) for c in chunks) // max(len(chunks), 1),
        )
        return chunks

    def _chunk_by_structure(self, text: str) -> list[ChunkMeta]:
        """按规范文档的结构标记进行分块。"""
        # 查找所有结构边界
        boundaries = self._find_boundaries(text)

        if not boundaries:
            # 无结构标记，整篇作为一个块
            return [ChunkMeta(index=0, content=text)]

        chunks: list[ChunkMeta] = []
        for i, (start, end, title) in enumerate(boundaries):
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunks.append(ChunkMeta(
                    index=i,
                    content=chunk_text,
                    section_title=title,
                ))

        return chunks

    def _find_boundaries(
        self, text: str
    ) -> list[tuple[int, int, str | None]]:
        """查找文档中的所有结构边界。

        返回: [(start_pos, end_pos, section_title), ...]
        每个元组表示一个分块的起止位置和章节标题。
        """
        # 收集所有匹配位置
        positions: list[tuple[int, str | None]] = [(0, None)]

        for pattern in self._CLAUSE_BOUNDARY_PATTERNS:
            for match in pattern.finditer(text):
                pos = match.start()
                # 跳过已在列表中的位置（去重，容差 5 字符）
                if not any(abs(pos - p) < 5 for p, _ in positions):
                    # 提取标题（匹配行的前 80 字符）
                    line_end = text.find("\n", pos)
                    title = text[pos:line_end if line_end > 0 else pos + 80].strip()
                    positions.append((pos, title[:80]))

        # 按位置排序
        positions.sort(key=lambda x: x[0])

        # 构建边界区间
        boundaries: list[tuple[int, int, str | None]] = []
        for i, (pos, title) in enumerate(positions):
            start = pos
            if i + 1 < len(positions):
                end = positions[i + 1][0]
            else:
                end = len(text)
            boundaries.append((start, end, title))

        return boundaries

    def _split_oversized(self, chunks: list[ChunkMeta]) -> list[ChunkMeta]:
        """对超过 CHUNK_SIZE 的分块按句子边界二次拆分。"""
        result: list[ChunkMeta] = []
        for chunk in chunks:
            if len(chunk.content) <= self._CHUNK_SIZE:
                result.append(chunk)
                continue

            sub_chunks = self._split_by_sentences(chunk.content)
            for sub in sub_chunks:
                result.append(ChunkMeta(
                    index=0,  # 后续重新编号
                    content=sub,
                    section_title=chunk.section_title,
                ))

        return result

    def _tokenize_protected(self, text: str) -> list[str]:
        """将文本切成 token 列表：普通句子 或 整段表格块（不可拆分的原子）。

        V1.2.3：连续 Markdown 表格行作为一个原子块（允许行间空行容错），
        并把紧邻其上的"表X 标题"行并入，保证"表名 + 表体"同块、检索不丢表体；
        其余文本按句子边界切分。
        """
        # 表格标题行模式：如"表5.1 大变形分级标准表"、"附表A"
        caption_re = re.compile(r'^\s*(表|附表)\s*[\d.、\-]*')
        lines = text.split("\n")
        tokens: list[str] = []
        pending: list[str] = []

        def flush_pending():
            nonlocal pending
            for ln in pending:
                for sent in re.split(r'(?<=[。！？；])', ln):
                    if sent.strip():
                        tokens.append(sent)
            pending = []

        i = 0
        n = len(lines)
        while i < n:
            line = lines[i]
            if self._TABLE_ROW_RE.match(line) and not self._TABLE_SEP_RE.match(line):
                block: list[str] = []
                # 若 pending 尾部是"表X 标题"行，并入表格块，避免表名与表体分块
                if pending:
                    idx = len(pending) - 1
                    while idx >= 0 and not pending[idx].strip():
                        idx -= 1
                    if idx >= 0:
                        cand = pending[idx].strip()
                        if len(cand) <= 80 and caption_re.match(cand):
                            block.append(pending[idx])
                            pending = pending[:idx]
                flush_pending()
                block.append(line)
                j = i + 1
                while j < n:
                    nxt = lines[j]
                    if self._TABLE_ROW_RE.match(nxt) and not self._TABLE_SEP_RE.match(nxt):
                        block.append(nxt)
                        j += 1
                    elif nxt.strip() == "" and j + 1 < n:
                        nxt2 = lines[j + 1]
                        if self._TABLE_ROW_RE.match(nxt2) and not self._TABLE_SEP_RE.match(nxt2):
                            block.append(nxt)
                            j += 1
                        else:
                            break
                    else:
                        break
                tokens.append("\n".join(block))
                i = j
            else:
                pending.append(line)
                i += 1

        flush_pending()
        return tokens

    def _split_by_sentences(self, text: str) -> list[str]:
        """按句子边界分块，保持重叠。

        句子边界：。！？；\n。V1.2.3：连续表格块作为原子单元不拆分，
        重叠按"整 token"回退，表格块不会被拦腰截断。
        """
        tokens = self._tokenize_protected(text)
        if not tokens:
            return [text]

        chunks: list[str] = []
        current = ""
        current_parts: list[str] = []

        for token in tokens:
            if len(current) + len(token) > self._CHUNK_SIZE and current:
                chunks.append(current.strip())
                # 重叠：从 current 末尾按整 token 回退，最多保留 OVERLAP 字符
                take = 0
                acc = 0
                for t in reversed(current_parts):
                    if acc + len(t) > self._CHUNK_OVERLAP:
                        break
                    acc += len(t)
                    take += 1
                if take == 0 and current_parts:
                    take = 1  # 至少带上前一个 token（可能是整张表格），避免上下文断裂
                current = "".join(current_parts[len(current_parts) - take:])
                current_parts = current_parts[len(current_parts) - take:]
            current += token
            current_parts.append(token)

        if current.strip():
            chunks.append(current.strip())

        return chunks

    def _merge_undersized(self, chunks: list[ChunkMeta]) -> list[ChunkMeta]:
        """合并过小的分块到前一个块。"""
        if len(chunks) <= 1:
            return chunks

        merged: list[ChunkMeta] = []
        for chunk in chunks:
            if (
                merged
                and len(chunk.content) < self._MIN_CHUNK_SIZE
                and len(merged[-1].content) + len(chunk.content) <= self._CHUNK_SIZE * 1.5
            ):
                # 合并到前一个块
                merged[-1].content = merged[-1].content + "\n" + chunk.content
                merged[-1].token_count = 0  # 后续重新计算
            else:
                merged.append(chunk)

        return merged
