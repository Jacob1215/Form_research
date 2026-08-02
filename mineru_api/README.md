# MinerU HTTP API 服务

将 [MinerU](https://github.com/opendatalab/MinerU) 的 PDF 解析能力封装为 HTTP 服务，供后端 `parsing_service.py` 调用。

## 部署形态

- **CPU 模式**（当前）：基于 `python:3.11-slim`，显式安装 CPU 版 torch/torchvision，清理 CUDA 依赖
- 镜像大小约 5GB，运行时内存约 4GB
- 模型权重在构建期从 modelscope 预下载并烘焙进镜像，运行时通过 `mineru_models` 命名卷持久化

## HTTP 接口

| 方法 | 路径 | 用途 |
|------|------|------|
| GET | `/health` | 健康检查 |
| POST | `/file_parse` | 同步解析（阻塞至返回，适合小文件） |
| POST | `/tasks` | 提交异步解析任务（推荐，支持进度查询） |
| GET | `/tasks/{task_id}` | 查询异步任务状态 |
| GET | `/tasks/{task_id}/result` | 拉取异步任务结果 |

### 同步解析（`POST /file_parse`）

阻塞直到解析完成，适合页数较少的 PDF。

**请求**：`multipart/form-data`
- `files`: PDF 文件
- `return_md`: `"true"` 返回 markdown
- `return_images`: `"true"` 返回图片

**响应**：`{ md_content, images[], total_pages }`

### 异步解析（`POST /tasks`）

立即返回 `task_id`，后台解析，适合大文件。后端 `parse_pdf_with_progress` 优先使用此接口。

**请求**：同 `/file_parse`

**响应**：`{ task_id }`（兼容字段名 `id`）

**轮询状态**（`GET /tasks/{task_id}`）：
```json
{ "status": "running", "progress": 0 }
```

`status` 取值：`running` / `completed` / `failed`（兼容 `processing`/`success`/`error` 等别名）

> 注意：CPU 模式下 `progress` 字段通常为 0，仅在完成时跳到 100，中间无精确进度。

## 构建说明

```bash
# 单独构建 mineru 服务
docker compose build mineru

# 全量启动
docker compose up -d --build
```

### Wheel 文件预下载

构建依赖两个 CPU 版 wheel（已通过 `.gitignore` 排除，不入库，需宿主机预下载）：

```
mineru_api/torch-2.13.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl
mineru_api/torchvision-0.28.0+cpu-cp311-cp311-manylinux_2_28_x86_64.whl
```

下载命令：
```bash
pip download --no-deps --dest mineru_api \
    torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip download --no-deps --dest mineru_api \
    torchvision==0.28.0+cpu --index-url https://download.pytorch.org/whl/cpu
```

## 镜像源加速

| 用途 | 镜像源 |
|------|--------|
| Debian apt | 清华大学 `mirrors.tuna.tsinghua.edu.cn` |
| Python pip | 阿里云 `mirrors.aliyun.com/pypi/simple` |
| 模型下载 | modelscope（国内源） |

## 关键设计决策

1. **CPU 版 torch 先装**：用 `--no-deps` 预装 CPU 版 torch/torchvision，使 `mineru[core]` 检测到依赖已满足，避免拉取 CUDA 版本（历史坑：GPU 版 `_C.so` 残留导致 nms 算子缺失）
2. **构建期 fast-fail**：两次验证 `torchvision.ops.nms` 可用性，避免运行时才发现问题
3. **模型烘焙进镜像**：首次构建时从 modelscope 下载，避免运行时联网阻塞；通过命名卷在容器间共享
4. **降级机制**：后端 `parse_pdf_with_progress` 在异步接口不可用时自动降级到同步 `/file_parse`

## 调试

```bash
# 查看服务日志
docker compose logs mineru -f

# 直接调用接口测试
curl http://localhost:8088/health
curl -X POST http://localhost:8088/file_parse \
  -F "files=@test.pdf" \
  -F "return_md=true"

# 探测异步任务接口字段（OpenAPI 文档）
curl http://localhost:8088/openapi.json | python -m json.tool
```

## 与后端的集成

后端调用方：`backend/app/parsing_service.py`

| 函数 | 调用接口 | 用途 |
|------|----------|------|
| `parse_pdf_with_mineru` | `POST /file_parse` | 同步解析（降级路径） |
| `submit_mineru_task` | `POST /tasks` | 提交异步任务 |
| `poll_mineru_task` | `GET /tasks/{id}` | 轮询任务状态 |
| `fetch_mineru_task_result` | `GET /tasks/{id}/result` | 拉取任务结果 |
| `parse_pdf_with_progress` | 编排上述接口 | 带进度回调的完整解析流程 |

配置项（`backend/app/config.py`）：
- `MINERU_API_URL`：服务地址，默认 `http://mineru:8000`
- `MINERU_TIMEOUT`：解析超时，默认 600 秒
- `MINERU_POLL_INTERVAL`：轮询间隔，默认 2 秒
