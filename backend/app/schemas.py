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
    context_window: int = 64000
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
            context_window=getattr(obj, "context_window", None) or 64000,
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
    context_window: Optional[int] = 64000
    timeout: Optional[int] = 30
    is_active: Optional[bool] = False


class LLMConfigUpdate(BaseModel):
    name: Optional[str] = None
    provider: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    model_name: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    context_window: Optional[int] = None
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
    relative_path: str | None = None
    parse_status: str = "pending"
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
            relative_path=getattr(obj, "relative_path", None),
            parse_status=getattr(obj, "parse_status", "pending") or "pending",
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class DocumentDetail(DocumentOut):
    content_text: Optional[str] = None
    parsed_content: Optional[str] = None


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
    # V1.1.1：kb_id 允许为空（不选知识库对话）
    kb_id: Optional[int] = None
    message: str = Field(..., min_length=1)
    conversation_id: Optional[int] = None


# ---------- 状态 ----------

class StatusResponse(BaseModel):
    llm_configured: bool
    active_model: Optional[str] = None


# ---------- 报告总结（V1.1+） ----------

class ReportDocRef(BaseModel):
    """上传的参考文档引用。"""
    url: str
    name: str = ""


class ReportMessage(BaseModel):
    """报告对话中的一条消息。user 消息可附带上传图片/文档引用。"""
    role: str = "user"  # user / assistant
    content: str = ""
    images: Optional[list[str]] = None
    documents: Optional[list[ReportDocRef]] = None


class ReportChatRequest(BaseModel):
    # V1.1.1：kb_id 允许为空（不选知识库，纯资料编制）
    kb_id: Optional[int] = None
    title: Optional[str] = None
    # V1.1.3：本次生成选用的 skill 名称列表（未选则为空/None，不注入技能指令）
    skills: Optional[list[str]] = None
    messages: list[ReportMessage] = Field(..., min_length=1)


class ReportExportRequest(BaseModel):
    title: Optional[str] = None
    content: str = Field(..., min_length=1)


# ---------- 报告记录（V1.1：手动保存） ----------

class ReportRecordCreate(BaseModel):
    title: Optional[str] = None
    content: str = Field(..., min_length=1)


class ReportRecordOut(BaseModel):
    id: int
    title: str
    kb_id: Optional[int] = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=obj.id,
            title=obj.title,
            kb_id=obj.kb_id,
            created_at=_to_iso(obj.created_at),
            updated_at=_to_iso(obj.updated_at),
        )


class ReportRecordDetail(ReportRecordOut):
    content: str


# ---------- PPT 制作（V1.2.1+） ----------

class PptChatRequest(BaseModel):
    # kb_id 允许为空（不选知识库，纯文本编制）
    kb_id: Optional[int] = None
    title: Optional[str] = None
    # 本次生成选用的 PPT skill 名称列表（未选则为空/None，不注入技能指令）
    skills: Optional[list[str]] = None
    messages: list[ReportMessage] = Field(..., min_length=1)


class PptExportRequest(BaseModel):
    title: Optional[str] = None
    content: str = Field(..., min_length=1)


class PptRecordCreate(BaseModel):
    title: Optional[str] = None
    content: str = Field(..., min_length=1)
    question: Optional[str] = None
