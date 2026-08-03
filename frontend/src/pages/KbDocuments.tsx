import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import MarkdownRenderer from '../components/MarkdownRenderer'
import rehypeHighlight from 'rehype-highlight'
import rehypeRaw from 'rehype-raw'
import {
  apiDelete,
  apiGet,
  fetchKnowledgeBases,
  parseDocument,
  type DocumentItem,
  type DocumentListResponse,
  type FolderUploadResponse,
  type KnowledgeBase,
  type ParsedContent,
} from '../api'

const MAX_FILE_SIZE = 50 * 1024 * 1024
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

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function buildHighlightedHtml(
  text: string,
  query: string,
  startIndex: number,
  activeIndex: number,
): { html: string; count: number } {
  const escaped = escapeHtml(text)
  const trimmed = query.trim()
  if (!trimmed) return { html: escaped, count: 0 }
  const escapedQuery = escapeHtml(trimmed).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const regex = new RegExp(escapedQuery, 'gi')
  let count = 0
  const html = escaped.replace(regex, (match) => {
    const globalIndex = startIndex + count
    const isActive = globalIndex === activeIndex
    count++
    const cls = isActive ? 'highlight-active' : ''
    return `<mark class="${cls}" id="match-${globalIndex}">${match}</mark>`
  })
  return { html, count }
}

