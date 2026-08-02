"""文档解析服务：MinerU 远程解析（PDF/DOC/DOCX）+ 本地文本读取（txt/md）。不降级。

V1.1.0：删除 pdfplumber/PyMuPDF/python-docx 本地解析组件，PDF 解析统一由 MinerU 服务承担。
"""
import base64
import logging
import os
import re

import httpx

from .config import settings

logger = logging.getLogger("app.parsing")


def _read_text(path: str) -> str:
    """读取 txt/md 文本文件，自动尝试多种编码。"""
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return ""


def _extract_mineru_markdown(data: dict) -> str:
    """从 MinerU 返回 JSON 中提取 markdown 文本，兼容多种字段命名。

    支持的响应结构：
    - 顶层字段：md_content / markdown / text / content / result
    - 嵌套对象：data.md_content 等
    - MinerU 3.x：results 是 dict（key 为文件名），取 results[filename].md_content
    - 兼容旧版：results 为 list 时取 results[0].md_content
    """
    # 1) 顶层字段直接命中
    for key in ("md_content", "markdown", "text", "content", "result"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, dict):
            for sub in ("md_content", "markdown", "text", "content"):
                sub_val = val.get(sub)
                if isinstance(sub_val, str) and sub_val.strip():
                    return sub_val

    # 2) MinerU 3.x：results 是 dict（key 为文件名，value 为含 md_content 的 dict）
    results = data.get("results")
    if isinstance(results, dict):
        for item in results.values():
            if isinstance(item, dict):
                for key in ("md_content", "markdown", "text", "content"):
                    val = item.get(key)
                    if isinstance(val, str) and val.strip():
                        return val
    elif isinstance(results, list) and len(results) > 0:
        # 兼容旧版 results 为 list 的结构
        result = results[0]
        if isinstance(result, dict):
            for key in ("md_content", "markdown", "text", "content"):
                val = result.get(key)
                if isinstance(val, str) and val.strip():
                    return val
    return ""


def _extract_mineru_images(data: dict) -> list[dict]:
    """从 MinerU 返回 JSON 中提取图片列表。

    MinerU 3.x 结构：results[filename].images 是 dict，key 为图片名，value 为 base64 data URI。
    兼容顶层 images 字段（list 或 dict）。
    返回统一格式：[{"name": ..., "b64": ...}, ...]
    """
    raw_images: dict | list | None = None

    # 1) 优先从 MinerU 3.x 的 results[filename].images 提取
    results = data.get("results")
    if isinstance(results, dict):
        for item in results.values():
            if isinstance(item, dict) and isinstance(item.get("images"), (dict, list)):
                raw_images = item["images"]
                break

    # 2) 兜底：顶层 images / image_list
    if raw_images is None:
        raw_images = data.get("images") or data.get("image_list") or []

    images: list[dict] = []
    if isinstance(raw_images, dict):
        # MinerU 3.x: {图片名: "data:image/png;base64,..."}
        for name, val in raw_images.items():
            if isinstance(val, str) and val.startswith("data:"):
                images.append({"name": name, "b64": val})
            elif isinstance(val, str) and len(val) > 200:
                # 纯 base64 字符串
                images.append({"name": name, "b64": val})
    elif isinstance(raw_images, list):
        for idx, item in enumerate(raw_images):
            if isinstance(item, dict):
                name = item.get("name") or item.get("path") or f"img_{idx}.png"
                b64 = item.get("data") or item.get("base64") or ""
                if b64 or item.get("url") or item.get("src"):
                    images.append({"name": name, "b64": b64,
                                   "url": item.get("url") or item.get("src") or ""})
            elif isinstance(item, str) and len(item) > 200:
                images.append({"name": f"img_{idx}.png", "b64": item})
    return images


