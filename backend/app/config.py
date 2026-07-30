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

    # Embedding 服务配置（可选，未配置则使用本地哈希降级方案）
    EMBEDDING_API_URL: str = ""
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_MODEL: str = ""
    EMBEDDING_DIM: int = 384

    # 上传文件目录
    UPLOAD_DIR: str = "/app/uploads"

    # 静态资源目录（前端 dist）
    STATIC_DIR: str = ""

    # CORS 允许来源
    CORS_ORIGINS: str = "*"

    # 文档上传限制
    MAX_UPLOAD_FILES: int = 20
    MAX_UPLOAD_SIZE_MB: int = 50


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