function renderParseStatus(status?: string) {
  switch (status) {
    case 'parsing':
      return <span className="parse-badge is-parsing">解析中</span>
    case 'done':
      return <span className="parse-badge is-done">已解析</span>
    case 'error':
      return <span className="parse-badge is-error">解析失败</span>
    default:
      return <span className="parse-badge is-pending">待解析</span>
  }
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

  const [searchQuery, setSearchQuery] = useState('')
  const [activeMatch, setActiveMatch] = useState(0)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)

  const loadKb = useCallback(async () => {
    if (!kbId) return
    try {
      const res = await fetchKnowledgeBases()
      const found = (res.items || []).find((k) => String(k.id) === String(kbId))
      if (found) setKb(found)
    } catch {
      /* ignore */
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

  useEffect(() => {
    loadKb()
    loadDocs()
  }, [loadKb, loadDocs])

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
            if (data.detail || data.message) {
              const detail = data.detail || data.message
              msg = Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg ?? String(e)).join('; ') : String(detail)
            }
          } catch { /* ignore */ }
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
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (valid.length === 0) return

    setUploading(true)
    try {
      for (const f of valid) {
        await uploadOne(f)
      }
      await loadDocs()
      setTimeout(() => {
        setUploads((prev) => prev.filter((u) => u.status === 'uploading'))
      }, 1500)
    } catch (err) {
      console.error('文件上传异常:', err)
    } finally {
      setUploading(false)
    }
  }

  const triggerFileInput = () => {
    fileInputRef.current?.click()
  }

  const handleFolderChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    // 先快照文件列表，再清空 input：input.value='' 会清空 FileList（live 引用），
    // 若先清空再遍历，files 已空 → 一个文件都发不出去（422 files missing）
    const fileArr = Array.from(e.target.files ?? [])
    if (fileArr.length === 0) return
    if (folderInputRef.current) folderInputRef.current.value = ''

    setUploading(true)
    const uid = `folder_${Date.now()}`
    setUploads((prev) => [...prev, {
      id: uid,
      name: `文件夹上传（${fileArr.length} 个文件）`,
      percent: 0,
      status: 'uploading',
    }])

    try {
      const formData = new FormData()
      const relPaths: string[] = []
      for (const f of fileArr) {
        // 记录相对路径（单独 JSON 字段传递，避免塞进 multipart filename 被解析器丢目录）
        const relPath = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
        relPaths.push(relPath)
        // 文件用普通文件名（与单文件上传 uploadOne 一致），规避解析器对 filename 中路径的处理差异
        formData.append('files', f)
      }
      // 相对路径与 files 按下标一一对应
      formData.append('relative_paths', JSON.stringify(relPaths))

      const xhr = new XMLHttpRequest()
      xhr.upload.addEventListener('progress', (ev) => {
        if (ev.lengthComputable) {
          const percent = Math.round((ev.loaded / ev.total) * 100)
          setUploads((prev) => prev.map((p) => p.id === uid ? { ...p, percent } : p))
        }
      })

      await new Promise<void>((resolve) => {
        xhr.addEventListener('load', () => {
          const ok = xhr.status >= 200 && xhr.status < 300
          if (ok) {
            try {
              const data = JSON.parse(xhr.responseText) as FolderUploadResponse
              setUploads((prev) => prev.map((p) =>
                p.id === uid ? { ...p, percent: 100, status: 'success', name: `文件夹上传成功：${data.items.length} 个 md，${data.image_count} 张图片` } : p
              ))
            } catch {
              setUploads((prev) => prev.map((p) =>
                p.id === uid ? { ...p, percent: 100, status: 'success' } : p
              ))
            }
          } else {
            let msg = `上传失败 (${xhr.status})`
            try {
              const data = JSON.parse(xhr.responseText)
              if (data.detail || data.message) {
                const detail = data.detail || data.message
                msg = Array.isArray(detail) ? detail.map((e: { msg?: string }) => e.msg ?? String(e)).join('; ') : String(detail)
              }
            } catch { /* ignore */ }
            setUploads((prev) => prev.map((p) =>
              p.id === uid ? { ...p, status: 'error', message: msg } : p
            ))
          }
          resolve()
        })
        xhr.addEventListener('error', () => {
          setUploads((prev) => prev.map((p) =>
            p.id === uid ? { ...p, status: 'error', message: '网络错误' } : p
          ))
          resolve()
        })
        xhr.open('POST', `/api/admin/knowledge-bases/${kbId}/documents/folder`)
        xhr.send(formData)
      })

      await loadDocs()
      setTimeout(() => {
        setUploads((prev) => prev.filter((u) => u.status === 'uploading'))
      }, 2500)
    } catch (err) {
      console.error('文件夹上传异常:', err)
      setUploads((prev) => prev.map((p) =>
        p.id === uid ? { ...p, status: 'error', message: err instanceof Error ? err.message : '上传异常' } : p
      ))
    } finally {
      setUploading(false)
    }
  }

  const triggerFolderInput = () => {
    folderInputRef.current?.click()
  }

  const handlePreview = async (doc: DocumentItem) => {
    setSearchQuery('')
    setActiveMatch(0)
    setPreviewLoading(true)
    setPreviewDoc(doc)
    try {
      const detail = await apiGet<DocumentItem>(`/api/admin/documents/${doc.id}`)
      setPreviewDoc({ ...doc, ...detail })
    } catch (err) {
      setPreviewDoc({
        ...doc,
        content_text: `加载预览失败：${err instanceof Error ? err.message : '未知错误'}`,
      })
    } finally {
      setPreviewLoading(false)
    }
  }

  const closePreview = () => {
    setPreviewDoc(null)
    setPreviewLoading(false)
    setSearchQuery('')
    setActiveMatch(0)
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

  const handleParse = async (doc: DocumentItem) => {
    setActionLoading((prev) => ({ ...prev, [doc.id]: true }))
    try {
      await parseDocument(doc.id)
      await loadDocs()
    } catch (err) {
      alert(err instanceof Error ? err.message : '解析失败')
    } finally {
      setActionLoading((prev) => ({ ...prev, [doc.id]: false }))
    }
  }

  const parsedContent = useMemo<ParsedContent | null>(() => {
    if (!previewDoc?.parsed_content) return null
    try {
      return JSON.parse(previewDoc.parsed_content) as ParsedContent
    } catch {
      return null
    }
  }, [previewDoc])

  const highlightedPages = useMemo(() => {
    if (!parsedContent) return []
    const q = searchQuery
    let globalIndex = 0
    return parsedContent.pages.map((page) => {
      const { html, count } = buildHighlightedHtml(page.text, q, globalIndex, activeMatch)
      globalIndex += count
      return { page, html, count }
    })
  }, [parsedContent, searchQuery, activeMatch])

  const isMdPreview = previewDoc && (previewDoc.file_type === 'md' || previewDoc.file_type === 'markdown')

  // md 搜索高亮
  const highlightedMdContent = useMemo(() => {
    const text = previewDoc?.content_text || ''
    const q = searchQuery.trim()
    if (!q || !text) return ''
    const escapedQ = q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(`(${escapedQ})`, 'gi')
    let globalIndex = 0
    return text.replace(regex, (match) => {
      const idx = globalIndex
      globalIndex++
      const isActive = idx === activeMatch
      return `<mark class="${isActive ? 'highlight-active' : ''}" id="match-${idx}">${match}</mark>`
    })
  }, [previewDoc?.content_text, searchQuery, activeMatch])

  const mdTotalMatches = useMemo(() => {
    if (!searchQuery.trim() || !previewDoc?.content_text) return 0
    const escapedQ = searchQuery.trim().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const regex = new RegExp(escapedQ, 'gi')
    const matches = previewDoc.content_text.match(regex)
    return matches ? matches.length : 0
  }, [searchQuery, previewDoc?.content_text])

  const totalMatches = isMdPreview ? mdTotalMatches : highlightedPages.reduce((sum, p) => sum + p.count, 0)

  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchQuery(e.target.value)
    setActiveMatch(0)
  }

  const navigateMatch = (dir: number) => {
    if (totalMatches === 0) return
    setActiveMatch((prev) => {
      const next = prev + dir
      if (next < 0) return totalMatches - 1
      if (next >= totalMatches) return 0
      return next
    })
  }

  useEffect(() => {
    if (totalMatches > 0) {
      const timer = setTimeout(() => {
        if (isMdPreview) {
          // md 预览：用 querySelector 找 mark 元素
          const marks = document.querySelectorAll('.markdown-preview mark[id^="match-"]')
          const el = marks[activeMatch] as HTMLElement | undefined
          el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        } else {
          const el = document.getElementById(`match-${activeMatch}`)
          el?.scrollIntoView({ behavior: 'smooth', block: 'center' })
        }
      }, 100)
      return () => clearTimeout(timer)
    }
  }, [activeMatch, totalMatches, isMdPreview])

  return (
    <main className="content">
      <div className="content-inner">
        <nav className="breadcrumb" aria-label="面包屑">
          <Link to="/admin/kb">知识库管理</Link>
          <span className="crumb-sep">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 18 15 12 9 6" />
            </svg>
          </span>
          <span className="crumb-current">{kb?.name || '知识库文档'}</span>
        </nav>

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
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
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

            <button
              className="btn btn-secondary upload-folder-btn"
              type="button"
              onClick={triggerFolderInput}
              disabled={uploading}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
              </svg>
              上传 Markdown 文件夹
            </button>
            <input
              type="file"
              ref={folderInputRef}
              onChange={handleFolderChange}
              style={{ display: 'none' }}
              // @ts-expect-error webkitdirectory 非标准属性
              webkitdirectory=""
              directory=""
              multiple
            />

            {uploads.length > 0 && (
              <div className="upload-progress">
                {uploads.map((u) => (
                  <div className="progress-file" key={u.id}>
                    <div className="progress-file-head">
                      <span className="progress-file-name">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                          <line x1="9" y1="13" x2="15" y2="13" />
                        </svg>
                        {u.name}
                      </span>
                      <span className={`progress-status ${u.status === 'success' ? 'is-success' : u.status === 'error' ? 'is-error' : ''}`}>
                        {u.status === 'uploading' ? '上传中...' : u.status === 'success' ? '上传完成' : u.message || '上传失败'}
                      </span>
                    </div>
                    <div className="progress-bar">
                      <div className={`progress-bar-fill ${u.status === 'error' ? 'is-error' : u.status === 'success' ? 'is-success' : ''}`} style={{ width: `${u.percent}%` }} />
                    </div>
                    <div className="progress-meta">
                      <span className="progress-status">{u.status === 'uploading' ? `${u.percent}%` : ''}</span>
                      <span className="progress-percent">{u.percent}%</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
            {uploading && <div className="progress-status" style={{ marginTop: 8 }}>正在上传，请稍候...</div>}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <div className="card-title">文档列表</div>
            <div className="card-subtitle">共 {docs.length} 个文档</div>
          </div>
          <div className="card-body" style={{ padding: '16px 0 0' }}>
            {error && <div className="error-banner" style={{ margin: '0 16px 16px' }}>{error}</div>}
            <div className="doc-table-wrap">
              <table className="doc-table">
                <thead>
                  <tr>
                    <th>文档名称</th>
                    <th>文件类型</th>
                    <th>文件大小</th>
                    <th>上传时间</th>
                    <th>状态</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && (
                    <tr>
                      <td colSpan={6} className="loading-row">加载中...</td>
                    </tr>
                  )}
                  {!loading && docs.length === 0 && (
                    <tr>
                      <td colSpan={6} className="empty-state">暂无文档，请上传</td>
                    </tr>
                  )}
                  {!loading && docs.map((doc) => {
                    const ext = getExt(doc.file_name)
                    const busy = !!actionLoading[doc.id]
                    return (
                      <tr key={doc.id}>
                        <td>
                          <span className="doc-name">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                              <polyline points="14 2 14 8 20 8" />
                              <line x1="16" y1="13" x2="8" y2="13" />
                              <line x1="16" y1="17" x2="8" y2="17" />
                            </svg>
                            {doc.file_name}
                            {doc.relative_path && (
                              <span className="doc-path">{doc.relative_path}</span>
                            )}
                          </span>
                        </td>
                        <td><span className="doc-type">{fileTypeLabel(ext)}</span></td>
                        <td className="doc-size">{formatSize(doc.file_size)}</td>
                        <td className="doc-time">{formatTime(doc.created_at)}</td>
                        <td>{renderParseStatus(doc.parse_status)}</td>
                        <td>
                          <div className="actions">
                            <button className="action-link" type="button" onClick={() => handlePreview(doc)} disabled={busy}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                                <circle cx="12" cy="12" r="3" />
                              </svg>
                              预览
                            </button>
                            {(ext === 'pdf' || ext === 'md' || ext === 'markdown') && (
                              <button
                                className="action-link action-info"
                                type="button"
                                onClick={() => handleParse(doc)}
                                disabled={busy || doc.parse_status === 'parsing'}
                              >
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                                </svg>
                                {doc.parse_status === 'parsing' ? '解析中' : '解析'}
                              </button>
                            )}
                            <button className="action-link" type="button" onClick={() => handleDownload(doc)} disabled={busy}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                                <polyline points="7 10 12 15 17 10" />
                                <line x1="12" y1="15" x2="12" y2="3" />
                              </svg>
                              下载
                            </button>
                            <button className="action-link action-danger" type="button" onClick={() => handleDelete(doc)} disabled={busy}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
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
          </div>
        </section>
      </div>

      {previewDoc && (
        <div className="modal-backdrop is-open" onClick={(e) => { if (e.target === e.currentTarget) closePreview() }}>
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="preview-title" style={{ maxWidth: 900 }}>
            <div className="modal-head">
              <h2 className="modal-title" id="preview-title">{previewDoc.file_name}</h2>
              <button className="modal-close" type="button" aria-label="关闭" onClick={closePreview}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="18" y1="6" x2="6" y2="18" />
                  <line x1="6" y1="6" x2="18" y2="18" />
                </svg>
              </button>
            </div>
            <div className="preview-toolbar">
              <input
                className="preview-search-input"
                type="text"
                placeholder="搜索文档内容..."
                value={searchQuery}
                onChange={handleSearchChange}
              />
              {searchQuery.trim() && (
                <>
                  <span className="preview-search-count">
                    {totalMatches > 0 ? `${activeMatch + 1}/${totalMatches} 匹配` : '无匹配'}
                  </span>
                  <button className="preview-nav-btn" type="button" onClick={() => navigateMatch(-1)} disabled={totalMatches === 0} aria-label="上一个匹配">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="18 15 12 9 6 15" />
                    </svg>
                  </button>
                  <button className="preview-nav-btn" type="button" onClick={() => navigateMatch(1)} disabled={totalMatches === 0} aria-label="下一个匹配">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </button>
                </>
              )}
            </div>
            <div className="preview-body">
              {previewLoading ? (
                <div className="preview-text">加载中...</div>
              ) : (previewDoc.file_type === 'md' || previewDoc.file_type === 'markdown') && previewDoc.content_text ? (
                <div className="markdown-preview">
                  <MarkdownRenderer
                    markdown={searchQuery.trim() && highlightedMdContent ? highlightedMdContent : previewDoc.content_text}
                    rehypePlugins={[rehypeHighlight, rehypeRaw]}
                  />
                </div>
              ) : parsedContent && highlightedPages.length > 0 ? (
                highlightedPages.map(({ page, html }, idx) => (
                  <div className="preview-page" key={idx}>
                    <div className="preview-page-num">第 {page.page_num} 页</div>
                    {page.text && <div className="preview-text" dangerouslySetInnerHTML={{ __html: html }} />}
                    {page.tables && page.tables.length > 0 && page.tables.map((table, ti) => (
                      <table className="preview-table" key={`t-${ti}`}>
                        <tbody>
                          {table.map((row, ri) => (
                            <tr key={ri}>
                              {row.map((cell, ci) => (
                                <td key={ci}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ))}
                    {page.images && page.images.length > 0 && page.images.map((img) => (
                      <img className="preview-image" key={img.id} src={img.src} alt={img.id} />
                    ))}
                  </div>
                ))
              ) : (
                <div className="preview-text">{previewDoc.content_text || '（该文档暂无可预览的文本内容）'}</div>
              )}
            </div>
          </div>
        </div>
      )}
    </main>
  )
}
