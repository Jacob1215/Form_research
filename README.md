# 规范智能问答助手系统

基于大语言模型的智能问答 Web 应用，支持工程规范文档知识库的定向提问。

## 功能特性

- 🤖 **智能问答**：选择知识库后，基于 RAG（检索增强生成）技术提供精准回答
- 📚 **知识库管理**：支持 PDF、Word、TXT、Markdown 文档上传与解析
- 🔧 **多模型支持**：可配置多个大语言模型 API，支持启用/禁用切换
- 📝 **流式输出**：AI 回答实时流式展示，提升交互体验
- 🔍 **引用溯源**：每条回答标注信息来源，确保可追溯

## 技术栈

| 层次 | 选型 |
|------|------|
| 前端 | React 18 + TypeScript + Vite + Ant Design |
| 后端 | Python 3.11 + FastAPI + Uvicorn |
| 数据库 | PostgreSQL 16 + pgvector |
| 文档解析 | MinerU（本地部署）/ 本地兜底解析 |
| 部署 | Docker + Docker Compose |

## 快速开始

### 前置要求

- [Docker](https://docs.docker.com/get-docker/) 20.10+
- [Docker Compose](https://docs.docker.com/compose/install/) 2.0+

### 一键部署

1. **克隆仓库**

```bash
git clone <your-repo-url>
cd Form_research
```

2. **配置环境变量**

```bash
# 复制示例配置
cp .env.example .env

# 编辑 .env 文件，填入必要的密钥（生产环境必填）
# LLM_ENCRYPT_KEY 用于加密存储 LLM API 密钥
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

3. **启动服务**

```bash
# 构建并启动（首次构建约 5-10 分钟）
docker compose up -d --build
```

4. **访问系统**

打开浏览器访问：[http://localhost](http://localhost)

## 使用指南

### 1. 配置大语言模型

进入 **后台管理 → LLM 配置**：

- 点击"新建配置"
- 填写配置名称、模型提供商、API 地址、API 密钥、模型名称
- 勾选"是否启用"并保存
- 建议点击"测试连接"验证配置有效性

### 2. 创建知识库

进入 **后台管理 → 知识库管理**：

- 点击"新建知识库"
- 填写知识库名称和描述
- 点击"管理文档"进入文档管理页

### 3. 上传文档

在文档管理页：

- 点击上传区域或拖放文件
- 支持格式：PDF、Word（.doc/.docx）、TXT、Markdown
- 单文件最大 50MB，单次最多 20 个文件
- 系统自动解析文档并向量化

### 4. 智能问答

回到 **前台对话**：

- 在顶部选择已创建的知识库
- 输入问题，按 Enter 发送
- AI 将基于知识库内容回答，并标注引用来源

## 项目结构

```
Form_research/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── routers/           # API 路由
│   │   ├── models.py          # 数据模型
│   │   ├── schemas.py         # Pydantic 模式
│   │   └── ...
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── pages/            # 页面组件
│   │   ├── api.ts            # API 客户端
│   │   └── ...
│   └── package.json
├── docker-compose.yml         # Docker 编排配置
├── .env.example              # 环境变量示例
└── README.md
```

## 环境变量说明

| 变量 | 必填 | 说明 |
|------|------|------|
| `LLM_ENCRYPT_KEY` | 生产必填 | Fernet 加密密钥，用于加密存储 LLM API 密钥 |
| `MINERU_API_URL` | 可选 | MinerU 文档解析服务地址 |
| `EMBEDDING_API_URL` | 可选 | Embedding 向量化服务地址 |
| `EMBEDDING_API_KEY` | 可选 | Embedding 服务密钥 |
| `EMBEDDING_MODEL` | 可选 | Embedding 模型名称 |

## 常见问题

### Q: 启动后访问页面提示"未配置模型"？

A: 需要先在后台管理页面配置并启用至少一个 LLM 模型。

### Q: 文档解析失败怎么办？

A: 文档管理页会显示错误原因。常见原因包括：
- 文件格式不支持
- 文件编码问题（建议使用 UTF-8）
- 文件内容为空或损坏

### Q: 如何更换端口？

A: 编辑 `docker-compose.yml`，修改 `ports` 映射：
```yaml
ports:
  - "8080:8000"  # 将宿主机 8080 映射到容器 8000
```

### Q: 数据存储在哪里？

A:
- 数据库：Docker 命名卷 `form_research_pgdata`
- 上传文件：`./uploads/` 目录（已加入 .gitignore）

### Q: 如何完全重置系统？

```bash
# 停止并删除所有容器和数据卷
docker compose down -v

# 重新启动
docker compose up -d --build
```

## 开发模式运行

如果需要在本地开发：

### 后端

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 前端

```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173
```

前端开发模式已配置 API 代理，自动将 `/api` 请求转发到后端。

## 技术细节

### RAG 流程

1. 用户选择知识库并提问
2. 系统在 pgvector 中进行向量相似度检索，召回 Top-5 相关文本片段
3. 拼接系统提示词 + 检索片段 + 用户问题
4. 调用 LLM 流式返回回答
5. 保存对话记录，标注引用来源

### 文档处理

1. 上传的文档先保存到磁盘
2. 异步调用解析服务（MinerU 优先，本地兜底）
3. 提取文本后按 500 字符分块（含 50 字符重叠）
4. 使用 Embedding 模型转换为 384 维向量
5. 存入 pgvector 并建立索引

## 许可证

MIT License
