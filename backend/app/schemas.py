"""Pydantic 请求/响应模型。日期统一以 ISO 字符串输出。"""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, ConfigDict, Field


def _to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


# ---------- 通用包装 ----------

class ItemsResponse(BaseModel):
    items: list[Any]


# ---------- 知识库 ----------

class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    description: Optional[str] = None
    doc_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            name=obj.name,
            description=obj.description,
            doc_count=obj.doc_count,
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: Optional[str] = None


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


# ---------- LLM 配置 ----------

class LLMConfigOut(BaseModel):
    id: int
    name: str
    provider: str
    api_url: str
    api_key_masked: str
    model_name: str
    temperature: float
    max_tokens: int
    timeout: int
    is_active: bool
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_orm_with_key(cls, obj, masked_key: str):
        return cls(
            id=obj.id,
            name=obj.name,
            provider=obj.provider,
            api_url=obj.api_url,
            api_key_masked=masked_key,
            model_name=obj.model_name,
            temperature=obj.temperature,
            max_tokens=obj.max_tokens,
            timeout=obj.timeout,
            is_active=obj.is_active,
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class LLMConfigCreate(BaseModel):
    name: str
    provider: str
    api_url: str
    api_key: str
    model_name: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 2048
    timeout: Optional[int] = 30
    is_active: Optional[bool] = False


class LLMConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None  # 可选：为空则保留原 key
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[int] = None
    is_active: Optional[bool] = None


class LLMTestRequest(BaseModel):
    provider: str
    api_url: str
    api_key: str
    model_name: str


class LLMTestResponse(BaseModel):
    success: bool
    message: str


# ---------- 文档 ----------

class DocumentOut(BaseModel):
    id: int
    kb_id: int
    file_name: str
    file_type: str
    file_size: int
    parse_status: str
    chunk_count: int
    error_message: Optional[str] = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            kb_id=obj.kb_id,
            file_name=obj.file_name,
            file_type=obj.file_type,
            file_size=obj.file_size,
            parse_status=obj.parse_status,
            chunk_count=obj.chunk_count,
            error_message=obj.error_message,
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class DocumentDetail(DocumentOut):
    parsed_text: Optional[str] = None


# ---------- 对话 ----------

class ConversationOut(BaseModel):
    id: int
    kb_id: int
    title: str
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            kb_id=obj.kb_id,
            title=obj.title,
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    references: Optional[Any] = None
    created_at: str | None = None

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            role=obj.role,
            content=obj.content,
            references=obj.references_,
            created_at=_to_iso(obj.created_at),
        )


# ---------- 对话请求 ----------

class ChatRequest(BaseModel):
    kb_id: int
    message: str = Field(..., min_length=1)
    conversation_id: Optional[int] = None


# ---------- 状态 ----------

class StatusResponse(BaseModel):
    llm_configured: bool
    active_model: Optional[str] = None
    mineru_available: bool
