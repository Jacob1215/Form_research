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
    2. 创建 pgvector 扩展；
    3. 创建所有表。
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

    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()

    Base.metadata.create_all(bind=engine)
    logger.info("数据库初始化完成。")
