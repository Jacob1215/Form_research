# 规范智能问答助手系统

基于大语言模型的 RAG（检索增强生成）智能问答 Web 应用，专为工程规范文档知识库定向提问而设计。

[![Version](https://img.shields.io/badge/version-V1.0.9-blue.svg)](CHANGELOG.md)
[![Python](https://img.shields.io/badge/python-3.11+-3776AB.svg?style=flat&logo=python&logoColor=white)](https://www.python.org/)
[![React](https://img.shields.io/badge/react-18-61DAFB.svg?style=flat&logo=react&logoColor=white)](https://react.dev/)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg?style=flat&logo=docker&logoColor=white)](https://www.docker.com/)

---

## ✨ 功能特性

- 🤖 **智能问答**：选择知识库后，基于 RAG 技术提供精准回答，引用规范原文与条款出处
- 🖼️ **检索结果图片展示**（V1.0.9）：查询结果与图片相关时，回答中直接展示相关图片，排版与文本一致
- 📚 **多格式文档支持**：PDF、Word、TXT、Markdown 上传与解析
- 📁 **Markdown 文件夹上传**（V1.0.8）：支持 `.md` 文件 + 图片文件夹批量上传，保留目录结构，预览时正确展示表格与图片
- 🔍 **纯向量语义检索**（V1.0.7）：基于 pgvector 余弦相似度，支持规范编号精确匹配加权
- 🔧 **多模型配置**：可配置多个 LLM API，支持启用/禁用切换
- 📝 **流式输出**：AI 回答实时流式展示
- 📖 **引用溯源**：每条回答标注信息来源，确保可追溯

## 🏗️ 技术栈

| 层次 | 技术选型 | 说明 |
|------|---------|------|
| 前端 | React 18 + TypeScript + Vite | 单页应用，react-markdown 渲染 |
| 后端 | Python 3.11 + FastAPI + Uvicorn | RESTful API + SSE 流式 |
| 数据库 | PostgreSQL 16 + pgvector | 关系存储 + 向量检索一体化 |
| 向量化 | sentence-transformers BAAI/bge-small-zh-v1.5 | 本地 512 维 Embedding，中文优化 |
| 文档解析 | MinerU 3.x / 本地兜底解析 | PDF 提取文本、表格、图片 |
| 部署 | Docker Compose 多阶段构建 | 前端编译 + 后端打包一体化 |

## 📦 项目结构

```
Form_research/
├── backend/                        # FastAPI 后端
│   ├── app/
│   │   ├── routers/                # API 路由
│   │   │   ├── chat.py             # 对话接口（SSE 流式）
│   │   │   ├── admin_docs.py       # 文档管理（上传/列表/详情/解析）
│   │   │   ├── admin_kb.py         # 知识库管理
│   │   │   └── admin_llm.py        # LLM 配置管理
│   │   ├── rag_service.py          # RAG 编排：检索、上下文构建、图片附带
│   │   ├── hybrid_search.py        # 向量检索引擎（V1.0.7 纯向量方案）
│   │   ├── text_utils.py           # 分词 / Markdown 清洗 / BM25 打分
│   │   ├── image_utils.py          # Markdown 图片解析与 URL 重写
│   │   ├── vector_store.py         # pgvector 向量存取
│   │   ├── embedding_service.py    # 本地/远程 Embedding 服务
│   │   ├── chunking_service.py     # 文档分块（600 字符 + 150 重叠）
│   │   ├── models.py               # SQLAlchemy 数据模型
│   │   ├── schemas.py              # Pydantic 请求/响应模型
│   │   ├── config.py               # 应用配置（pydantic-settings）
│   │   └── version.py              # 版本号集中管理
│   ├── Dockerfile                  # 多阶段构建
│   └── requirements.txt            # Python 依赖
├── frontend/                       # React 前端
│   ├── src/
│   │   ├── pages/                  # 页面组件
│   │   │   ├── Chat.tsx            # 前台对话
│   │   │   ├── KbDocuments.tsx     # 文档管理（含文件夹上传）
│   │   │   ├── KbManagement.tsx    # 知识库管理
│   │   │   └── LlmConfig.tsx       # LLM 配置
│   │   ├── components/             # 公共组件
│   │   │   ├── Header.tsx          # 页头导航
│   │   │   ├── Sidebar.tsx         # 侧边栏
│   │   │   └── MarkdownRenderer.tsx # Markdown 渲染器（表格/图片支持）
│   │   ├── api.ts                  # API 客户端封装
│   │   └── version.ts              # 版本号
│   └── package.json
├── docker-compose.yml              # Docker Compose 编排
├── .env.example                    # 环境变量示例
└── README.md
```

## 🚀 快速开始

### 前置要求

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) 2.0+

### 一键部署

```bash
# 1. 克隆仓库
git clone <your-repo-url>
cd Form_research

# 2. 生成加密密钥（生产环境必填）
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入上一步生成的 LLM_ENCRYPT_KEY 和 LLM API 配置

# 4. 构建并启动（首次构建约 5-10 分钟，含 embedding 模型预下载）
docker compose up -d --build

# 5. 查看日志确认启动完成
docker compose logs -f app
```

### 端口映射说明

| 分支 | 宿主机端口 | 容器端口 | 说明 |
|------|-----------|---------|------|
| `master` | 80 | 8000 | 默认端口 |
| `test` | 8080 | 8000 | 开发测试端口 |

访问地址：`http://localhost:8080`（test 分支）或 `http://localhost`（master 分支）

> **注意**：代码变更需重建镜像才能生效（镜像内嵌后端代码与前端构建产物，无代码卷挂载）。上传文件（`./uploads`）与数据库卷均持久化不受影响。

## 📖 使用指南

### 1. 配置大语言模型

进入 **后台管理 → LLM 配置**：

1. 点击「新建配置」
2. 填写配置信息：
   - **配置名称**：任意标识名
   - **模型提供商**：兼容 OpenAI API 的任何提供商
   - **API 地址**：如 `https://api.deepseek.com/v1`
   - **API 密钥**：从提供商控制台获取
   - **模型名称**：如 `deepseek-chat`
3. 勾选「是否启用」并保存
4. 建议点击「测试连接」验证配置有效性

### 2. 创建知识库

进入 **后台管理 → 知识库管理**：

1. 点击「新建知识库」
2. 填写知识库名称和描述
3. 点击「管理文档」进入文档管理页

### 3. 上传文档

在文档管理页：

**单文件上传**：点击上传区域或拖放文件

- 支持格式：PDF、Word（.doc/.docx）、TXT、Markdown
- 单文件最大 50MB，单次最多 20 个

**文件夹上传（Markdown + 图片）**：

1. 点击「上传文件夹」按钮
2. 选择包含 `.md` 文件和图片子文件夹的整个目录
3. 系统自动保留相对目录结构，md 内图片路径自动重写
4. 预览时可正确展示表格与图片

### 4. 智能问答

回到 **前台对话**：

1. 顶部选择已创建的知识库
2. 输入问题，按 Enter 或点击发送
3. AI 基于知识库内容回答，并标注引用来源
4. **图片展示（V1.0.9）**：当检索结果包含相关图片时：
   - 大模型会在回答的对应位置内嵌图片
   - 若未内嵌，后端自动追加「相关图片」小节
   - 保证图片相关的查询必然展示图片

## ⚙️ 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_ENCRYPT_KEY` | 生产必填 | - | Fernet 加密密钥，加密存储 LLM API 密钥 |
| `DATABASE_URL` | 是 | `postgresql://app:app@db:5432/qa` | PostgreSQL 连接串 |
| `MINERU_API_URL` | 可选 | - | MinerU 文档解析服务地址 |
| `EMBEDDING_USE_LOCAL` | 可选 | `true` | 是否使用本地 Embedding 模型 |
| `EMBEDDING_MODEL` | 可选 | `BAAI/bge-small-zh-v1.5` | 本地 Embedding 模型名称 |
| `EMBEDDING_DIM` | 可选 | `512` | 向量维度（需与模型匹配） |
| `EMBEDDING_DEVICE` | 可选 | `cpu` | 推理设备（cpu/cuda） |
| `ENABLE_QUERY_REWRITE` | 可选 | `true` | 是否开启 LLM 查询改写 |
| `RETRIEVE_MODE` | 可选 | `vector_only` | 检索模式 |
| `CORS_ORIGINS` | 可选 | `*` | CORS 允许来源 |

## 📊 架构详解

### RAG 处理流程

```
用户提问 → [查询改写] → [纯向量检索] → [图片提取]
                                           ↓
                                     LLM 流式回答 ← 系统提示词 + 检索片段 + 相关图片
                                           ↓
                                     对话记录保存 + 引用来源标注
```

1. **查询改写**：LLM 将口语化查询扩展为工程规范专业术语（如"焊缝探伤"→"焊缝探伤比例 超声波探伤 射线探伤 焊缝质量"）
2. **纯向量检索**：基于 pgvector 余弦相似度召回 Top-5 相关分块
3. **图片提取**：从检索分块中提取 Markdown 图片引用，重写为后端可访问 URL
4. **上下文构建**：系统提示词 + 检索片段 + 用户问题 + 相关图片
5. **流式生成**：LLM 实时流式返回回答
6. **结果增强**：若回答未包含图片，后端自动追加「相关图片」小节

### 文档处理流水线

```
文档上传 → 磁盘存储 → 解析服务 → 文本提取 → 600字符分块 → Embedding 向量化 → pgvector 存储
          (uploads/)  (MinerU/本地)  (pdfplumber)  (150字符重叠)   (BAAI/bge-small-zh-v1.5)
```

## 🛠️ 开发模式

### 后端开发

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
# 访问 http://localhost:8000/docs 查看 API 文档
```

### 前端开发

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173
# 已配置 API 代理，自动将 /api 请求转发到后端
```

## 🔧 常见问题

<details>
<summary><strong>Q: 启动后提示"未配置模型"？</strong></summary>

需要先在后台管理页面配置并启用至少一个 LLM 模型。
</details>

<details>
<summary><strong>Q: 文档解析失败怎么办？</strong></summary>

文档管理页会显示错误原因。常见原因：
- 文件格式不支持（仅支持 PDF、Word、TXT、Markdown）
- 文件编码问题（建议 UTF-8）
- 文件内容为空或损坏
- PDF 为扫描件且无 OCR 结果
</details>

<details>
<summary><strong>Q: 如何更换端口？</strong></summary>

编辑 `docker-compose.yml`，修改 `ports` 映射：
```yaml
ports:
  - "8080:8000"  # 宿主机 8080 → 容器 8000
```
</details>

<details>
<summary><strong>Q: 数据存储在哪里？</strong></summary>

- **数据库**：Docker 命名卷 `pgdata_test`（test 分支）或 `pgdata`（master 分支）
- **上传文件**：宿主机 `./uploads/` 目录（已加入 .gitignore）
- **Embedding 模型**：Docker 镜像内 `/root/.cache/huggingface/`
</details>

<details>
<summary><strong>Q: 如何完全重置系统？</strong></summary>

```bash
# 停止并删除所有容器和数据卷
docker compose down -v

# 重新构建并启动
docker compose up -d --build
```
</details>

<details>
<summary><strong>Q: 422 错误"Field required"？</strong></summary>

这是 `python-multipart` 版本过低导致的 multipart 边界解析问题。
确保 `requirements.txt` 中 `python-multipart>=0.0.18`，然后重建镜像：
```bash
docker compose build --no-cache app
docker compose up -d
```
</details>

<details>
<summary><strong>Q: 向量检索返回空结果？</strong></summary>

1. 确认知识库文档已完成解析和索引
2. 在文档管理页点击「重新索引」重建向量
3. 检查 `.env` 中 `EMBEDDING_DIM` 是否与模型匹配（BAAI/bge-small-zh-v1.5 为 512）
4. 查看后端日志是否有 embedding 相关错误
</details>

## 📝 版本历史

### V1.0.9 (2026-08-03)
- ✨ 新增检索结果图片展示功能，查询结果与图片相关时在回答中展示
- ✨ 后端自动在回答后追加「相关图片」小节，确保图片必然展示
- 🛠️ 优化 RAG 提示词，增强规范原文引用和条款出处标注
- 🐛 修复查询改写返回空时的异常处理

### V1.0.8 (2026-08-02)
- ✨ 新增 Markdown 文件夹批量上传功能，保留目录结构
- ✨ Markdown 预览支持表格和图片渲染
- ✨ 图片路径自动重写为后端可访问 URL
- 🐛 修复文件夹上传时的 422 错误（python-multipart 升级至 0.0.32）

### V1.0.7 (2026-08-02)
- ✨ 切换为纯向量语义检索方案，取消默认降级 BM25
- ✨ 向量检索异常时提示用户重新索引
- 🛠️ 构建期预下载 Embedding 模型，避免首次启动阻塞

### V1.0.6 - V1.0.0
- 基础 RAG 问答功能
- 多 LLM 模型配置
- 文档解析与分块
- 流式输出与引用溯源

## 📄 许可证

本项目基于 [MIT License](LICENSE) 开源。
