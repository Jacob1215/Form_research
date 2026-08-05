"""向量存储服务：基于 pgvector 的文档分块存储与语义检索。

提供：
- 分块存储（批量写入，幂等替换）
- 余弦相似度语义搜索
- 分块删除与统计
"""
import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from .models import DocumentChunk

logger = logging.getLogger("app.vector_store")


class VectorStore:
    """管理文档分块的向量存储与检索。"""

    def __init__(self, db: Session):
        self.db = db

    # ============================================================
    # 写入操作
    # ============================================================

    def store_chunks(
        self,
        doc_id: int,
        kb_id: int,
        chunks: list[dict],
    ) -> int:
        """批量存储文档分块（幂等：先删后写）。

        每个 chunk 字典应包含：
        - index: int      分块序号
        - content: str    分块文本
        - embedding: list[float] | None  嵌入向量
        - token_count: int (可选)
        - section_title: str | None (可选，V1.2.3 所属章节标题)
        """
        # 删除旧分块（幂等）
        self.delete_chunks(doc_id)

        count = 0
        for c in chunks:
            embedding = c.get("embedding")
            # pgvector 期望 list 或 ndarray，不能传字符串
            vec_value = embedding if (embedding is not None and len(embedding) > 0) else None

            chunk = DocumentChunk(
                doc_id=doc_id,
                kb_id=kb_id,
                chunk_index=c["index"],
                content=c["content"],
                token_count=c.get("token_count", 0),
                section_title=c.get("section_title"),
                embedding=vec_value,
            )
            self.db.add(chunk)
            count += 1

        self.db.commit()
        logger.info("已存储 %d 个分块: doc_id=%d kb_id=%d", count, doc_id, kb_id)
        return count

    def delete_chunks(self, doc_id: int) -> int:
        """删除文档的所有分块。"""
        result = self.db.execute(
            text("DELETE FROM document_chunks WHERE doc_id = :doc_id"),
            {"doc_id": doc_id},
        )
        self.db.commit()
        deleted = result.rowcount
        if deleted:
            logger.info("已删除 %d 个分块: doc_id=%d", deleted, doc_id)
        return deleted

    def delete_kb_chunks(self, kb_id: int) -> int:
        """删除知识库的所有分块。"""
        result = self.db.execute(
            text("DELETE FROM document_chunks WHERE kb_id = :kb_id"),
            {"kb_id": kb_id},
        )
        self.db.commit()
        return result.rowcount

    # ============================================================
    # 查询操作
    # ============================================================

    def chunk_count(self, kb_id: int, doc_ids: list[int] | None = None) -> int:
        """获取知识库中已向量化的分块总数（V1.2.5：doc_ids 非空时仅统计指定文档）。"""
        where = "WHERE kb_id = :kb_id AND embedding IS NOT NULL"
        params: dict = {"kb_id": kb_id}
        if doc_ids:
            where += " AND doc_id = ANY(:doc_ids)"
            params["doc_ids"] = doc_ids
        return (
            self.db.execute(
                text(f"SELECT COUNT(*) FROM document_chunks {where}"),
                params,
            ).scalar()
            or 0
        )

    def vector_search(
        self,
        kb_id: int,
        query_embedding: list[float],
        top_k: int = 10,
        score_threshold: float = 0.3,
        doc_ids: list[int] | None = None,
    ) -> list[tuple[int, int, str, float, str | None, str | None]]:
        """余弦相似度搜索。

        V1.2.5：doc_ids 非空时仅检索指定文档（WHERE 加 dc.doc_id = ANY(:doc_ids)）。

        返回: list[(doc_id, chunk_index, content, similarity, file_name, section_title)]
        按相似度降序排列。

        使用 pgvector 的 <=> 余弦距离运算符：
        similarity = 1 - (<=>)  → 范围 [0, 2]，越高越相似
        """
        if not query_embedding:
            return []

        dim = len(query_embedding)
        embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

        # V1.2.5：doc_ids 非空时追加文档过滤（= ANY(:doc_ids) 传 Python list → PG array）
        doc_filter = " AND dc.doc_id = ANY(:doc_ids)" if doc_ids else ""
        params: dict = {
            "query_vec": embedding_str,
            "kb_id": kb_id,
            "top_k": top_k,
            "threshold": score_threshold,
        }
        if doc_ids:
            params["doc_ids"] = doc_ids

        try:
            sql = text(f"""
                SELECT
                    dc.doc_id,
                    dc.chunk_index,
                    dc.content,
                    1 - (dc.embedding <=> :query_vec) AS similarity,
                    d.file_name,
                    dc.section_title
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.doc_id
                WHERE dc.kb_id = :kb_id
                  AND dc.embedding IS NOT NULL
                  AND 1 - (dc.embedding <=> :query_vec) > :threshold{doc_filter}
                ORDER BY dc.embedding <=> :query_vec
                LIMIT :top_k
            """)
            result = self.db.execute(
                sql,
                params,
            )
            rows = result.fetchall()

            results = [
                (
                    row.doc_id, row.chunk_index, row.content,
                    float(row.similarity), row.file_name, row.section_title,
                )
                for row in rows
            ]
            logger.info(
                "向量检索完成: kb_id=%d top_k=%d results=%d",
                kb_id, top_k, len(results),
            )
            return results

        except Exception as e:
            logger.warning("向量检索异常: %s", e)
            return []

    def get_unvectorized_count(self, kb_id: int) -> int:
        """获取知识库中尚未向量化的分块数。"""
        return (
            self.db.execute(
                text(
                    "SELECT COUNT(*) FROM document_chunks "
                    "WHERE kb_id = :kb_id AND embedding IS NULL"
                ),
                {"kb_id": kb_id},
            ).scalar()
            or 0
        )

    def get_doc_chunks(self, doc_id: int) -> list[DocumentChunk]:
        """获取文档的所有分块（按序号排序）。"""
        from sqlalchemy import select

        return list(
            self.db.execute(
                select(DocumentChunk)
                .where(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
            ).scalars().all()
        )
