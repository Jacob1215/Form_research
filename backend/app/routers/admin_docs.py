"""管理后台 — 文档上传/列表/详情/删除/重解析/下载。"""
import os
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db, SessionLocal
from ..config import settings
from ..models import KnowledgeBase, Document, DocChunk
from ..schemas import DocumentOut, DocumentDetail
from ..parsing_service import parse_file
from ..rag_service import chunk_text
from ..embedding_service import embedding_service

logger = logging.getLogger("app.admin_docs")

router = APIRouter(prefix="/api/admin", tags=["admin-docs"])

ALLOWED_EXT = {"pdf", "doc", "docx", "txt", "md"}
MAX_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def _serialize(doc: Document) -> dict:
    return DocumentOut.from_orm(doc).model_dump()


def _serialize_detail(doc: Document) -> dict:
    return DocumentDetail.from_orm(doc).model_dump()


# ---------- 文档列表 ----------

@router.get("/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: int, db: Session = Depends(get_db)):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    rows = db.execute(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    ).scalars().all()
    return {"items": [_serialize(r) for r in rows]}


# ---------- 上传 ----------

@router.post("/knowledge-bases/{kb_id}/documents")
def upload_documents(
    kb_id: int,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if not files:
        raise HTTPException(status_code=400, detail="未提供文件")
    if len(files) > settings.MAX_UPLOAD_FILES:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {settings.MAX_UPLOAD_FILES} 个文件")

    # 准备目录
    kb_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id))
    os.makedirs(kb_dir, exist_ok=True)

    created: list[Document] = []
    for upload in files:
        ext = _ext(upload.filename or "")
        if ext not in ALLOWED_EXT:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{upload.filename}")
        # 读取内容并校验大小
        data = upload.file.read()
        if len(data) > MAX_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=f"文件过大（>{settings.MAX_UPLOAD_SIZE_MB}MB）：{upload.filename}")
        # 避免覆盖同名：加 6 位随机后缀
        base_name = os.path.splitext(upload.filename or "uploaded")[0]
        unique_name = f"{base_name}_{os.urandom(3).hex()}.{ext}"
        save_path = os.path.join(kb_dir, unique_name)
        with open(save_path, "wb") as f:
            f.write(data)

        doc = Document(
            kb_id=kb_id,
            file_name=upload.filename or unique_name,
            file_type=ext,
            file_size=len(data),
            file_path=save_path,
            parse_status="pending",
        )
        db.add(doc)
        db.flush()
        created.append(doc)

    db.commit()
    for d in created:
        db.refresh(d)

    # 异步处理每个文档
    for d in created:
        background_tasks.add_task(process_document, d.id)

    return {"items": [_serialize(d) for d in created]}


# ---------- 文档详情 ----------

@router.get("/documents/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _serialize_detail(doc)


# ---------- 删除 ----------

@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    # 删除分块
    db.execute(DocChunk.__table__.delete().where(DocChunk.doc_id == doc_id))
    # 删除文件
    try:
        if doc.file_path and os.path.isfile(doc.file_path):
            os.remove(doc.file_path)
    except OSError as e:
        logger.warning("删除文件失败 %s: %s", doc.file_path, e)
    db.delete(doc)
    db.commit()
    # 更新知识库文档数
    _refresh_kb_doc_count(db, doc.kb_id)
    return {"success": True}


# ---------- 重新解析 ----------

@router.post("/documents/{doc_id}/reparse")
def reparse_document(doc_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    doc.parse_status = "pending"
    doc.error_message = None
    db.commit()
    background_tasks.add_task(process_document, doc_id)
    return {"success": True}


# ---------- 下载 ----------

@router.get("/documents/{doc_id}/download")
def download_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if not doc.file_path or not os.path.isfile(doc.file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(doc.file_path, filename=doc.file_name)


# ---------- 后台任务 ----------

def _refresh_kb_doc_count(db: Session, kb_id: int):
    cnt = db.execute(
        select(func.count(Document.id)).where(Document.kb_id == kb_id)
    ).scalar() or 0
    kb = db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.doc_count = int(cnt)
        db.commit()


def process_document(doc_id: int):
    """后台处理：解析 → 分块 → 向量化 → 入库。"""
    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            logger.warning("处理文档 %d 失败：文档不存在", doc_id)
            return
        doc.parse_status = "parsing"
        doc.error_message = None
        db.commit()

        # 1. 解析
        try:
            text = parse_file(doc.file_path, doc.file_type)
        except Exception as e:  # noqa: BLE001
            doc.parse_status = "failed"
            doc.error_message = f"解析失败：{e}"
            db.commit()
            return
        if not text or not text.strip():
            doc.parse_status = "failed"
            doc.error_message = "解析结果为空"
            db.commit()
            return

        # 2. 分块
        chunks = chunk_text(text, chunk_size=500, overlap=50)

        # 3. 删除旧分块
        db.execute(DocChunk.__table__.delete().where(DocChunk.doc_id == doc_id))

        # 4. 批量向量化
        try:
            vectors = embedding_service.embed(chunks)
        except Exception as e:  # noqa: BLE001
            doc.parse_status = "failed"
            doc.error_message = f"向量化失败：{e}"
            db.commit()
            return

        # 5. 入库
        for idx, (chunk_text_content, vec) in enumerate(zip(chunks, vectors)):
            chunk_row = DocChunk(
                doc_id=doc_id,
                kb_id=doc.kb_id,
                content=chunk_text_content,
                embedding=vec,
                chunk_index=idx,
            )
            db.add(chunk_row)

        doc.parsed_text = text
        doc.chunk_count = len(chunks)
        doc.parse_status = "success"
        doc.error_message = None
        db.commit()

        # 更新知识库文档计数
        _refresh_kb_doc_count(db, doc.kb_id)
        logger.info("文档 %d 处理完成，分块数 %d", doc_id, len(chunks))
    except Exception as e:  # noqa: BLE001
        logger.exception("处理文档 %d 异常", doc_id)
        try:
            doc = db.get(Document, doc_id)
            if doc is not None:
                doc.parse_status = "failed"
                doc.error_message = f"内部错误：{e}"
                db.commit()
        except Exception:
            pass
    finally:
        db.close()