def parse_pdf_with_mineru(path: str, doc_id: int, kb_id: int) -> dict:
    """调用 MinerU API 解析 PDF，落地图片并重写引用。不降级，失败直接抛异常。

    返回结构（与原 _parse_pdf 兼容，供前端预览）：
    {
        "pages": [{"page_num": 1, "text": <markdown>, "tables": [], "images": [...]}],
        "total_pages": 1,
        "format": "mineru",
        "markdown": <完整 markdown>
    }
    """
    url = settings.MINERU_API_URL.rstrip("/") + settings.MINERU_PARSE_ENDPOINT
    img_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id), "images", str(doc_id))
    os.makedirs(img_dir, exist_ok=True)

    # 调用 MinerU HTTP API（字段名 files，参数 return_md=true 返回 markdown，return_images=true 返回图片 base64）
    with httpx.Client(timeout=httpx.Timeout(settings.MINERU_TIMEOUT)) as client:
        with open(path, "rb") as f:
            files = {"files": (os.path.basename(path), f, "application/pdf")}
            data = {"return_md": "true", "return_images": "true"}
            resp = client.post(url, files=files, data=data)

    if resp.status_code >= 400:
        raise RuntimeError(f"MinerU 返回 {resp.status_code}: {resp.text[:300]}")

    ctype = resp.headers.get("content-type", "")
    data = resp.json() if "application/json" in ctype else {"md_content": resp.text}
    markdown = _extract_mineru_markdown(data)
    if not markdown.strip():
        raise RuntimeError("MinerU 返回内容为空")

    # 落地图片（base64 → 本地文件），并建立 basename → URL 映射
    raw_images = _extract_mineru_images(data)
    images: list[dict] = []
    name_to_src: dict[str, str] = {}

    for item in raw_images:
        name = item.get("name", "img.png")
        b64 = item.get("b64", "")
        url_img = item.get("url", "")

        safe_name = os.path.basename(name).replace(" ", "_")
        if not os.path.splitext(safe_name)[1]:
            safe_name += ".png"
        save_path = os.path.join(img_dir, safe_name)

        try:
            if b64:
                payload = b64.split(",", 1)[-1] if "," in b64 else b64
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(payload))
            elif url_img.startswith("http"):
                with httpx.Client(timeout=30.0) as c:
                    r = c.get(url_img)
                    if r.status_code == 200:
                        with open(save_path, "wb") as f:
                            f.write(r.content)
            else:
                continue
            src = f"/api/admin/documents/{doc_id}/images/{safe_name}"
            images.append({"id": safe_name, "name": safe_name, "src": src})
            name_to_src[safe_name] = src
        except Exception as e:  # noqa: BLE001
            logger.warning("保存 MinerU 图片失败 %s: %s", safe_name, e)

    # 重写 markdown 中的图片引用 ![alt](images/xxx.jpg) → ![alt](/api/admin/documents/...)
    def _replace(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        basename = os.path.basename(src.split("?", 1)[0])
        return f"![{alt}]({name_to_src[basename]})" if basename in name_to_src else m.group(0)

    markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, markdown)

    logger.info("MinerU 解析成功 doc_id=%d: %d 字符, %d 张图片",
                doc_id, len(markdown), len(images))
    return {
        "pages": [{"page_num": 1, "text": markdown, "tables": [], "images": images}],
        "total_pages": data.get("total_pages", 1) or 1,
        "format": "mineru",
        "markdown": markdown,
    }


def submit_mineru_task(path: str, doc_id: int) -> str | None:
    """向 MinerU 提交异步解析任务。

    Args:
        path: PDF 文件绝对路径。
        doc_id: 文档 ID（仅用于日志，不发送给 MinerU）。

    Returns:
        task_id 字符串；失败时返回 None，调用方据此降级到同步 /file_parse 接口。
    """
    url = settings.MINERU_API_URL.rstrip("/") + settings.MINERU_TASK_ENDPOINT
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        with open(path, "rb") as f:
            files = {"files": (os.path.basename(path), f, "application/pdf")}
            data = {"return_md": "true", "return_images": "true"}
            resp = client.post(url, files=files, data=data)
    if resp.status_code >= 400:
        logger.warning("MinerU 提交异步任务失败 doc_id=%d: %s %s",
                       doc_id, resp.status_code, resp.text[:200])
        return None
    payload = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
    # 兼容 MinerU 不同版本的字段命名：task_id / id
    task_id = payload.get("task_id") or payload.get("id")
    if not task_id:
        logger.warning("MinerU 异步任务响应缺少 task_id doc_id=%d: %s", doc_id, str(payload)[:200])
        return None
    return str(task_id)


