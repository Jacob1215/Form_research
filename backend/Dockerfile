# 多阶段构建：stage1 编译前端，stage2 运行后端 + 托管前端 dist
# 在 docker-compose 中将 build context 设为项目根目录，dockerfile 指向 backend/Dockerfile

# ---------- Stage 1: 编译前端 ----------
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
# 先拷贝依赖描述以利用 docker 缓存
COPY frontend/package*.json ./
RUN npm config set registry https://registry.npmmirror.com && npm install --include=dev
COPY frontend/ ./
RUN node_modules/.bin/tsc -b && node_modules/.bin/vite build
# 产物位于 /frontend/dist

# ---------- Stage 2: Python 运行时 ----------
FROM python:3.11-slim AS runtime

# 切换为国内 Debian 镜像源（清华），加速 apt 下载
RUN sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true

# 安装编译 psycopg2 / cryptography 所需系统依赖，以及 torch 运行时依赖 libgomp1（OpenMP）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装依赖以利用缓存（清华 PyPI 主源 + PyTorch CPU 源）
# torch 走 CPU 版可避免拉取 CUDA 包，镜像体积从 ~2.5GB 降到 ~1.5GB
COPY backend/requirements.txt ./requirements.txt
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_EXTRA_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/pytorch/whl/cpu \
    PIP_DEFAULT_TIMEOUT=120 \
    HF_ENDPOINT=https://hf-mirror.com
RUN pip install --no-cache-dir -r requirements.txt

# V1.0.7：构建期预下载本地 embedding 模型，避免首次启动时运行时下载阻塞请求
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5', device='cpu')" \
    && echo "embedding 模型预下载完成"

# 拷贝后端代码
COPY backend/app ./app

# 拷贝前端构建产物到 /app/static，由 FastAPI StaticFiles 托管
COPY --from=frontend-build /frontend/dist ./static

# 上传文件持久化目录
RUN mkdir -p /app/uploads

# 构建标记：记录镜像构建时间，便于确认容器是否为新镜像（放在所有代码 COPY 之后，任何源码变更都会使该层缓存失效、重新写入新时间戳）
RUN date -u +"%Y-%m-%d %H:%M:%S UTC" > /app/build_time.txt
ENV UPLOAD_DIR=/app/uploads
ENV STATIC_DIR=/app/static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
