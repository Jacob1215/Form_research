"""管理后台 — 知识库 CRUD。删除时级联清理文档、分块及磁盘文件。"""
import os
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..config import settings
from ..models import KnowledgeBase, Document, DocChunk
from ..schemas import KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseOut

logger = logging.getLogger("app.admin_kb")

router = APIRouter(prefix="/api/admin/knowledge-bases", tags=["admin-kb"])


def _serialize(kb: KnowledgeBase) -> dict:
    return KnowledgeBaseOut.from_orm(kb).model_dump()


def _delete_kb_files(kb_id: int):
    """删除该知识库在磁盘上的上传目录。"""
    kb_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id))
    if not kb_dir or not os.path.isdir(kb_dir):
        return
    try:
        for root, _dirs, files in os.walk(kb_dir, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(root, name))
                except OSError as e:
                    logger.warning("删除文件失败 %s: %s", name, e)
            try:
                os.rmdir(root)
            except OSError:
                pass
    except OSError as e:
        logger.warning("删除知识库目录失败 %s: %s", kb_dir, e)


@router.get("")
def list_knowledge_bases(search: str = "", db: Session = Depends(get_db)):
    stmt = select(KnowledgeBase).order_by(KnowledgeBase.created_at.desc())
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(KnowledgeBase.name.ilike(like), KnowledgeBase.description.ilike(like)))
    rows = db.execute(stmt).scalars().all()
    return {"items": [_serialize(r) for r in rows]}


@router.post("")
def create_knowledge_base(body: KnowledgeBaseCreate, db: Session = Depends(get_db)):
    exists = db.execute(select(KnowledgeBase).where(KnowledgeBase.name == body.name)).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=400, detail="知识库名称已存在")
    kb = KnowledgeBase(name=body.name, description=body.description or "")
    db.add(kb)
    db.commit()
    db.refresh(kb)
    return _serialize(kb)


@router.put("/{kb_id}")
def update_knowledge_base(kb_id: int, body: KnowledgeBaseUpdate, db: Session = Depends(get_db)):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if body.name is not None:
        dup = db.execute(
            select(KnowledgeBase).where(KnowledgeBase.name == body.name, KnowledgeBase.id != kb_id)
        ).scalar_one_or_none()
        if dup:
            raise HTTPException(status_code=400, detail="知识库名称已存在")
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    db.commit()
    db.refresh(kb)
    return _serialize(kb)


@router.delete("/{kb_id}")
def delete_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    # 显式删除分块（防止 FK 与 vector 列带来的级联问题）
    db.execute(DocChunk.__table__.delete().where(DocChunk.kb_id == kb_id))
    db.delete(kb)  # KnowledgeBase 级联删除 Document
    db.commit()
    _delete_kb_files(kb_id)
    return {"success": True}
