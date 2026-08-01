"""Embedding 向量化服务（V1.0.7：默认使用本地 sentence-transformers 模型）。

优先级：
1. 远程 API（EMBEDDING_API_URL 已配置时优先）—— 无额外本地依赖
2. 本地 sentence-transformers 模型（BAAI/bge-small-zh-v1.5，512 维，已内置于 Docker 镜像）
3. 都不可用时 → 返回 None，上游抛异常提示用户（方案 A 不再降级 BM25）

注意：本地模型使用 PyTorch CPU 版，Dockerfile 已安装 libgomp1 并在构建期预下载模型。
如遇段错误，请改用远程 API（配置 EMBEDDING_API_URL）。
"""
import logging
import os
import threading
from typing import List, Optional

import httpx

from .config import settings

logger = logging.getLogger("app.embedding")

DIM = settings.EMBEDDING_DIM  # 向量维度

# 本地模型状态
_LOCAL_MODEL = None
_MODEL_LOCK = threading.Lock()
_MODEL_LOAD_FAILED = False  # 标记是否曾尝试加载失败
_MODEL_NAME = settings.LOCAL_EMBEDDING_MODEL or "BAAI/bge-small-zh-v1.5"


def _get_local_model():
    """惰性加载 sentence-transformers 模型（线程安全）。

    加载失败只记录日志并标记不可用，绝不抛出异常导致进程崩溃。
    """
    global _LOCAL_MODEL, _MODEL_LOAD_FAILED
    if _LOCAL_MODEL is not None:
        return _LOCAL_MODEL
    if _MODEL_LOAD_FAILED:
        return None
    with _MODEL_LOCK:
        if _LOCAL_MODEL is not None:
            return _LOCAL_MODEL
        if _MODEL_LOAD_FAILED:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("正在加载本地 embedding 模型: %s", _MODEL_NAME)

            # 设置环境变量以减少 PyTorch 线程冲突
            os.environ.setdefault("OMP_NUM_THREADS", "1")
            os.environ.setdefault("MKL_NUM_THREADS", "1")

            model = SentenceTransformer(_MODEL_NAME, device=settings.EMBEDDING_DEVICE)
            actual_dim = model.get_sentence_embedding_dimension()
            if actual_dim is not None and actual_dim != DIM:
                logger.warning(
                    "模型维度 %d 与配置 EMBEDDING_DIM=%d 不匹配！"
                    "请在 .env 中设置 EMBEDDING_DIM=%d",
                    actual_dim, DIM, actual_dim,
                )
            logger.info(
                "本地 embedding 模型加载成功: %s（%d 维）",
                _MODEL_NAME, actual_dim or DIM,
            )
            _LOCAL_MODEL = model
            return model
        except ImportError:
            _MODEL_LOAD_FAILED = True
            logger.warning(
                "sentence-transformers 未安装，本地向量检索不可用。"
                "如需启用，请执行: pip install sentence-transformers"
            )
            return None
        except Exception as e:
            _MODEL_LOAD_FAILED = True
            logger.warning(
                "本地 embedding 模型加载失败: %s。"
                "向量检索已禁用，系统将使用纯 BM25 关键词检索。"
                "如需启用向量检索，请配置远程 EMBEDDING_API_URL 或排查本地模型加载问题。",
                e,
            )
            return None


def _local_embed(texts: list[str]) -> Optional[list[list[float]]]:
    """使用本地 sentence-transformers 模型生成向量。

    返回 None 表示本地模型不可用。
    """
    if not texts:
        return []
    model = _get_local_model()
    if model is None:
        return None
    try:
        embeddings = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return embeddings.tolist()
    except Exception as e:
        logger.warning("本地 embedding 编码失败: %s", e)
        return None


def _remote_embed(texts: list[str]) -> Optional[list[list[float]]]:
    """调用 OpenAI 兼容 /embeddings 接口。失败返回 None。"""
    if not settings.EMBEDDING_API_URL:
        return None
    url = settings.EMBEDDING_API_URL.rstrip("/")
    if not url.endswith("/embeddings"):
        url = f"{url}/embeddings"
    headers = {
        "Authorization": f"Bearer {settings.EMBEDDING_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": settings.EMBEDDING_MODEL,
        "input": texts,
    }
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
            resp = client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            logger.warning("Embedding 远程接口返回 %s: %s", resp.status_code, resp.text[:300])
            return None
        data = resp.json()
        items = data.get("data") or []
        items.sort(key=lambda x: x.get("index", 0))
        vectors = [item.get("embedding") for item in items]
        if any(v is None for v in vectors):
            logger.warning("Embedding 远程返回缺失向量")
            return None
        if vectors and len(vectors[0]) != DIM:
            logger.warning(
                "Embedding 远程返回维度 %d 与配置 %d 不符",
                len(vectors[0]), DIM,
            )
            return None
        return vectors
    except Exception as e:
        logger.warning("Embedding 远程调用异常: %s", e)
        return None


class EmbeddingService:
    """Embedding 服务门面。

    策略（按优先级）：
    1. 远程 API（EMBEDDING_API_URL 已配置）
    2. 本地 sentence-transformers 模型（如已安装且加载成功）
    3. 返回 None → 上游自动降级为纯 BM25 检索
    """

    def embed(self, texts: list[str]) -> Optional[list[list[float]]]:
        """批量生成嵌入向量。返回 None 表示向量化不可用。"""
        if not texts:
            return []

        # 远程优先
        if settings.EMBEDDING_API_URL:
            remote = _remote_embed(texts)
            if remote is not None:
                return remote
            logger.info("远程 embedding 不可用，尝试本地模型")

        # 本地模型
        local = _local_embed(texts)
        if local is not None:
            return local

        logger.warning(
            "向量化服务不可用（本地模型未安装且未配置远程 API），"
            "系统将使用纯 BM25 关键词检索"
        )
        return None

    def embed_one(self, text: str) -> Optional[list[float]]:
        """单个文本生成嵌入向量。返回 None 表示不可用。"""
        result = self.embed([text])
        return result[0] if result else None

    def is_available(self) -> bool:
        """检查向量化服务是否可用。"""
        if settings.EMBEDDING_API_URL:
            return True
        return _get_local_model() is not None


# 全局单例
embedding_service = EmbeddingService()
