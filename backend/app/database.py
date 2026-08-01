"""数据库引擎、会话工厂与初始化逻辑。"""
import time
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

from .config import settings
from .models import Base

logger = logging.getLogger("app.database")

# pool_pre_ping 防止连接断开后报错；连接池大小适中应对 50 并发
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db():
    """FastAPI 依赖：提供 DB 会话并在请求结束时关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db(max_wait_seconds: int = 30) -> None:
    """启动时初始化数据库：
    1. 等待数据库就绪（带重试）；
    2. 启用 pgvector 扩展；
    3. 创建所有表；
    4. 自动迁移。
    """
    deadline = time.time() + max_wait_seconds
    last_err: Exception | None = None
    while time.time() < deadline:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            last_err = None
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("数据库未就绪：%s，3 秒后重试...", e)
            time.sleep(3)
    if last_err is not None:
        raise RuntimeError(f"无法连接数据库：{last_err}")

    # 启用 pgvector 扩展
    _ensure_pgvector_extension(engine)

    Base.metadata.create_all(bind=engine)

    # 自动迁移：补充新列/新表
    _auto_migrate(engine)

    logger.info("数据库初始化完成。")


def _ensure_pgvector_extension(engine) -> None:
    """启用 pgvector 扩展（幂等操作）。"""
    try:
        with engine.connect() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            conn.commit()
        logger.info("pgvector 扩展已启用。")
    except Exception as e:
        logger.warning("pgvector 扩展启用失败: %s，向量检索将不可用。", e)


def _auto_migrate(engine) -> None:
    """检查并添加缺失的列和表。"""
    with engine.connect() as conn:
        # 补充 documents 表的新列
        for col, col_type, default in [
            ("parse_status", "VARCHAR(32)", "'pending'"),
            ("parsed_content", "TEXT", "NULL"),
        ]:
            exists = conn.execute(text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='documents' AND column_name=:col"
            ), {"col": col}).first()
            if not exists:
                conn.execute(text(
                    f"ALTER TABLE documents ADD COLUMN {col} {col_type} DEFAULT {default}"
                ))
                logger.info("已添加列: documents.%s", col)

        # 检查并创建 document_chunks 表
        table_exists = conn.execute(text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'document_chunks'"
        )).first()
        if not table_exists:
            conn.execute(text("""
                CREATE TABLE document_chunks (
                    id SERIAL PRIMARY KEY,
                    doc_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                    kb_id INTEGER NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    embedding vector(512)
                )
            """))
            logger.info("已创建表: document_chunks")

            # 创建索引
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(doc_id)"
            ))
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_chunks_kb_id ON document_chunks(kb_id)"
            ))
            logger.info("已创建 document_chunks 索引。")

        # 检查并创建 HNSW 向量索引（pgvector 0.5.0+）
        try:
            idx_exists = conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = 'idx_chunks_embedding'"
            )).first()
            if not idx_exists:
                # 先检查是否有数据，HNSW 需要非空表
                count = conn.execute(text("SELECT COUNT(*) FROM document_chunks")).scalar() or 0
                if count > 0:
                    conn.execute(text(
                        "CREATE INDEX idx_chunks_embedding ON document_chunks "
                        "USING hnsw (embedding vector_cosine_ops)"
                    ))
                else:
                    conn.execute(text(
                        "CREATE INDEX idx_chunks_embedding ON document_chunks "
                        "USING hnsw (embedding vector_cosine_ops) WITH (ef_construction = 64)"
                    ))
                conn.commit()
                logger.info("已创建 HNSW 向量索引: idx_chunks_embedding")
        except Exception as e:
            logger.warning("HNSW 索引创建失败（可能 pgvector 版本过低）: %s", e)

        conn.commit()
