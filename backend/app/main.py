"""FastAPI 应用入口：CORS、启动初始化、路由注册、静态资源托管。"""
import os
import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import init_db
from .routers import chat, admin_llm, admin_kb, admin_docs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("app.main")

app = FastAPI(title="规范智能问答助手 V1.1.1", version="1.1.1")

# CORS
origins = (
    [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
    if settings.CORS_ORIGINS else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    """启动时初始化数据库（带重试 + 创建扩展 + 建表）。"""
    try:
        init_db()
    except Exception as e:  # noqa: BLE001
        logger.error("数据库初始化失败：%s", e)


# ---------- 路由注册 ----------

app.include_router(chat.router)
app.include_router(admin_llm.router)
app.include_router(admin_kb.router)
app.include_router(admin_docs.router)


# ---------- 静态资源托管 ----------

def _resolve_static_dir() -> str | None:
    """按优先级解析前端 dist 目录。"""
    candidates = []
    if settings.STATIC_DIR:
        candidates.append(settings.STATIC_DIR)
    candidates.append("/app/static")
    candidates.append("./static")
    candidates.append("../frontend/dist")
    candidates.append("../static")
    for c in candidates:
        if c and os.path.isdir(c) and os.path.isfile(os.path.join(c, "index.html")):
            return c
    return None


STATIC_DIR = _resolve_static_dir()

if STATIC_DIR:
    assets_dir = os.path.join(STATIC_DIR, "assets")
    if os.path.isdir(assets_dir):
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")
    logger.info("静态资源目录：%s", STATIC_DIR)
else:
    logger.warning("未找到前端 dist 目录，仅提供 API 与健康检查。")


@app.get("/")
def root():
    """根路径：返回 index.html；若没有前端则返回健康检查 JSON。"""
    if STATIC_DIR:
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    return JSONResponse({"name": "规范智能问答助手", "status": "ok", "version": "1.0.0"})


@app.get("/health")
def health():
    return {"status": "ok"}


# SPA fallback：所有非 /api 路径回退到 index.html
@app.get("/{full_path:path}")
def spa_fallback(full_path: str, request: Request):
    if full_path.startswith("api/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    # 优先尝试在静态目录中查找真实文件
    if STATIC_DIR:
        candidate = os.path.normpath(os.path.join(STATIC_DIR, full_path))
        if (
            candidate.startswith(os.path.abspath(STATIC_DIR))
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
    return JSONResponse({"detail": "Not Found"}, status_code=404)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000)
