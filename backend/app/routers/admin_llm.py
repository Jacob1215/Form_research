"""管理后台 — LLM 配置 CRUD 与连通性测试。"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import LLMConfig
from ..schemas import (
    LLMConfigCreate, LLMConfigUpdate, LLMConfigOut,
    LLMTestRequest, LLMTestResponse,
)
from ..security import encrypt, decrypt, mask
from ..llm_provider import test_connection as _test_connection

logger = logging.getLogger("app.admin_llm")

router = APIRouter(prefix="/api/admin/llm-configs", tags=["admin-llm"])


def _serialize(cfg: LLMConfig) -> dict:
    plain = decrypt(cfg.api_key_enc)
    return LLMConfigOut.from_orm_with_key(cfg, mask(plain)).model_dump()


@router.get("")
def list_configs(db: Session = Depends(get_db)):
    rows = db.execute(select(LLMConfig).order_by(LLMConfig.created_at.desc())).scalars().all()
    return {"items": [_serialize(r) for r in rows]}


@router.post("")
def create_config(body: LLMConfigCreate, db: Session = Depends(get_db)):
    # 名称唯一性
    exists = db.execute(select(LLMConfig).where(LLMConfig.name == body.name)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="配置名称已存在")

    cfg = LLMConfig(
        name=body.name,
        provider=body.provider,
        api_url=body.api_url,
        api_key_enc=encrypt(body.api_key),
        model_name=body.model_name,
        temperature=body.temperature if body.temperature is not None else 0.7,
        max_tokens=body.max_tokens if body.max_tokens is not None else 2048,
        context_window=body.context_window if body.context_window is not None else 64000,
        timeout=body.timeout if body.timeout is not None else 30,
        is_active=bool(body.is_active),
    )
    db.add(cfg)
    db.flush()

    if cfg.is_active:
        _deactivate_others(db, exclude_id=cfg.id)

    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


@router.put("/{cfg_id}")
def update_config(cfg_id: int, body: LLMConfigUpdate, db: Session = Depends(get_db)):
    cfg = db.get(LLMConfig, cfg_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")

    if body.name is not None:
        dup = db.execute(
            select(LLMConfig).where(LLMConfig.name == body.name, LLMConfig.id != cfg_id)
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="配置名称已存在")
        cfg.name = body.name
    if body.provider is not None:
        cfg.provider = body.provider
    if body.api_url is not None:
        cfg.api_url = body.api_url
    if body.model_name is not None:
        cfg.model_name = body.model_name
    if body.temperature is not None:
        cfg.temperature = body.temperature
    if body.max_tokens is not None:
        cfg.max_tokens = body.max_tokens
    if body.context_window is not None:
        cfg.context_window = body.context_window
    if body.timeout is not None:
        cfg.timeout = body.timeout
    # api_key 为空表示保留原 key
    if body.api_key is not None and body.api_key != "":
        cfg.api_key_enc = encrypt(body.api_key)

    if body.is_active is True:
        _deactivate_others(db, exclude_id=cfg.id)
        cfg.is_active = True
    elif body.is_active is False:
        cfg.is_active = False

    db.commit()
    db.refresh(cfg)
    return _serialize(cfg)


@router.delete("/{cfg_id}")
def delete_config(cfg_id: int, db: Session = Depends(get_db)):
    cfg = db.get(LLMConfig, cfg_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail="配置不存在")
    db.delete(cfg)
    db.commit()
    return {"success": True}


@router.post("/test")
def test_config(body: LLMTestRequest):
    success, msg = _test_connection(body.provider, body.api_url, body.api_key, body.model_name)
    return LLMTestResponse(success=success, message=msg).model_dump()


def _deactivate_others(db: Session, exclude_id: int):
    rows = db.execute(
        select(LLMConfig).where(LLMConfig.is_active == True, LLMConfig.id != exclude_id)  # noqa: E712
    ).scalars().all()
    for r in rows:
        r.is_active = False
