"""LLM 提供商抽象层：基于 OpenAI 兼容接口实现流式对话。"""
import json
import logging
from typing import Iterator
import httpx

from .models import LLMConfig

logger = logging.getLogger("app.llm")


class LLMProvider:
    """LLM 提供商抽象基类。"""

    def stream_chat(self, messages: list[dict], config: LLMConfig) -> Iterator[str]:
        raise NotImplementedError

    def test_connection(self, provider: str, api_url: str, api_key: str, model_name: str) -> tuple[bool, str]:
        raise NotImplementedError


class OpenAICompatibleProvider(LLMProvider):
    """OpenAI 兼容协议的 Provider，绝大多数中外厂商都提供该协议端点。"""

    def stream_chat(self, messages: list[dict], config: LLMConfig) -> Iterator[str]:
        url = config.api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": config.model_name,
            "messages": messages,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "stream": True,
        }
        timeout = httpx.Timeout(config.timeout if config.timeout > 0 else 30.0)
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="ignore")
                    raise RuntimeError(f"LLM 接口返回 {resp.status_code}: {body[:500]}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("data:"):
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        content = delta.get("content")
                        if content:
                            yield content

    def test_connection(self, provider: str, api_url: str, api_key: str, model_name: str) -> tuple[bool, str]:
        url = api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
            "stream": False,
        }
        try:
            with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
                resp = client.post(url, headers=headers, json=payload)
            if resp.status_code < 400:
                return True, "连接成功"
            return False, f"HTTP {resp.status_code}: {resp.text[:300]}"
        except Exception as e:  # noqa: BLE001
            return False, f"连接异常：{e}"


def factory(provider: str) -> LLMProvider:
    """根据 provider 字段返回对应实现。所有兼容厂商统一走 OpenAI 协议。"""
    _ = provider.lower()
    return OpenAICompatibleProvider()


def test_connection(provider: str, api_url: str, api_key: str, model_name: str) -> tuple[bool, str]:
    """对外暴露的连通性测试入口。"""
    return factory(provider).test_connection(provider, api_url, api_key, model_name)
