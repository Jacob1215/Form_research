"""应用配置：通过环境变量注入，使用 pydantic-settings 管理。"""
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # 数据库连接
    DATABASE_URL: str = "postgresql://app:app@db:5432/qa"

    # API 密钥加密用的 Fernet key；未设置时派生一个默认 key 并告警
    LLM_ENCRYPT_KEY: str = ""

    # MinerU 解析服务地址（可选）
    MINERU_API_URL: str = ""

    # Embedding 服务配置
    EMBEDDING_API_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 512  # 向量维度，需与模型输出匹配
    EMBEDDING_USE_LOCAL: bool = True  # 优先使用本地 sentence-transformers 模型
    EMBEDDING_DEVICE: str = "cpu"  # 推理设备：cpu / cuda
    LOCAL_EMBEDDING_MODEL: str = "BAAI/bge-small-zh-v1.5"  # 本地模型名（512维，中文优化）

    # RAG 检索配置
    ENABLE_QUERY_REWRITE: bool = True  # LLM 查询改写
    ENABLE_RERANK: bool = False  # Cross-encoder 重排序（需额外模型）
    # V1.0.7 检索模式：vector_only（方案 A，纯向量）/ hybrid（BM25+向量融合）/ bm25_fallback（纯 BM25）
    RETRIEVE_MODE: str = "vector_only"
    HYBRID_BM25_WEIGHT: float = 0.3  # RRF 融合中 BM25 权重（仅 hybrid 模式生效）
    HYBRID_VECTOR_WEIGHT: float = 0.7  # RRF 融合中向量检索权重（仅 hybrid 模式生效）

    # 上传文件目录
    UPLOAD_DIR: str = "/app/uploads"

    # 静态资源目录（前端 dist）
    STATIC_DIR: str = ""

    # CORS 允许来源
    CORS_ORIGINS: str = "*"

    # 文档上传限制（V1.1：支持更大文档/更多图片）
    MAX_UPLOAD_FILES: int = 50
    MAX_UPLOAD_SIZE_MB: int = 100

    # ---------- 报告总结功能配置（V1.1+） ----------

    # 单次上传图片/文档数量与单文件大小限制
    REPORT_MAX_IMAGES: int = 20
    REPORT_MAX_IMAGE_SIZE_MB: int = 20

    # 上传文档注入 LLM 上下文的字符上限
    # （调小可防止超出模型上下文导致空响应；调大可纳入更长文档，需模型上下文足够大）
    REPORT_DOC_TEXT_CAP: int = 80000

    # 大文档分块读取（文档超过 REPORT_DOC_TEXT_CAP 时启用 map-reduce）
    REPORT_CHUNK_SIZE: int = 8000        # 每块字符数
    REPORT_MAX_CHUNKS: int = 80          # 单文档最大块数（溢出时保留首尾块，确保靠后章节覆盖）
    REPORT_CHUNK_SUMMARY_CAP: int = 30000  # 合并后要点汇总上限（超限时首尾保留，不丢末尾章节）

    # V1.1.3：报告 skill 库 — 注入 system prompt 的指令块总长上限
    REPORT_SKILLS_MAX_CHARS: int = 24000

    # 报告生成输出 token 上限：取 max(REPORT_MAX_TOKENS, LLM 配置 max_tokens)
    # 防止完整报告/要点输出被后台 max_tokens 限制
    REPORT_MAX_TOKENS: int = 4096


settings = Settings()


def get_encrypt_key() -> bytes:
    """获取 Fernet key。未配置时派生一个固定默认 key（仅用于本地开发）。"""
    if settings.LLM_ENCRYPT_KEY:
        return settings.LLM_ENCRYPT_KEY.encode("utf-8")
    # 本地开发降级：基于固定盐值生成一个 Fernet key
    import hashlib
    from cryptography.fernet import Fernet  # 局部导入避免循环依赖
    digest = hashlib.sha256(b"form-research-dev-encrypt-key-do-not-use-in-prod").digest()
    key = digest  # Fernet 需要 urlsafe base64 32 字节 key
    import base64
    fernet_key = base64.urlsafe_b64encode(key)
    logger.warning(
        "LLM_ENCRYPT_KEY 未设置，已使用默认开发密钥。生产环境必须通过环境变量 LLM_ENCRYPT_KEY 显式提供 Fernet key。"
    )
    return fernet_key
