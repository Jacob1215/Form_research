"""Embedding 服务：优先调用 OpenAI 兼容 /embeddings 接口，否则降级为本地哈希向量。"""
import logging
import hashlib
import math
import re
from typing import List

import httpx

from .config import settings

logger = logging.getLogger("app.embedding")

DIM = settings.EMBEDDING_DIM or 384


def _tokenize(text: str) -> list[str]:
    """简单分词：英文按空白，中文按字符。"""
    tokens: list[str] = []
    if not text:
        return tokens
    # 先按空白切分英文片段
    for part in re.split(r"\s+", text):
        if not part:
            continue
        # 若整段都是非 CJK 字符，作为整体 token
        if re.fullmatch(r"[\x00-\x7F]+", part):
            tokens.append(part.lower())
        else:
            # 中文按单字切，同时保留其中的英文子串
            buf = ""
            for ch in part:
                if "\u4e00" <= ch <= "\u9fff":
                    if buf:
                        tokens.append(buf.lower())
                        buf = ""
                    tokens.append(ch)
                else:
                    buf += ch
            if buf:
                tokens.append(buf.lower())
    return tokens


def _local_embed_one(text: str) -> list[float]:
    """基于 SHA256 哈希的确定性 384 维向量，L2 归一化。"""
    vec = [0.0] * DIM
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        # 取前 4 字节作为桶索引（mod DIM）
        idx = int.from_bytes(h[:4], "big") % DIM
        # 用前 4 字节再派生一个 [-1,1] 的有符号权重
        sign_val = int.from_bytes(h[4:8], "big") / 0xFFFFFFFF  # 0~1
        weight = (sign_val * 2.0) - 1.0
        vec[idx] += weight
    # L2 归一化
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def _local_embed(texts: list[str]) -> list[list[float]]:
    return [_local_embed_one(t) for t in texts]


def _remote_embed(texts: list[str]) -> list[list[float]] | None:
    """调用 OpenAI 兼容 /embeddings 接口，失败或维度不符返回 None。"""
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
            logger.warning("Embedding 远程返回缺失向量。")
            return None
        if vectors and len(vectors[0]) != DIM:
            logger.warning(
                "Embedding 远程返回维度 %d 与期望 %d 不符，降级本地实现。",
                len(vectors[0]), DIM,
            )
            return None
        return vectors
    except Exception as e:  # noqa: BLE001
        logger.warning("Embedding 远程调用异常：%s，降级本地实现。", e)
        return None


class EmbeddingService:
    """对外暴露的 Embedding 服务：优先远程，失败降级本地。"""

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        remote = _remote_embed(texts)
        if remote is not None:
            return remote
        return _local_embed(texts)

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


# 单例
embedding_service = EmbeddingService()
