"""应用配置：通过环境变量注入，使用 pydantic-settings 管理。"""
import logging
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger("app.config")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        # V1.2.5：空值 env 视为未设置 → 默认值单一来源在 config.py。
        # docker-compose 只做 ${VAR:-} 纯透传，.env 没配的键落回本类默认值，
        # 避免 int/float/bool 字段被空串校验炸掉。
        env_ignore_empty=True,
    )

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
    # V1.2.3 检索模式：hybrid（BM25+向量 RRF 融合，默认）/ vector_only（纯向量）/ bm25_fallback（纯 BM25）
    # V1.0.7 曾默认 vector_only，导致"分级标准表"这类精确表名查询难命中，故 V1.2.3 切回混合。
    RETRIEVE_MODE: str = "hybrid"
    # V1.2.5：RRF 权重默认语义偏重（0.35/0.65），降低 BM25 关键字重复块的刷榜影响。
    # V1.2.3 曾因 0.3/0.7 让 BM25 第 1 名贡献 0.3/61≈0.0049 低于向量第 15 名 0.7/76≈0.0092，
    # 导致纯字面命中块永远进不了 top-5；现回归语义偏重是权衡结果，精确字面命中
    # 靠整句提权（SUBSTRING_PHRASE_WEIGHT）兜底。可 .env 回退 0.4/0.6 或 0.5/0.5。
    HYBRID_BM25_WEIGHT: float = 0.35
    HYBRID_VECTOR_WEIGHT: float = 0.65

    # V1.2.3：BGE 查询指令（仅查询侧加前缀，文档侧不加，无需重索引）
    # BAAI/bge-small-zh-v1.5 官方要求查询侧加此前缀以提升短查询/同义查询召回；
    # 空串表示禁用。
    EMBEDDING_QUERY_PROMPT: str = "为这个句子生成表示以用于检索相关文章："
    # 命中这些模型名子串才应用查询指令（逗号分隔，大小写不敏感）；
    # 仅 bge 系需要，远程 text-embedding-3 等模型应排除。
    EMBEDDING_QUERY_PROMPT_MODELS: str = "bge"

    # ---------- V1.2.4：对话检索召回率调优 ----------
    # 各检索调用方默认 top_k（chat/report/ppt 之前硬编码 5；8 块约 5k 字符注入 LLM）
    RAG_TOP_K: int = 8
    # 向量相似度阈值（原 vector_store 默认 0.3；放宽提升精确表名/条款号召回）
    VECTOR_SCORE_THRESHOLD: float = 0.2
    # 子串/字面命中增强总开关：分词级子串仅补漏、整句字面命中才提权并入 BM25 候选池
    ENABLE_SUBSTRING_BOOST: bool = True
    # 原始整句精确命中的权重乘数（对齐预览整串子串高亮）；0 = 不提升整句
    SUBSTRING_PHRASE_WEIGHT: float = 5.0
    # 融合后字面命中强制回插开关（默认关；仅当验证后仍有具体查询漏掉字面块时打开）
    ENABLE_LITERAL_FORCE_INJECT: bool = False

    # 上传文件目录
    UPLOAD_DIR: str = "/app/uploads"

    # 静态资源目录（前端 dist）
    STATIC_DIR: str = ""

    # CORS 允许来源
    CORS_ORIGINS: str = "*"

    # 文档上传限制（V1.1：支持更大文档/更多图片）
    MAX_UPLOAD_FILES: int = 50
    MAX_UPLOAD_SIZE_MB: int = 100

    # V1.2.3：大文档入库文本上限（字符）。公路隧道设计规范等大规范远超 10 万字符，
    # 硬截断会丢掉靠后章节/表格（表现为"查不到靠后内容"）；按机器内存可调大。
    DOC_TEXT_CAP: int = 500000
    # 单文档最大分块数（配合 DOC_TEXT_CAP，避免"块数截断"再次丢尾部；500k 字符约 830 块）
    CHUNK_MAX_COUNT: int = 1500

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

    # ---------- PPT 制作功能配置（V1.2.1+） ----------

    # PPT 输出 token 上限：取 max(PPT_MAX_TOKENS, LLM 配置 max_tokens)，防止完整演示文稿被截断
    PPT_MAX_TOKENS: int = 4096

    # PPT skill 库 — 注入 system prompt 的指令块总长上限
    PPT_SKILLS_MAX_CHARS: int = 24000


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
