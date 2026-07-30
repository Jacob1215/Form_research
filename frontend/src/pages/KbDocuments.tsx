import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  apiDelete,
  apiGet,
  apiPost,
  fetchKnowledgeBases,
  type DocumentItem,
  type DocumentListResponse,
  type KnowledgeBase,
} from '../api'

const MAX_FILE_SIZE = 50 * 1024 * 1024 // 50MB
const MAX_FILES = 20
const ALLOWED_EXT = ['pdf', 'doc', 'docx', 'txt', 'md', 'markdown']

function formatTime(t?: string): string {
  if (!t) return '-'
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return t
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatSize(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function getExt(name: string): string {
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? name.slice(idx + 1).toLowerCase() : ''
}

function fileTypeLabel(ext: string): string {
  if (ext === 'pdf') return 'PDF'
  if (ext === 'doc' || ext === 'docx') return 'Word'
  if (ext === 'txt') return 'TXT'
  if (ext === 'md' || ext === 'markdown') return 'Markdown'
  return ext ? ext.toUpperCase() : '-'
}

interface StatusBadge {
  className: string
  label: string
}

function statusOf(s: string): StatusBadge {
  switch (s) {
    case 'success':
    case 'completed':
    case 'done':
      return { className: 'status-success', label: '解析成功' }
    case 'parsing':
    case 'processing':
      return { className: 'status-info', label: '解析中' }
    case 'pending':
    case 'queued':
      return { className: 'status-pending', label: '待解析' }
    case 'failed':
    case 'error':
      return { className: 'status-error', label: '解析失败' }
    default:
      return { className: 'status-pending', label: s || '待解析' }
  }
}

function isPending(s: string): boolean {
  return (
    s === 'pending' || s === 'parsing' || s === 'processing' || s === 'queued'
  )
}

interface UploadProgress {
  id: string
  name: string
  percent: number
  status: 'uploading' | 'success' | 'error'
  message?: string
}

export default function KbDocuments() {
  const { id: kbIdParam } = useParams<{ id: string }>()
  const kbId = kbIdParam || ''

  const [kb, setKb] = useState<KnowledgeBase | null>(null)
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [uploads, setUploads] = useState<UploadProgress[]>([])
  const [uploading, setUploading] = useState(false)

  const [previewDoc, setPreviewDoc] = useState<DocumentItem | null>(null)
  const [previewLoading, setPreviewLoading] = useState(false)

  const [actionLoading, setActionLoading] = useState<Record<string, boolean>>({})

  const fileInputRef = useRef<HTMLInputElement>(null)
  const pollTimerRef = useRef<number | null>(null)

  const loadKb = useCallback(async () => {
    if (!kbId) return
    try {
      const res = await fetchKnowledgeBases()
      const found = (res.items || []).find((k) => String(k.id) === String(kbId))
      if (found) setKb(found)
    } catch {
      /* ignore — name is decorative */
    }
  }, [kbId])

  const loadDocs = useCallback(async () => {
    if (!kbId) return
    try {
      const res = await apiGet<DocumentListResponse>(
        `/api/admin/knowledge-bases/${kbId}/documents`,
      )
      setDocs(res.items || [])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [kbId])

  // initial load
  useEffect(() => {
    loadKb()
    loadDocs()
  }, [loadKb, loadDocs])

  // poll while any doc is pending/parsing
  useEffect(() => {
    const hasPending = docs.some((d) => isPending(d.parse_status))
    if (hasPending) {
      pollTimerRef.current = window.setInterval(() => {
        loadDocs()
      }, 3000)
    }
    return () => {
      if (pollTimerRef.current !== null) {
        clearInterval(pollTimerRef.current)
        pollTimerRef.current = null
      }
    }
  }, [docs, loadDocs])

  const validateFiles = (files: FileList | File[]): File[] => {
    const arr = Array.from(files)
    if (arr.length > MAX_FILES) {
      alert(`单次最多上传 ${MAX_FILES} 个文件`)
      return []
    }
    const valid: File[] = []
    for (const f of arr) {
      const ext = getExt(f.name)
      if (!ALLOWED_EXT.includes(ext)) {
        alert(`文件「${f.name}」格式不支持，仅支持 PDF/DOC/DOCX/TXT/MD`)
        continue
      }
      if (f.size > MAX_FILE_SIZE) {
        alert(`文件「${f.name}」超过 50MB 限制`)
        continue
      }
      valid.push(f)
    }
    return valid
  }

  const uploadOne = async (file: File): Promise<void> => {
    const uid = `${file.name}_${Date.now()}_${Math.random().toString(36).slice(2)}`
    const prog: UploadProgress = {
      id: uid,
      name: file.name,
      percent: 0,
      status: 'uploading',
    }
    setUploads((prev) => [...prev, prog])

    return new Promise<void>((resolve) => {
      const xhr = new XMLHttpRequest()
      const formData = new FormData()
      formData.append('files', file)

      xhr.upload.addEventListener('progress', (e) => {
        if (e.lengthComputable) {
          const percent = Math.round((e.loaded / e.total) * 100)
          setUploads((prev) =>
            prev.map((p) => (p.id === uid ? { ...p, percent } : p)),
          )
        }
      })

      xhr.addEventListener('load', () => {
        const ok = xhr.status >= 200 && xhr.status < 300
        if (ok) {
          setUploads((prev) =>
            prev.map((p) =>
              p.id === uid ? { ...p, percent: 100, status: 'success' } : p,
            ),
          )
        } else {
          let msg = `上传失败 (${xhr.status})`
          try {
            const data = JSON.parse(xhr.responseText)
            if (data.detail || data.message) msg = data.detail || data.message
          } catch {
            /* ignore */
          }
          setUploads((prev) =>
            prev.map((p) =>
              p.id === uid ? { ...p, status: 'error', message: msg } : p,
            ),
          )
        }
        resolve()
      })

      xhr.addEventListener('error', () => {
        setUploads((prev) =>
          prev.map((p) =>
            p.id === uid
              ? { ...p, status: 'error', message: '网络错误' }
              : p,
          ),
        )
        resolve()
      })

      xhr.addEventListener('abort', () => {
        setUploads((prev) =>
          prev.map((p) =>
            p.id === uid ? { ...p, status: 'error', message: '已取消' } : p,
          ),
        )
        resolve()
      })

      xhr.open('POST', `/api/admin/knowledge-bases/${kbId}/documents`)
      xhr.send(formData)
    })
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (!files || files.length === 0) return
    const valid = validateFiles(files)
    // reset input so selecting the same file again re-triggers
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (valid.length === 0) return

    setUploading(true)
    try {
      // upload sequentially to keep progress bars readable
      for (const f of valid) {
        await uploadOne(f)
      }
      await loadDocs()
      // clear finished upload entries after a short delay
      setTimeout(() => {
        setUploads((prev) => prev.filter((u) => u.status === 'uploading'))
      }, 1500)
    } finally {
      setUploading(false)
    }
  }

  const triggerFileInput = () => {
    fileInputRef.current?.click()
  }

  const handlePreview = async (doc: DocumentItem) => {
    setPreviewLoading(true)
    setPreviewDoc(doc)
    try {
      const detail = await apiGet<DocumentItem>(`/api/admin/documents/${doc.id}`)
      setPreviewDoc({ ...doc, ...detail })
    } catch (err) {
      setPreviewDoc({
        ...doc,
        parsed_text: `加载预览失败：${err instanceof Error ? err.message : '未知错误'}`,
      })
    } finally {
      setPreviewLoading(false)
    }
  }

  const closePreview = () => {
    setPreviewDoc(null)
    setPreviewLoading(false)
  }

  useEffect(() => {
    if (!previewDoc) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closePreview()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [previewDoc])

  const handleDownload = (doc: DocumentItem) => {
    window.open(`/api/admin/documents/${doc.id}/download`, '_blank')
  }

  const handleReparse = async (doc: DocumentItem) => {
    setActionLoading((prev) => ({ ...prev, [doc.id]: true }))
    try {
      await apiPost(`/api/admin/documents/${doc.id}/reparse`, {})
      await loadDocs()
    } catch (err) {
      alert(err instanceof Error ? err.message : '重新解析失败')
    } finally {
      setActionLoading((prev) => ({ ...prev, [doc.id]: false }))
    }
  }

  const handleDelete = async (doc: DocumentItem) => {
    if (!window.confirm(`确认删除文档「${doc.file_name}」吗？`)) return
    setActionLoading((prev) => ({ ...prev, [doc.id]: true }))
    try {
      await apiDelete(`/api/admin/documents/${doc.id}`)
      await loadDocs()
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    } finally {
      setActionLoading((prev) => ({ ...prev, [doc.id]: false }))
    }
  }

  return (
    <main className="content">
      <div className="content-inner">
        {/* Breadcrumb */}
        <nav className="breadcrumb" aria-label="面包屑">
          <Link to="/admin/kb">知识库管理</Link>
          <span className="crumb-sep">
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </span>
          <span className="crumb-current">{kb?.name || '知识库文档'}</span>
        </nav>

        {/* Upload Card */}
        <section className="card">
          <div className="card-head">
            <div className="card-title">文档上传</div>
            <div className="card-subtitle">
              支持PDF、Word、TXT、Markdown格式，单次最多20个文件，单个文件不超过50MB
            </div>
          </div>
          <div className="card-body">
            <label className="upload-zone" htmlFor="file-input">
              <span className="upload-icon">
                <svg
                  width="48"
                  height="48"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <path d="M16 16l-4-4-4 4" />
                  <path d="M12 12v9" />
                  <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3" />
                  <polyline points="16 16 12 12 8 16" />
                </svg>
              </span>
              <span className="upload-text">点击选择文件（支持多选）</span>
              <span className="upload-hint">PDF、DOCX、TXT、MD · 单文件最大 50MB</span>
              <input
                type="file"
                id="file-input"
                multiple
                ref={fileInputRef}
                onChange={handleFileChange}
                accept=".pdf,.doc,.docx,.txt,.md,.markdown"
              />
            </label>

            {uploads.length > 0 && (
              <div className="upload-progress">
                {uploads.map((u) => (
                  <div className="progress-file" key={u.id}>
                    <div className="progress-file-head">
                      <span className="progress-file-name">
                        <svg
                          width="16"
                          height="16"
                          viewBox="0 0 24 24"
                          fill="none"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        >
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                          <line x1="9" y1="13" x2="15" y2="13" />
                        </svg>
                        {u.name}
                      </span>
                      <span
                        className={`progress-status ${
                          u.status === 'success'
                            ? 'is-success'
                            : u.status === 'error'
                              ? 'is-error'
                              : ''
                        }`}
                      >
                        {u.status === 'uploading'
                          ? '上传中...'
                          : u.status === 'success'
                            ? '上传完成'
                            : u.message || '上传失败'}
                      </span>
                    </div>
                    <div className="progress-bar">
                      <div
                        className={`progress-bar-fill ${
                          u.status === 'error'
                            ? 'is-error'
                            : u.status === 'success'
                              ? 'is-success'
                              : ''
                        }`}
                        style={{ width: `${u.percent}%` }}
                      />
                    </div>
                    <div className="progress-meta">
                      <span className="progress-status">
                        {u.status === 'uploading' ? `${u.percent}%` : ''}
                      </span>
                      <span className="progress-percent">{u.percent}%</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {uploading && (
              <div className="progress-status" style={{ marginTop: 8 }}>
                正在上传，请稍候...
              </div>
            )}
          </div>
        </section>

        {/* Document List Table */}
        <section className="card">
          <div className="card-head">
            <div className="card-title">文档列表</div>
            <div className="card-subtitle">共 {docs.length} 个文档</div>
          </div>
          <div className="card-body" style={{ padding: '16px 0 0' }}>
            {error && (
              <div className="error-banner" style={{ margin: '0 16px 16px' }}>
                {error}
              </div>
            )}
            <div className="doc-table-wrap">
              <table className="doc-table">
                <thead>
                  <tr>
                    <th>文档名称</th>
                    <th>文件类型</th>
                    <th>文件大小</th>
                    <th>上传时间</th>
                    <th>解析状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && (
                    <tr>
                      <td colSpan={6} className="loading-row">
                        加载中...
                      </td>
                    </tr>
                  )}
                  {!loading && docs.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-state">
                        暂无文档，请上传
                      </td>
                    </tr>
                  )}
                  {!loading &&
                    docs.map((doc) => {
                      const ext = getExt(doc.file_name)
                      const st = statusOf(doc.parse_status)
                      const failed = st.label === '解析失败'
                      const busy = !!actionLoading[doc.id]
                      return (
                        <tr key={doc.id}>
                          <td>
                            <span className="doc-name">
                              <svg
                                width="16"
                                height="16"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                <polyline points="14 2 14 8 20 8" />
                                <line x1="16" y1="13" x2="8" y2="13" />
                                <line x1="16" y1="17" x2="8" y2="17" />
                              </svg>
                              {doc.file_name}
                            </span>
                          </td>
                          <td>
                            <span className="doc-type">{fileTypeLabel(ext)}</span>
                          </td>
                          <td className="doc-size">{formatSize(doc.file_size)}</td>
                          <td className="doc-time">{formatTime(doc.created_at)}</td>
                          <td>
                            <span className={`status-badge ${st.className}`}>
                              <span className="status-dot" />
                              {st.label}
                              {doc.chunk_count !== undefined && doc.chunk_count > 0 && st.label === '解析成功' && (
                                <span style={{ marginLeft: 4, color: 'var(--qa-muted-foreground)' }}>
                                  · {doc.chunk_count} 块
                                </span>
                              )}
                            </span>
                          </td>
                          <td>
                            <div className="actions">
                              <button
                                className="action-link"
                                type="button"
                                onClick={() => handlePreview(doc)}
                                disabled={busy}
                              >
                                <svg
                                  width="15"
                                  height="15"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                                  <circle cx="12" cy="12" r="3" />
                                </svg>
                                预览
                              </button>
                              <button
                                className="action-link"
                                type="button"
                                onClick={() => handleDownload(doc)}
                                disabled={busy}
                              >
                                <svg
                                  width="15"
                                  height="15"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                  <polyline points="7 10 12 15 17 10" />
                                  <line x1="12" y1="15" x2="12" y2="3" />
                                </svg>
                                下载
                              </button>
                              {failed && doc.error_message && (
                                <button
                                  className="action-link action-info"
                                  type="button"
                                  title={doc.error_message}
                                  onClick={() => alert(doc.error_message)}
                                  disabled={busy}
                                >
                                  <svg
                                    width="15"
                                    height="15"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                  >
                                    <circle cx="12" cy="12" r="10" />
                                    <line x1="12" y1="16" x2="12" y2="12" />
                                    <line x1="12" y1="8" x2="12.01" y2="8" />
                                  </svg>
                                  查看原因
                                </button>
                              )}
                              <button
                                className="action-link"
                                type="button"
                                onClick={() => handleReparse(doc)}
                                disabled={busy}
                              >
                                <svg
                                  width="15"
                                  height="15"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <polyline points="23 4 23 10 17 10" />
                                  <polyline points="1 20 1 14 7 14" />
                                  <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
                                </svg>
                                重新解析
                              </button>
                              <button
                                className="action-link action-danger"
                                type="button"
                                onClick={() => handleDelete(doc)}
                                disabled={busy}
                              >
                                <svg
                                  width="15"
                                  height="15"
                                  viewBox="0 0 24 24"
                                  fill="none"
                                  stroke="currentColor"
                                  strokeWidth="2"
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                >
                                  <polyline points="3 6 5 6 21 6" />
                                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                </svg>
                                删除
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                </tbody>
              </table>
            </div>
            <div className="table-footnote">
              解析失败的文档可通过「查看原因」排查问题后重新解析。
            </div>
          </div>
        </section>
      </div>

      {/* Preview modal */}
      {previewDoc && (
        <div
          className="modal-backdrop is-open"
          onClick={(e) => {
            if (e.target === e.currentTarget) closePreview()
          }}
        >
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="preview-title">
            <div className="modal-head">
              <h2 className="modal-title" id="preview-title">
                {previewDoc.file_name}
              </h2>
              <button
                className="modal-close"
                type="button"
                aria-label="关闭"
                onClick={closePreview}
              >
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="modal-body">
              {previewLoading ? (
                '加载中...'
              ) : (
                previewDoc.parsed_text || '（该文档暂无可预览的解析文本）'
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
