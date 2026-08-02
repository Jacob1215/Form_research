# =============================================================================
# MinerU HTTP API 服务镜像（CPU 模式）
# =============================================================================
# 用途：封装 MinerU PDF 解析能力为 HTTP 服务，供后端 parsing_service 调用。
# 接口：GET  /health           健康检查
#       POST /file_parse       同步解析（阻塞至返回，适合小文件）
#       POST /tasks            提交异步解析任务（推荐，支持进度查询）
#       GET  /tasks/{id}       查询异步任务状态
#       GET  /tasks/{id}/result 拉取异步任务结果
# 构建：docker compose build mineru
# 模型：首次构建时从 modelscope 预下载，烘焙进镜像；运行时挂载 mineru_models 卷
# =============================================================================

FROM python:3.11-slim

# ---- 系统依赖 ----
# OpenCV 运行时库（libgl1/libsm6/libXext 等）、OpenMP（libgomp1）、中文字体
# 使用清华 Debian 镜像源加速 apt 安装
RUN sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true \
    && apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libgomp1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    fonts-noto-core \
    fonts-noto-cjk \
    fontconfig \
    curl \
    && fc-cache -fv \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ---- Python 依赖 ----
# 阿里云 PyPI 镜像加速
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple

# torch/torchvision 的非 CUDA 运行时依赖（先装，避免 mineru 拉取 CUDA 版本）
RUN pip install --no-cache-dir --timeout 300 --retries 5 \
    typing-extensions sympy networkx jinja2 fsspec filelock mpmath markupsafe \
    numpy pillow

# 复制预下载的 CPU 版 wheel（宿主机下载更稳定，避免构建期网络波动）
COPY torch-2.13.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl /tmp/
COPY torchvision-0.28.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl /tmp/

# ---- 安装 CPU 版 torch/torchvision ----
# 关键：用 --no-deps 先装 CPU 版，使后续 mineru[core] 检测到依赖已满足，
# 不会拉取 GPU/CUDA 版本（历史坑：曾因残留 GPU 版 _C.so 导致 nms 算子缺失）
RUN pip install --no-cache-dir --no-deps /tmp/torch-2.13.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl \
    && pip install --no-cache-dir --no-deps /tmp/torchvision-0.28.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl \
    && rm -f /tmp/torch-2.13.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl

# 构建期 fast-fail：验证 torchvision nms 算子可用
RUN python -c "import torch, torchvision; print('torch', torch.__version__); print('torchvision', torchvision.__version__); import torchvision.ops as ops; print('nms ok:', hasattr(ops, 'nms'))"

# ---- 安装 MinerU ----
# torch/torchvision 已由 CPU 版满足，不触发 CUDA 依赖
RUN pip install --no-cache-dir --timeout 600 --retries 10 "mineru[core]>=2.7.0"

# 兜底：若 mineru[core] 升级了 torchvision，用本地 CPU wheel 强制还原；
# 同时清理可能被拉入的 nvidia-* CUDA 包以缩小镜像体积
RUN pip install --no-cache-dir --no-deps --force-reinstall /tmp/torchvision-0.28.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl \
    && rm -f /tmp/torchvision-0.28.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl \
    ; pip uninstall -y nvidia-cudnn-cu12 nvidia-cublas-cu12 nvidia-cuda-runtime-cu12 \
                       nvidia-cuda-cupti-cu12 nvidia-cuda-nvrtc-cu12 nvidia-cufft-cu12 \
                       nvidia-curand-cu12 nvidia-cusolver-cu12 nvidia-cusparse-cu12 \
                       nvidia-nccl-cu12 nvidia-nvtx-cu12 nvidia-cusparselt-cu12 2>/dev/null \
    ; true

# 再次验证 nms 算子（mineru 安装后）
RUN python -c "import torch, torchvision; import torchvision.ops as ops; print('post-mineru nms ok:', hasattr(ops, 'nms'))"

# ---- 模型权重 ----
# 预下载（modelscope 国内源），烘焙进镜像层；
# 运行时由 mineru_models 命名卷挂载到 /root/.cache，首次启动从镜像拷入卷
ENV MINERU_MODEL_SOURCE=modelscope
RUN echo "modelscope" | mineru-models-download -m all

# 切换为 local 模型源（使用已下载的模型，避免运行时联网）
ENV MINERU_MODEL_SOURCE=local

EXPOSE 8000

# 启动官方 mineru-api HTTP 服务
CMD ["mineru-api", "--host", "0.0.0.0", "--port", "8000"]
