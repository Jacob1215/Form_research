"""管理后台 — 文档上传/列表/详情/删除/下载/解析。

支持 PDF 文档解析：提取文本、表格、图片，解析后可在预览框中查看和搜索。
"""
import os
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from ..database import get_db
from ..config import settings
from ..models import KnowledgeBase, Document
from ..schemas import DocumentOut, DocumentDetail

logger = logging.getLogger("app.admin_docs")

router = APIRouter(prefix="/api/admin", tags=["admin-docs"])

ALLOWED_EXT = {"pdf", "doc", "docx", "txt", "md"}
TEXT_EXT = {"txt", "md"}
MAX_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


def _ext(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def _extract_text(file_path: str, file_type: str) -> str | None:
    """上传时仅对 txt/md 提取文本（PDF/DOC/DOCX 留待 MinerU 解析时提取）。"""
    if file_type in TEXT_EXT:
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                return f.read()[:100000]
        except Exception as e:  # noqa: BLE001
            logger.warning("读取文本失败 %s: %s", file_path, e)
    return None


def _parse_markdown(file_path: str) -> dict:
    """解析 Markdown 文件，提取文本、表格和图片引用。

    按标题（## ）分页，提取 Markdown 表格和图片引用。
    返回结构与 _parse_pdf 一致。
    """
    import re as _re

    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()

    lines = content.split("\n")
    pages: list[dict] = []
    page_num = 0

    current_text: list[str] = []
    current_tables: list[list[list[str]]] = []
    current_images: list[dict] = []
    current_table: list[list[str]] = []
    in_table = False

    def _flush_page():
        nonlocal page_num
        if not current_text and not current_tables and not current_images:
            return
        page_num += 1
        if current_table:
            current_tables.append(current_table)
        pages.append({
            "page_num": page_num,
            "text": "\n".join(current_text).strip(),
            "tables": current_tables,
            "images": current_images,
        })

    for line in lines:
        stripped = line.strip()

        # 标题行 → 新页
        if _re.match(r'^#{1,6}\s+', stripped):
            _flush_page()
            current_text = [stripped]
            current_tables = []
            current_images = []
            current_table = []
            in_table = False
            continue

        # Markdown 表格行
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            # 跳过分隔行 |---|---|
            if all(_re.match(r'^[-:\s]+$', c) for c in cells):
                in_table = True
                continue
            current_table.append(cells)
            in_table = True
            continue

        # 表格结束
        if in_table and not (stripped.startswith("|") and stripped.endswith("|")):
            if current_table:
                current_tables.append(current_table)
            current_table = []
            in_table = False

        # 图片引用 ![alt](src)
        img_match = _re.match(r'!\[([^\]]*)\]\(([^)]+)\)', stripped)
        if img_match:
            alt_text = img_match.group(1)
            img_src = img_match.group(2)
            current_images.append({
                "id": f"img_{len(current_images)}",
                "src": img_src,
            })
            current_text.append(alt_text or "[图片]")
            continue

        current_text.append(line)

    # 最后一页
    _flush_page()

    # 如果没有任何标题，整篇作为一个页面
    if not pages:
        pages.append({
            "page_num": 1,
            "text": content,
            "tables": [],
            "images": [],
        })

    return {"pages": pages, "total_pages": len(pages)}


# ---------- 文档列表 ----------

@router.get("/knowledge-bases/{kb_id}/documents")
def list_documents(kb_id: int, db: Session = Depends(get_db)):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    rows = db.execute(
        select(Document).where(Document.kb_id == kb_id).order_by(Document.created_at.desc())
    ).scalars().all()
    return {"items": [DocumentOut.from_orm(r).model_dump() for r in rows]}


# ---------- 上传 ----------

@router.post("/knowledge-bases/{kb_id}/documents")
def upload_documents(
    kb_id: int,
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

    kb_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id))
    os.makedirs(kb_dir, exist_ok=True)

    created: list[Document] = []
    for upload in files:
        ext = _ext(upload.filename or "")
        if ext not in ALLOWED_EXT:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型：{upload.filename}")
        data = upload.file.read()
        if len(data) > MAX_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=f"文件过大（>{settings.MAX_UPLOAD_SIZE_MB}MB）：{upload.filename}")
        base_name = os.path.splitext(upload.filename or "uploaded")[0]
        unique_name = f"{base_name}_{os.urandom(3).hex()}.{ext}"
        save_path = os.path.join(kb_dir, unique_name)
        with open(save_path, "wb") as f:
            f.write(data)

        # 仅对 txt/md 提取文本内容，供检索使用
        content_text = _extract_text(save_path, ext)

        doc = Document(
            kb_id=kb_id,
            file_name=upload.filename or unique_name,
            file_type=ext,
            file_size=len(data),
            file_path=save_path,
            content_text=content_text,
        )
        db.add(doc)
        db.flush()
        created.append(doc)

    db.commit()
    for d in created:
        db.refresh(d)

    _refresh_kb_doc_count(db, kb_id)

    # 自动索引：对上传的文档进行分块和向量化
    for d in created:
        try:
            _auto_index_document(db, d)
        except Exception as e:
            logger.warning("自动索引文档 %d 失败: %s", d.id, e)

    return {"items": [DocumentOut.from_orm(d).model_dump() for d in created]}