def poll_mineru_task(task_id: str) -> dict:
    """查询 MinerU 异步任务状态。

    Args:
        task_id: submit_mineru_task 返回的任务 ID。

    Returns:
        dict 含三个字段：
        - status: running / completed / failed（兼容 processing/success 等别名）
        - progress: 0-100 整数；MinerU 未返回精确进度时根据 status 估算
        - result: 已完成时携带结果 dict，其余为 None
    """
    url = f"{settings.MINERU_API_URL.rstrip('/')}{settings.MINERU_TASK_ENDPOINT}/{task_id}"
    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        # 网络异常或服务未就绪，视为仍在运行
        return {"status": "running", "progress": 0, "result": None}
    data = resp.json() if "application/json" in resp.headers.get("content-type", "") else {}
    # 兼容 MinerU 不同版本的字段命名：status / state
    raw_status = (data.get("status") or data.get("state") or "running").lower()
    progress = int(data.get("progress", 0) or 0)
    result = None
    if raw_status in ("completed", "success", "done", "finished"):
        status = "completed"
        progress = 100
        result = data.get("result") or data
    elif raw_status in ("failed", "error"):
        status = "failed"
        result = data
    else:
        status = "running"
        # MinerU CPU 模式不返回精确中间进度，running 时估算为 10%
        if progress <= 0:
            progress = 10
    return {"status": status, "progress": max(0, min(100, progress)), "result": result}


def fetch_mineru_task_result(task_id: str) -> dict:
    """拉取已完成任务的解析结果 JSON。

    当 poll_mineru_task 返回的 result 非完整结果时，通过此接口单独拉取。
    """
    url = f"{settings.MINERU_API_URL.rstrip('/')}{settings.MINERU_TASK_ENDPOINT}/{task_id}/result"
    with httpx.Client(timeout=httpx.Timeout(60.0)) as client:
        resp = client.get(url)
    if resp.status_code >= 400:
        raise RuntimeError(f"MinerU 拉取结果失败 {resp.status_code}: {resp.text[:300]}")
    ctype = resp.headers.get("content-type", "")
    return resp.json() if "application/json" in ctype else {"md_content": resp.text}


def parse_pdf_with_progress(
    path: str,
    doc_id: int,
    kb_id: int,
    on_progress=None,
) -> dict:
    """带进度回调的 PDF 解析。

    优先走 MinerU 异步任务接口（POST /tasks + 轮询 GET /tasks/{id}），
    若异步接口不可用则降级到同步 /file_parse（无中间进度）。

    Args:
        path: PDF 文件绝对路径。
        doc_id: 文档 ID。
        kb_id: 知识库 ID（用于图片落地目录）。
        on_progress: 可选回调 `fn(step: str, percent: int)`，每次状态变化时调用，
                     用于更新 DB 进度字段供前端轮询。

    Returns:
        与 parse_pdf_with_mineru 结构一致的解析结果 dict。
    """
    def _emit(step: str, percent: int):
        """安全调用进度回调，吞掉异常避免影响解析主流程。"""
        if on_progress:
            try:
                on_progress(step, percent)
            except Exception as e:  # noqa: BLE001
                logger.warning("进度回调异常 doc_id=%d: %s", doc_id, e)

    # 进度阶段：5% 提交 → 15% 已排队 → 轮询更新 → 100% 完成
    _emit("提交解析任务", 5)
    task_id = submit_mineru_task(path, doc_id)

    if task_id is None:
        # 降级路径：MinerU 不支持异步任务，走同步接口（阻塞至完成，无中间进度）
        logger.info("MinerU 异步任务不可用，降级同步解析 doc_id=%d", doc_id)
        _emit("同步解析中", 20)
        result = parse_pdf_with_mineru(path, doc_id, kb_id)
        _emit("解析完成", 100)
        return result

    _emit("已提交，等待 MinerU 处理", 15)
    import time
    deadline = time.time() + settings.MINERU_TIMEOUT
    while time.time() < deadline:
        time.sleep(settings.MINERU_POLL_INTERVAL)
        info = poll_mineru_task(task_id)
        step_label = {
            "running": "MinerU 解析中",
            "completed": "解析完成",
            "failed": "解析失败",
        }.get(info["status"], "解析中")
        _emit(step_label, info["progress"])
        if info["status"] == "completed":
            # result 可能已内嵌在状态响应里，否则单独拉取
            data = info["result"] if isinstance(info["result"], dict) else fetch_mineru_task_result(task_id)
            return _build_mineru_result(data, path, doc_id, kb_id)
        if info["status"] == "failed":
            raise RuntimeError(f"MinerU 任务失败: {str(info.get('result'))[:300]}")
    raise RuntimeError(f"MinerU 任务超时（{settings.MINERU_TIMEOUT}s）")


