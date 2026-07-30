# 多阶段构建：stage1 编译前端，stage2 运行后端 + 托管前端 dist
# 在 docker-compose 中将 build context 设为项目根目录，dockerfile 指向 backend/Dockerfile

# ---------- Stage 1: 编译前端 ----------
FROM node:20-alpine AS frontend-build
WORKDIR /frontend
# 先拷贝依赖描述以利用 docker 缓存
COPY frontend/package*.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build
# 产物位于 /frontend/dist

# ---------- Stage 2: Python 运行时 ----------
FROM python:3.11-slim AS runtime

# 切换为国内 Debian 镜像源（清华），加速 apt 下载
RUN sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true

# 安装编译 psycopg2 / cryptography 所需系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先安装依赖以利用缓存（使用清华 PyPI 镜像加速）
COPY backend/requirements.txt ./requirements.txt
ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝后端代码
COPY backend/app ./app

# 拷贝前端构建产物到 /app/static，由 FastAPI StaticFiles 托管
COPY --from=frontend-build /frontend/dist ./static

# 上传文件持久化目录
RUN mkdir -p /app/uploads
ENV UPLOAD_DIR=/app/uploads
ENV STATIC_DIR=/app/static

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
