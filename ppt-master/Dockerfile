# ppt-master 服务镜像（V1.2.6）
# 下载 hugohe3/ppt-master，内置 Claude Code CLI + ppt-master skill，并提供一个
# 轻量 FastAPI wrapper（server.py）向本应用 PPT 制作页面提供 HTTP 服务。
#
# 运行时大模型凭据由后端每次请求下发（Anthropic 兼容端点），本镜像不含任何密钥。

# ---------- 基础镜像：python:3.11-slim（Debian bookworm，本机已缓存，避免慢拉 Docker Hub） ----------
FROM python:3.11-slim

# 国内 Debian 镜像源（清华），加速 apt
RUN sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || true \
    && sed -i 's|//deb.debian.org|//mirrors.tuna.tsinghua.edu.cn|g; s|//security.debian.org|//mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null || true

# git（构建期 clone）+ curl（下载 Node）+ xz-utils（解 .tar.xz）
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Node 20（Claude Code CLI 运行时）— 从 npmmirror 下载，避免 Docker Hub 慢速
ARG NODE_VERSION=20.19.4
RUN curl -fsSL "https://registry.npmmirror.com/-/binary/node/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz \
    && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 \
    && rm /tmp/node.tar.xz \
    && node --version && npm --version

# Claude Code CLI（npm 国内镜像）
RUN npm config set registry https://registry.npmmirror.com \
    && npm install -g @anthropic-ai/claude-code

# 下载 ppt-master（构建期 clone，钉死版本；升级改 PPT_MASTER_REF）
ARG PPT_MASTER_REF=v4.5.0
RUN git clone --depth 1 --branch ${PPT_MASTER_REF} https://github.com/hugohe3/ppt-master.git /app/ppt-master

# 安装 ppt-master 依赖（阿里云 PyPI 主源；requirements.txt 会递归包含 skill 内依赖）
ENV PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_DEFAULT_TIMEOUT=120
RUN pip install --no-cache-dir -r /app/ppt-master/requirements.txt

# 拷贝并安装 wrapper 依赖
WORKDIR /srv
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py ./server.py

# 任务工作目录（卷挂载 /work）
ENV PPT_MASTER_REPO_DIR=/app/ppt-master \
    PPT_MASTER_WORK_DIR=/work
RUN mkdir -p /work /app/ppt-master/projects

# 非 root 运行：claude 的 --dangerously-skip-permissions 拒绝 root 用户。
# 建 pptmaster 用户，把 skill 装到其 HOME，并给 /work 与 projects 目录写权限。
RUN useradd -m -u 1000 pptmaster \
    && mkdir -p /home/pptmaster/.claude/skills \
    && ln -s /app/ppt-master/skills/ppt-master /home/pptmaster/.claude/skills/ppt-master \
    && chown -R pptmaster:pptmaster /home/pptmaster /work /app/ppt-master/projects
ENV HOME=/home/pptmaster
USER pptmaster

EXPOSE 8001

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