def _build_mineru_result(data: dict, path: str, doc_id: int, kb_id: int) -> dict:
    """从 MinerU 任务结果构建与 parse_pdf_with_mineru 一致的返回结构。

    处理内容：提取 markdown 文本 → 落地图片到 uploads/{kb_id}/images/{doc_id}/
    → 重写 markdown 中的图片引用为本地 URL。
    """
    markdown = _extract_mineru_markdown(data)
    if not markdown.strip():
        raise RuntimeError("MinerU 返回内容为空")
    img_dir = os.path.join(settings.UPLOAD_DIR, str(kb_id), "images", str(doc_id))
    os.makedirs(img_dir, exist_ok=True)
    raw_images = _extract_mineru_images(data)
    images: list[dict] = []
    name_to_src: dict[str, str] = {}
    for item in raw_images:
        name = item.get("name", "img.png")
        b64 = item.get("b64", "")
        url_img = item.get("url", "")
        safe_name = os.path.basename(name).replace(" ", "_")
        if not os.path.splitext(safe_name)[1]:
            safe_name += ".png"
        save_path = os.path.join(img_dir, safe_name)
        try:
            if b64:
                payload = b64.split(",", 1)[-1] if "," in b64 else b64
                with open(save_path, "wb") as f:
                    f.write(base64.b64decode(payload))
            elif url_img.startswith("http"):
                with httpx.Client(timeout=30.0) as c:
                    r = c.get(url_img)
                    if r.status_code == 200:
                        with open(save_path, "wb") as f:
                            f.write(r.content)
            else:
                continue
            src = f"/api/admin/documents/{doc_id}/images/{safe_name}"
            images.append({"id": safe_name, "name": safe_name, "src": src})
            name_to_src[safe_name] = src
        except Exception as e:  # noqa: BLE001
            logger.warning("保存 MinerU 图片失败 %s: %s", safe_name, e)

    def _replace(m: re.Match) -> str:
        alt, src = m.group(1), m.group(2)
        if src.startswith(("http://", "https://", "data:")):
            return m.group(0)
        basename = os.path.basename(src.split("?", 1)[0])
        return f"![{alt}]({name_to_src[basename]})" if basename in name_to_src else m.group(0)

    markdown = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, markdown)
    logger.info("MinerU 异步解析成功 doc_id=%d: %d 字符, %d 张图片",
                doc_id, len(markdown), len(images))
    return {
        "pages": [{"page_num": 1, "text": markdown, "tables": [], "images": images}],
        "total_pages": data.get("total_pages", 1) or 1,
        "format": "mineru",
        "markdown": markdown,
    }


def parse_file_to_text(file_path: str, file_type: str) -> str:
    """解析文档为纯文本（仅用于 txt/md）。PDF/DOC/DOCX 需通过 parse_pdf_with_mineru 解析。

    file_type 不含点号。"""
    ext = (file_type or "").lower().lstrip(".")
    if ext in ("txt", "md", "markdown"):
        return _read_text(file_path)
    # PDF/DOC/DOCX 的文本提取由 parse_pdf_with_mineru 在解析阶段完成
    return ""