# ---------- 文档详情 ----------

@router.get("/documents/{doc_id}")
def get_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return DocumentDetail(
        id=doc.id,
        kb_id=doc.kb_id,
        file_name=doc.file_name,
        file_type=doc.file_type,
        file_size=doc.file_size,
        content_text=doc.content_text,
        parsed_content=doc.parsed_content,
        parse_status=getattr(doc, "parse_status", "pending") or "pending",
        created_at=doc.created_at.isoformat() if doc.created_at else None,
        updated_at=doc.updated_at.isoformat() if doc.updated_at else None,
    )


# ---------- 文档解析 ----------

@router.post("/documents/{doc_id}/parse")
def parse_document(doc_id: int, db: Session = Depends(get_db)):
    """解析文档：PDF 走 MinerU 异步任务（后台线程 + 进度轮询），Markdown 同步解析。

    V1.1.1：PDF 解析不再阻塞 HTTP 请求，立即返回 parsing 状态；
    前端通过 GET /documents/{doc_id}/parse-progress 轮询进度。
    """
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    if doc.file_type not in ("pdf", "md", "markdown"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 和 Markdown 文件解析")

    if not doc.file_path or not os.path.isfile(doc.file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    # 重置进度状态
    doc.parse_status = "parsing"
    doc.parse_progress = 0
    doc.parse_step = "初始化"
    doc.parse_error = None
    doc.parse_task_id = None
    doc.parse_started_at = datetime.utcnow()
    doc.parse_finished_at = None
    db.commit()

    if doc.file_type == "pdf":
        # PDF 解析耗时较长，放入后台线程执行，避免阻塞 HTTP 请求
        import threading
        threading.Thread(
            target=_run_pdf_parse_async, args=(doc.id,), daemon=True
        ).start()
        return {
            "success": True,
            "parse_status": "parsing",
            "progress": 0,
            "step": "初始化",
        }

    # Markdown 解析快，仍同步执行
    try:
        result = _parse_markdown(doc.file_path)
        doc.parsed_content = json.dumps(result, ensure_ascii=False)
        doc.parse_status = "done"
        doc.parse_progress = 100
        doc.parse_step = "解析完成"
        doc.parse_finished_at = datetime.utcnow()
        full_text = "\n\n".join(p["text"] for p in result["pages"] if p.get("text"))
        if full_text:
            doc.content_text = full_text[:100000]
        db.commit()
        db.refresh(doc)
        try:
            rebuilt = _auto_index_document(db, doc)
            logger.info("文档 %s 解析后重建分块：%d 个", doc_id, rebuilt)
        except Exception as e:  # noqa: BLE001
            logger.warning("文档 %s 解析后重建分块失败: %s", doc_id, e)
        return {
            "success": True,
            "parse_status": "done",
            "total_pages": result["total_pages"],
            "total_images": sum(len(p.get("images", [])) for p in result["pages"]),
            "total_tables": sum(len(p.get("tables", [])) for p in result["pages"]),
        }
    except Exception as e:
        doc.parse_status = "error"
        doc.parse_error = str(e)[:500]
        doc.parse_finished_at = datetime.utcnow()
        db.commit()
        logger.error("文档 %s 解析失败: %s", doc_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析失败: {e}")


def _run_pdf_parse_async(doc_id: int):
    """后台线程：调用 MinerU 异步任务解析 PDF，实时更新 DB 进度。

    使用独立 DB 会话（SessionLocal），避免与请求作用域冲突。
    """
    from ..parsing_service import parse_pdf_with_progress
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        doc = db.get(Document, doc_id)
        if doc is None:
            return

        def on_progress(step: str, percent: int):
            try:
                d = db.get(Document, doc_id)
                if d is not None:
                    d.parse_step = step
                    # 进度只增不减，避免 MinerU running 时 progress=0 导致回退
                    d.parse_progress = max(int(d.parse_progress or 0), int(percent))
                    db.commit()
            except Exception as e:  # noqa: BLE001
                logger.warning("进度回调写库失败 doc_id=%d: %s", doc_id, e)

        result = parse_pdf_with_progress(
            doc.file_path, doc.id, doc.kb_id, on_progress=on_progress
        )

        doc = db.get(Document, doc_id)
        if doc is None:
            return
        doc.parsed_content = json.dumps(result, ensure_ascii=False)
        doc.parse_status = "done"
        doc.parse_progress = 100
        doc.parse_step = "解析完成"
        doc.parse_finished_at = datetime.utcnow()
        full_text = "\n\n".join(p["text"] for p in result["pages"] if p.get("text"))
        if full_text:
            doc.content_text = full_text[:100000]
        db.commit()
        try:
            _auto_index_document(db, doc)
        except Exception as e:  # noqa: BLE001
            logger.warning("文档 %s 解析后重建分块失败: %s", doc_id, e)
        logger.info("文档 %s 后台解析完成：%d 页",
                     doc_id, result["total_pages"])
    except Exception as e:
        try:
            d = db.get(Document, doc_id)
            if d is not None:
                d.parse_status = "error"
                d.parse_error = str(e)[:500]
                d.parse_step = "解析失败"
                d.parse_finished_at = datetime.utcnow()
                db.commit()
        except Exception:  # noqa: BLE001
            pass
        logger.error("文档 %s 后台解析失败: %s", doc_id, e, exc_info=True)
    finally:
        db.close()


@router.get("/documents/{doc_id}/parse-progress")
def get_parse_progress(doc_id: int, db: Session = Depends(get_db)):
    """查询文档解析进度（前端轮询）。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {
        "doc_id": doc.id,
        "parse_status": doc.parse_status or "pending",
        "progress": int(doc.parse_progress or 0),
        "step": doc.parse_step,
        "error": doc.parse_error,
        "started_at": doc.parse_started_at.isoformat() if doc.parse_started_at else None,
        "finished_at": doc.parse_finished_at.isoformat() if doc.parse_finished_at else None,
    }


# ---------- 图片服务 ----------

@router.get("/documents/{doc_id}/images/{img_filename}")
def serve_document_image(doc_id: int, img_filename: str, db: Session = Depends(get_db)):
    """提供解析后的图片文件。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    img_path = os.path.join(settings.UPLOAD_DIR, str(doc.kb_id), "images", str(doc_id), img_filename)
    if not os.path.isfile(img_path):
        raise HTTPException(status_code=404, detail="图片不存在")

    return FileResponse(img_path)


# ---------- 删除 ----------

@router.delete("/documents/{doc_id}")
def delete_document(doc_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    try:
        if doc.file_path and os.path.isfile(doc.file_path):
            os.remove(doc.file_path)
    except OSError as e:
        logger.warning("删除文件失败 %s: %s", doc.file_path, e)
    kb_id = doc.kb_id

    # 清理向量分块
    try:
        from ..vector_store import VectorStore
        VectorStore(db).delete_chunks(doc_id)
    except Exception as e:
        logger.warning("清理分块失败 doc_id=%d: %s", doc_id, e)

    db.delete(doc)
    db.commit()
    _refresh_kb_doc_count(db, kb_id)
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


# ---------- 重建索引 ----------

@router.post("/documents/{doc_id}/reindex")
def reindex_document(doc_id: int, db: Session = Depends(get_db)):
    """手动重建文档的向量索引：分块 → 向量化 → 存储。"""
    doc = db.get(Document, doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if not doc.content_text:
        raise HTTPException(status_code=400, detail="文档需先有文本内容，请先上传或解析文档")

    try:
        result = _auto_index_document(db, doc)
        return {"success": True, "chunks": result}
    except Exception as e:
        logger.error("重建索引失败 doc_id=%d: %s", doc_id, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"索引重建失败: {e}")


@router.post("/knowledge-bases/{kb_id}/reindex-all")
def reindex_knowledge_base(kb_id: int, db: Session = Depends(get_db)):
    """重建知识库中所有文档的向量索引。"""
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")

    docs = db.execute(
        select(Document).where(
            Document.kb_id == kb_id,
            Document.content_text.isnot(None),
        )
    ).scalars().all()

    total_chunks = 0
    errors = []
    for doc in docs:
        try:
            total_chunks += _auto_index_document(db, doc)
        except Exception as e:
            errors.append({"doc_id": doc.id, "file_name": doc.file_name, "error": str(e)})
            logger.warning("重建索引失败 doc_id=%d: %s", doc.id, e)

    return {
        "success": True,
        "total_docs": len(docs),
        "total_chunks": total_chunks,
        "errors": errors,
    }


# ---------- 内部工具 ----------

def _auto_index_document(db: Session, doc: Document) -> int:
    """对文档进行分块、向量化并存储到 pgvector。

    返回创建的分块数。如果文档没有文本内容，返回 0。
    """
    if not doc.content_text:
        return 0

    from ..chunking_service import ChunkingService
    from ..embedding_service import embedding_service
    from ..vector_store import VectorStore

    # 1. 分块
    chunker = ChunkingService()
    chunks = chunker.chunk_text(doc.content_text, doc.file_name)
    if not chunks:
        logger.warning("文档 %d 分块结果为空", doc.id)
        return 0

    # 2. 向量化
    chunk_texts = [c.content for c in chunks]
    embeddings = None
    try:
        embeddings = embedding_service.embed(chunk_texts)
    except Exception as e:
        logger.warning("文档 %d 向量化失败: %s", doc.id, e)

    # 3. 存储
    if embeddings is not None and len(embeddings) == len(chunks):
        chunk_dicts = [
            {"index": c.index, "content": c.content, "embedding": emb, "token_count": c.token_count}
            for c, emb in zip(chunks, embeddings)
        ]
    else:
        # 向量化不可用，存储无向量的分块（后续可手动向量化）
        if embeddings is None:
            logger.info("向量化服务不可用，存储无向量分块: doc_id=%d", doc.id)
        chunk_dicts = [
            {"index": c.index, "content": c.content, "embedding": None, "token_count": c.token_count}
            for c in chunks
        ]
    vs = VectorStore(db)
    count = vs.store_chunks(doc.id, doc.kb_id, chunk_dicts)

    logger.info("文档 %d 自动索引完成: %d 个分块", doc.id, count)
    return count


def _refresh_kb_doc_count(db: Session, kb_id: int):
    cnt = db.execute(
        select(func.count(Document.id)).where(Document.kb_id == kb_id)
    ).scalar() or 0
    kb = db.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.doc_count = int(cnt)
        db.commit()
