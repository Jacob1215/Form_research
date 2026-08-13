"""ppt-master 容器 HTTP 客户端（V1.2.6）。

后端与 ppt-master 容器的唯一通信层：提交生成任务、轮询状态、下载产物。
容器接口见 ppt-master/server.py。
"""
import httpx
import logging

from .config import settings

logger = logging.getLogger("app.ppt_master_client")

BASE_URL = settings.PPT_MASTER_API_URL.rstrip("/")

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0)


def _post(path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    try:
        resp = httpx.post(url, json=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        logger.error("ppt-master 请求失败 %s: %s", url, e)
        raise RuntimeError(f"无法连接 ppt-master 服务：{e}") from e
    if resp.status_code >= 400:
        detail = resp.text[:300]
        logger.error("ppt-master 响应异常 %s (%s): %s", url, resp.status_code, detail)
        raise RuntimeError(f"ppt-master 服务错误：{detail}")
    return resp.json()


def generate(brief: str, title: str, base_url: str, api_key: str, model: str) -> str:
    """提交生成任务，返回 task_id。"""
    data = _post("/generate", {
        "brief": brief,
        "title": title,
        "base_url": base_url,
        "api_key": api_key,
        "model": model,
    })
    return data["task_id"]


def get_task(task_id: str) -> dict:
    """查询任务状态，返回 {status, message}。"""
    url = f"{BASE_URL}/tasks/{task_id}"
    try:
        resp = httpx.get(url, timeout=_TIMEOUT)
    except httpx.HTTPError as e:
        logger.error("ppt-master 查询失败 %s: %s", url, e)
        raise RuntimeError(f"无法连接 ppt-master 服务：{e}") from e
    if resp.status_code >= 400:
        raise RuntimeError(f"ppt-master 服务错误：{resp.text[:300]}")
    return resp.json()


def download(task_id: str) -> bytes:
    """下载生成的 .pptx 字节。"""
    url = f"{BASE_URL}/tasks/{task_id}/file"
    try:
        resp = httpx.get(url, timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=10.0))
    except httpx.HTTPError as e:
        logger.error("ppt-master 下载失败 %s: %s", url, e)
        raise RuntimeError(f"下载 PPT 失败：{e}") from e
    if resp.status_code >= 400:
        raise RuntimeError(f"下载 PPT 失败：{resp.text[:300]}")
    return resp.content
