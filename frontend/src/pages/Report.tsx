/**
 * 报告总结编制页面（V1.1+）。
 *
 * 版本演进：
 * - V1.1：基础报告编制 — 上传图片/文档 + 多轮对话 + docx 导出 + 手动保存。
 * - V1.1.1：支持不选知识库（纯资料编制）；支持上传 docx/txt/md/pdf。
 * - V1.1.2：两阶段生成 — 对话框显示要点，完整报告可展开查看与导出。
 * - V1.1.3：skill 库选择 — 输入 / 触发技能菜单，选中后注入 system prompt。
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import Header from '../components/Header'
import MarkdownRenderer from '../components/MarkdownRenderer'
import { APP_NAME, APP_VERSION } from '../version'
import {
  fetchKnowledgeBases,
  streamReportChat,
  uploadReportFiles,
  exportReportDocx,
  saveReportRecord,
  fetchReportRecords,
  fetchReportRecord,
  deleteReportRecord,
  fetchReportSkills,
  type KnowledgeBase,
  type ReportMessage,
  type ReportDocRef,
  type ReportRecordItem,
  type ReportRecordDetailItem,
  type ReportSkillItem,
  type StreamHandle,
} from '../api'

interface Attachment {
  url: string
  name: string
  kind?: 'image' | 'doc'
  uploading?: boolean
}

interface ReportUiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  images?: string[]
  documents?: ReportDocRef[]
  status?: 'thinking' | 'streaming' | 'done' | 'error'
  error?: string
}

let idCounter = 0
const nextId = () => `r${Date.now()}_${idCounter++}`

function formatTime(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function truncateTitle(t: string, max = 18): string {
  if (!t) return '未命名报告'
  return t.length > max ? t.slice(0, max) + '...' : t
}

export default function Report() {
  /* ---------- 知识库 ---------- */
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [kbLoading, setKbLoading] = useState(true)
  const [selectedKb, setSelectedKb] = useState<KnowledgeBase | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)

  /* ---------- 编辑态 ---------- */
  const [title, setTitle] = useState('')
  const [messages, setMessages] = useState<ReportUiMessage[]>([])
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [streaming, setStreaming] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [exportMsg, setExportMsg] = useState('')
  const [progress, setProgress] = useState('')
  // V1.1.2：完整报告内容（对话框只显示要点，完整报告用于展开查看与 docx 导出）
  const [fullReports, setFullReports] = useState<Record<string, string>>({})
  const [expandedReports, setExpandedReports] = useState<Record<string, boolean>>({})

  // V1.1.3：skill 界面选择
  const [skillList, setSkillList] = useState<ReportSkillItem[]>([])
  const [selectedSkills, setSelectedSkills] = useState<string[]>([])
  const [skillMenuOpen, setSkillMenuOpen] = useState(false)
  const [skillQuery, setSkillQuery] = useState('')
  const [skillHighlight, setSkillHighlight] = useState(0)

  /* ---------- 报告记录 ---------- */
  const [records, setRecords] = useState<ReportRecordItem[]>([])
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [currentRecord, setCurrentRecord] = useState<ReportRecordDetailItem | null>(null)

  const messagesRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const streamRef = useRef<StreamHandle | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const inputWrapRef = useRef<HTMLDivElement>(null)

  /* ----- 加载知识库 ----- */
  useEffect(() => {
    let cancelled = false
    setKbLoading(true)
    fetchKnowledgeBases()
      .then((res) => {
        if (cancelled) return
        const items = res.items || []
        setKbs(items)
        if (items.length > 0) setSelectedKb(items[0])
      })
      .catch((err: unknown) => { if (!cancelled) console.error(err) })
      .finally(() => { if (!cancelled) setKbLoading(false) })
    return () => { cancelled = true }
  }, [])

  /* ----- 加载 skill 清单 ----- */
  useEffect(() => {
    let cancelled = false
    fetchReportSkills()
      .then((res) => { if (!cancelled) setSkillList(res.items || []) })
      .catch((err: unknown) => { if (!cancelled) console.error(err) })
    return () => { cancelled = true }
  }, [])

  /* ----- 加载报告记录 ----- */
  const loadRecords = useCallback(async () => {
    setRecordsLoading(true)
    try {
      const res = await fetchReportRecords()
      setRecords(res.items || [])
    } catch (err) { console.error(err) }
    finally { setRecordsLoading(false) }
  }, [])
  useEffect(() => { loadRecords() }, [loadRecords])

  /* ----- 关闭下拉 ----- */
  useEffect(() => {
    if (!dropdownOpen) return
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [dropdownOpen])

  /* ----- 点击输入区外部关闭 skill 菜单 ----- */
  useEffect(() => {
    if (!skillMenuOpen) return
    const handler = (e: MouseEvent) => {
      if (inputWrapRef.current && !inputWrapRef.current.contains(e.target as Node)) {
        setSkillMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [skillMenuOpen])

  /* ----- 自动滚动 ----- */
  const scrollToBottom = useCallback(() => {
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])
  useEffect(() => { scrollToBottom() }, [messages, currentRecord, scrollToBottom])

  /* ----- 输入框自适应高度 ----- */
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`
  }, [input])

  const selectKb = (kb: KnowledgeBase | null) => {
    setSelectedKb(kb)
    setDropdownOpen(false)
  }

  /* ----- 新报告：清空编辑态并返回编辑视图 ----- */
  const newReport = () => {
    setMessages([])
    setAttachments([])
    setInput('')
    setExportMsg('')
    setProgress('')
    setFullReports({})
    setExpandedReports({})
    setCurrentRecord(null)
    if (streamRef.current) { streamRef.current.abort(); streamRef.current = null }
    setStreaming(false)
  }

  const updateMessage = (id: string, patch: Partial<ReportUiMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)))
  }

  /* ----- 上传图片/文档：选中即上传 ----- */
  const handleFiles = async (files: FileList | null) => {
    if (!files || files.length === 0) return
    const list = Array.from(files)
    setAttachments((prev) => [...prev, ...list.map((f) => ({ url: '', name: f.name, uploading: true }))])
    try {
      const res = await uploadReportFiles(list)
      const items = res.items || []
      let idx = 0
      setAttachments((prev) => {
        const next = [...prev]
        for (let i = 0; i < next.length; i++) {
          if (next[i].uploading) {
            const it = items[idx++]
            if (it) next[i] = { url: it.url, name: it.name, kind: it.kind }
          }
        }
        return next
      })
    } catch (err) {
      setExportMsg(`上传失败：${err instanceof Error ? err.message : '未知错误'}`)
      setAttachments((prev) => prev.filter((a) => !a.uploading))
    }
  }

  const removeAttachment = (url: string) => {
    setAttachments((prev) => prev.filter((a) => a.url !== url))
  }

  /* ----- 发送：历史 + 附件 → 流式编制报告 ----- */
  const sendMessage = useCallback((
    text: string,
    opts?: { images?: string[]; documents?: ReportDocRef[]; baseHistory?: ReportUiMessage[] },
  ) => {
    const trimmed = text.trim()
    const imgUrls = opts?.images ?? attachments
      .filter((a) => a.url && !a.uploading && a.kind !== 'doc').map((a) => a.url)
    const docRefs = opts?.documents ?? attachments
      .filter((a) => a.url && !a.uploading && a.kind === 'doc')
      .map((a) => ({ url: a.url, name: a.name }))
    if ((!trimmed && imgUrls.length === 0 && docRefs.length === 0) || streaming) return

    const userMsg: ReportUiMessage = {
      id: nextId(), role: 'user', content: trimmed, status: 'done',
      images: imgUrls.length ? imgUrls : undefined,
      documents: docRefs.length ? docRefs : undefined,
    }
    const aiMsg: ReportUiMessage = { id: nextId(), role: 'assistant', content: '', status: 'thinking' }
    const base = opts?.baseHistory ?? messages
    setMessages([...base, userMsg, aiMsg])
    setInput('')
    setAttachments([])
    setExportMsg('')
    setProgress('')
    setStreaming(true)

    const aiId = aiMsg.id
    const history: ReportMessage[] = [...base, userMsg].map((m) => ({
      role: m.role, content: m.content, images: m.images, documents: m.documents,
    }))

    let firstToken = true
    streamRef.current = streamReportChat({
      kb_id: selectedKb?.id ?? null,
      title: title.trim() || undefined,
      messages: history,
      skills: selectedSkills,
      onProgress: (msg) => setProgress(msg),
      onReport: (content) => {
        setFullReports((prev) => ({ ...prev, [aiId]: content }))
        setProgress('')
      },
      onToken: (content) => {
        if (firstToken) {
          firstToken = false
          setProgress('')
          updateMessage(aiId, { content, status: 'streaming' })
        } else {
          setMessages((prev) =>
            prev.map((m) => (m.id === aiId ? { ...m, content: m.content + content } : m)),
          )
        }
      },
      onDone: () => {
        setMessages((prev) => prev.map((m) => (m.id === aiId ? { ...m, status: 'done' } : m)))
        setStreaming(false)
        streamRef.current = null
      },
      onError: (err) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === aiId
              ? { ...m, status: 'error', error: err, content: m.content || `请求失败：${err}` }
              : m,
          ),
        )
        setStreaming(false)
        streamRef.current = null
      },
    })
  }, [attachments, messages, selectedKb, streaming, title, selectedSkills])

  const handleSend = () => { sendMessage(input) }

  /* ----- V1.1.3 skill 菜单 ----- */
  const filteredSkills = skillList.filter((s) =>
    (s.name || '').toLowerCase().includes(skillQuery.toLowerCase()),
  )

  const openSkillMenu = () => {
    if (skillList.length === 0) return
    setSkillQuery('')
    setSkillHighlight(0)
    setSkillMenuOpen(true)
  }

  const selectSkill = (name: string) => {
    setSelectedSkills((prev) => (prev.includes(name) ? prev : [...prev, name]))
    // 从输入中删除 /query
    const slashIdx = input.lastIndexOf('/')
    if (slashIdx >= 0) setInput(input.slice(0, slashIdx))
    setSkillMenuOpen(false)
    setSkillQuery('')
    setSkillHighlight(0)
    textareaRef.current?.focus()
  }

  const removeSkill = (name: string) => {
    setSelectedSkills((prev) => prev.filter((s) => s !== name))
  }

  const handleInputChange = (v: string) => {
    setInput(v)
    if (skillList.length === 0) { setSkillMenuOpen(false); return }
    const slashIdx = v.lastIndexOf('/')
    const query = slashIdx >= 0 ? v.slice(slashIdx + 1) : ''
    if (slashIdx >= 0 && !query.includes(' ')) {
      setSkillQuery(query)
      setSkillHighlight(0)
      setSkillMenuOpen(true)
    } else {
      setSkillMenuOpen(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (skillMenuOpen) {
      const count = filteredSkills.length
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSkillHighlight((h) => (count ? (h + 1) % count : 0))
        return
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSkillHighlight((h) => (count ? (h - 1 + count) % count : 0))
        return
      }
      if (e.key === 'Enter' && count > 0) {
        e.preventDefault()
        const hit = filteredSkills[Math.min(skillHighlight, count - 1)]
        if (hit) selectSkill(hit.name)
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setSkillMenuOpen(false)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  /* ----- 重新生成：复用上一条用户输入（含图片/文档）重发 ----- */
  const handleRegenerate = (aiMsg: ReportUiMessage) => {
    if (streaming) return
    const idx = messages.findIndex((m) => m.id === aiMsg.id)
    if (idx <= 0) return
    const userMsg = messages[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return
    const base = messages.filter((m) => m.id !== aiMsg.id)
    sendMessage(userMsg.content, {
      images: userMsg.images,
      documents: userMsg.documents,
      baseHistory: base,
    })
  }

  /* ----- 下载 docx ----- */
  const lastAssistant = [...messages].reverse().find((m) => m.role === 'assistant' && m.content.trim())
  // V1.1.2：下载/保存用完整报告（对话框只显示要点）
  const lastFull = lastAssistant ? fullReports[lastAssistant.id] : undefined
  const canDownload = !!lastFull && !exporting && !streaming

  const downloadContent = async (dlTitle: string, content: string) => {
    if (exporting) return
    setExporting(true)
    setExportMsg('')
    try {
      const blob = await exportReportDocx(dlTitle, content)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${dlTitle}.docx`
      document.body.appendChild(a)
      a.click()
      a.remove()
      setTimeout(() => URL.revokeObjectURL(url), 3000)
      setExportMsg('docx 已生成并开始下载')
    } catch (err) {
      setExportMsg(`导出失败：${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setExporting(false)
    }
  }

  /* ----- 手动保存报告记录 ----- */
  const saveCurrentReport = async () => {
    if (!lastFull || saving) return
    setSaving(true)
    setExportMsg('')
    try {
      await saveReportRecord(title.trim() || '报告总结', lastFull)
      setExportMsg('已保存到左侧报告列表')
      loadRecords()
    } catch (err) {
      setExportMsg(`保存失败：${err instanceof Error ? err.message : '未知错误'}`)
    } finally {
      setSaving(false)
    }
  }

  const openRecord = async (id: string) => {
    try {
      const rec = await fetchReportRecord(id)
      setCurrentRecord(rec)
    } catch (err) {
      setExportMsg(`加载记录失败：${err instanceof Error ? err.message : '未知错误'}`)
    }
  }

  const deleteCurrentRecord = async () => {
    if (!currentRecord) return
    if (!window.confirm('确认删除该报告记录？')) return
    try {
      await deleteReportRecord(currentRecord.id)
      setCurrentRecord(null)
      loadRecords()
    } catch (err) {
      setExportMsg(`删除失败：${err instanceof Error ? err.message : '未知错误'}`)
    }
  }

  const canSend = (!!input.trim() || attachments.some((a) => a.url)) && !streaming

  return (
    <>
      <Header activeNav="report" />

      <main className="chat-main">
        {/* ===== 左侧报告记录侧栏 ===== */}
        <aside className="chat-sidebar">
          <div className="sidebar-header">
            <button className="new-conv-btn" onClick={newReport}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              <span>新报告</span>
            </button>
          </div>
          <div className="sidebar-list">
            {recordsLoading && <div className="sidebar-empty">加载中...</div>}
            {!recordsLoading && records.length === 0 && (
              <div className="sidebar-empty">暂无已保存的报告<br />编制完成后点「保存报告」</div>
            )}
            {records.map((rec) => (
              <button
                key={rec.id}
                className={`conv-item${currentRecord?.id === rec.id ? ' is-active' : ''}`}
                onClick={() => openRecord(rec.id)}
                type="button"
              >
                <div className="conv-item-title">{truncateTitle(rec.title)}</div>
                <div className="conv-item-time">{formatTime(rec.created_at)}</div>
              </button>
            ))}
          </div>
        </aside>

        {/* ===== 右侧主区域 ===== */}
        <div className="chat-content">
          {currentRecord ? (
            /* ---------- 记录详情视图 ---------- */
            <>
              <div className="chat-topbar">
                <div className="report-record-head">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                  <span className="report-record-head-title">{truncateTitle(currentRecord.title, 40)}</span>
                </div>
                <div className="topbar-status">
                  <button className="report-new-btn" type="button" onClick={newReport}>返回编辑</button>
                  <button className="report-delete-btn" type="button" onClick={deleteCurrentRecord}>删除</button>
                  <button
                    className="report-download-btn"
                    type="button"
                    disabled={exporting}
                    onClick={() => downloadContent(currentRecord.title, currentRecord.content)}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" x2="12" y1="15" y2="3" />
                    </svg>
                    <span>{exporting ? '导出中...' : '下载docx'}</span>
                  </button>
                </div>
              </div>
              <div className="chat-messages" ref={messagesRef}>
                <div className="chat-column">
                  <div className="report-record-card">
                    <h1>{currentRecord.title}</h1>
                    <MarkdownRenderer markdown={currentRecord.content} />
                  </div>
                </div>
              </div>
            </>
          ) : (
            /* ---------- 编辑视图 ---------- */
            <>
              <div className="chat-topbar">
                <div className="kb-selector" ref={dropdownRef}>
                  <button
                    className={`kb-trigger${dropdownOpen ? ' open' : ''}`}
                    type="button"
                    onClick={() => setDropdownOpen((v) => !v)}
                  >
                    <span className="kb-trigger-icon">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                        <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                      </svg>
                    </span>
                    <span className="kb-trigger-label">
                      <span className="kb-trigger-hint">{selectedKb ? '已选择知识库' : '未选择知识库'}</span>
                      <span className="kb-name">{selectedKb ? selectedKb.name : kbLoading ? '加载中...' : '不选择知识库'}</span>
                    </span>
                    <span className="kb-chevron">
                      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                        <path d="m6 9 6 6 6-6" />
                      </svg>
                    </span>
                  </button>
                  {dropdownOpen && (
                    <div className="kb-dropdown open" role="listbox" aria-label="请选择知识库">
                      <div className="kb-dropdown-header">请选择知识库</div>
                      <div
                        className={`kb-option${selectedKb === null ? ' selected' : ''}`}
                        role="option"
                        aria-selected={selectedKb === null}
                        onClick={() => selectKb(null)}
                      >
                        <span className="kb-option-icon">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M9 9H5a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h1l3 3V9Zm6 0h4a2 2 0 0 1 2 2v4a2 2 0 0 1-2 2h-1l-3 3V9Z" />
                          </svg>
                        </span>
                        <span className="kb-option-body">
                          <span className="kb-option-name">不选择知识库</span>
                          <span className="kb-option-desc">仅基于输入资料编制报告</span>
                        </span>
                        {selectedKb === null && (
                          <span className="kb-check">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M20 6 9 17l-5-5" />
                            </svg>
                          </span>
                        )}
                      </div>
                      {kbs.length === 0 && (
                        <div className="kb-option" style={{ cursor: 'default' }}>
                          <span className="kb-option-body">
                            <span className="kb-option-name">暂无知识库</span>
                            <span className="kb-option-desc">请前往后台创建知识库</span>
                          </span>
                        </div>
                      )}
                      {kbs.map((kb) => (
                        <div
                          key={kb.id}
                          className={`kb-option${selectedKb?.id === kb.id ? ' selected' : ''}`}
                          role="option"
                          aria-selected={selectedKb?.id === kb.id}
                          onClick={() => selectKb(kb)}
                        >
                          <span className="kb-option-icon">
                            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
                              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
                            </svg>
                          </span>
                          <span className="kb-option-body">
                            <span className="kb-option-name">{kb.name}</span>
                            <span className="kb-option-desc">{kb.description || `共 ${kb.doc_count ?? 0} 篇文档`}</span>
                          </span>
                          {selectedKb?.id === kb.id && (
                            <span className="kb-check">
                              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M20 6 9 17l-5-5" />
                              </svg>
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                <input
                  className="report-title-input"
                  placeholder="请输入报告标题（用于 docx 文件名与首页标题）"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />

                <div className="topbar-status">
                  <button
                    className="report-save-btn"
                    type="button"
                    disabled={!lastFull || saving || streaming}
                    onClick={saveCurrentReport}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                      <polyline points="17 21 17 13 7 13 7 21" />
                      <polyline points="7 3 7 8 15 8" />
                    </svg>
                    <span>{saving ? '保存中...' : '保存报告'}</span>
                  </button>
                  <button className="report-new-btn" type="button" onClick={newReport} disabled={streaming}>新报告</button>
                  <button
                    className="report-download-btn"
                    type="button"
                    disabled={!canDownload}
                    onClick={() => lastFull && downloadContent(title.trim() || '报告总结', lastFull)}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                      <polyline points="7 10 12 15 17 10" />
                      <line x1="12" x2="12" y1="15" y2="3" />
                    </svg>
                    <span>{exporting ? '导出中...' : '下载docx'}</span>
                  </button>
                </div>
              </div>

              {/* 消息区 */}
              <div className="chat-messages" ref={messagesRef}>
                <div className="chat-column">
                  {messages.length === 0 && (
                    <div className="welcome-bubble">
                      <div className="welcome-avatar">
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                          <line x1="16" x2="8" y1="13" y2="13" />
                          <line x1="16" x2="8" y1="17" y2="17" />
                          <polyline points="10 9 9 9 8 9" />
                        </svg>
                      </div>
                      <div className="welcome-text">
                        {selectedKb
                          ? `你好！我是${APP_NAME} ${APP_VERSION} 报告编制助手。请在下方输入报告、参考资料（可上传图片/文档），点击发送后，我会结合知识库「${selectedKb.name}」编制总结报告。`
                          : `你好！我是${APP_NAME} ${APP_VERSION} 报告编制助手。当前未选择知识库，将仅基于您输入的资料编制报告。请输入资料（可上传图片/docx/txt/md/pdf）。`}
                      </div>
                    </div>
                  )}

                  {messages.map((msg) =>
                    msg.role === 'user' ? (
                      <div className="message-row user" key={msg.id}>
                        <div className="message-content">
                          <div className="bubble user-bubble">
                            {msg.content && <p>{msg.content}</p>}
                            {msg.images && msg.images.length > 0 && (
                              <div className="report-msg-images">
                                {msg.images.map((url) => (
                                  <img key={url} src={url} alt="" loading="lazy" />
                                ))}
                              </div>
                            )}
                            {msg.documents && msg.documents.length > 0 && (
                              <div className="report-msg-docs">
                                {msg.documents.map((doc) => (
                                  <span className="report-doc-chip" key={doc.url}>
                                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                      <polyline points="14 2 14 8 20 8" />
                                    </svg>
                                    <span>{doc.name || '参考文档'}</span>
                                  </span>
                                ))}
                              </div>
                            )}
                          </div>
                        </div>
                        <div className="message-avatar user-avatar">
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" />
                            <circle cx="12" cy="7" r="4" />
                          </svg>
                        </div>
                      </div>
                    ) : (
                      <div className="message-row ai" key={msg.id}>
                        <div className="message-avatar ai-avatar">
                          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                            <polyline points="14 2 14 8 20 8" />
                          </svg>
                        </div>
                        <div className="message-content">
                          <div className="bubble ai-bubble">
                            {msg.status === 'thinking' ? (
                              <div className="ai-answer">
                                <span className="typing-dots"><span /><span /><span /></span>
                                <span style={{ fontSize: '13px', color: 'var(--qa-muted-foreground)' }}>
                                  {progress || '正在结合资料编制报告...'}
                                </span>
                              </div>
                            ) : (
                              <div className="ai-answer">
                                {msg.content ? (
                                  // 对话框只显示要点；完整报告见下方「查看完整报告」
                                  <MarkdownRenderer markdown={msg.content} />
                                ) : (
                                  <span style={{ color: 'var(--qa-muted-foreground)' }}>（无内容）</span>
                                )}
                                {msg.status === 'error' && msg.error && (
                                  <span style={{ color: '#dc2626', fontSize: '13px' }}>{msg.error}</span>
                                )}
                              </div>
                            )}
                            {progress && msg.status === 'streaming' && (
                              <div className="report-progress-hint">⏳ {progress}</div>
                            )}
                            {fullReports[msg.id] && (
                              <div className="report-full-toggle">
                                <button
                                  className="report-full-btn"
                                  type="button"
                                  onClick={() =>
                                    setExpandedReports((prev) => ({ ...prev, [msg.id]: !prev[msg.id] }))
                                  }
                                >
                                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                                    <polyline points="14 2 14 8 20 8" />
                                  </svg>
                                  <span>{expandedReports[msg.id] ? '收起完整报告' : '查看完整报告'}</span>
                                </button>
                                {expandedReports[msg.id] && (
                                  <div className="report-full-content">
                                    <MarkdownRenderer markdown={fullReports[msg.id]} />
                                  </div>
                                )}
                              </div>
                            )}
                          </div>
                          {msg.status !== 'thinking' && (
                            <div className="message-meta">
                              <button className="action-btn" type="button" onClick={() => handleRegenerate(msg)} disabled={streaming}>
                                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
                                  <path d="M21 3v5h-5" />
                                  <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
                                  <path d="M8 16H3v5" />
                                </svg>
                                <span>重新生成</span>
                              </button>
                            </div>
                          )}
                        </div>
                      </div>
                    ),
                  )}
                </div>
              </div>
            </>
          )}
        </div>
      </main>

      {/* 输入区（仅编辑态） */}
      {!currentRecord && (
        <div className="chat-input-bar">
          <div className="input-column">
            {attachments.length > 0 && (
              <div className="report-attach-list">
                {attachments.map((att) => (
                  <div className="report-attach-item" key={att.url || att.name}>
                    {att.uploading ? (
                      <span className="report-attach-thumb report-attach-loading">上传中…</span>
                    ) : att.kind === 'doc' ? (
                      <div className="report-attach-doc">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                        </svg>
                        <span>{att.name}</span>
                      </div>
                    ) : (
                      <img className="report-attach-thumb" src={att.url} alt={att.name} />
                    )}
                    <button className="report-attach-remove" type="button" aria-label="删除附件" onClick={() => removeAttachment(att.url)}>
                      ×
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* V1.1.3：已选 skill chips */}
            {selectedSkills.length > 0 && (
              <div className="report-skill-chips">
                {selectedSkills.map((name) => (
                  <span className="report-skill-chip" key={name}>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1M7.7 16.3l-2.1 2.1" />
                    </svg>
                    <span>{name}</span>
                    <button className="report-skill-remove" type="button" aria-label={`移除${name}`} onClick={() => removeSkill(name)}>×</button>
                  </span>
                ))}
              </div>
            )}

            <div className="input-wrapper" ref={inputWrapRef}>
              {skillMenuOpen && filteredSkills.length > 0 && (
                <div className="skill-menu">
                  {filteredSkills.map((s, i) => (
                    <div
                      key={s.name}
                      className={`skill-menu-item${i === skillHighlight ? ' is-active' : ''}`}
                      onMouseEnter={() => setSkillHighlight(i)}
                      onClick={() => selectSkill(s.name)}
                    >
                      <span className="skill-menu-name">{s.name}</span>
                      {s.description && <span className="skill-menu-desc">{s.description}</span>}
                    </div>
                  ))}
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*,.docx,.txt,.md,.pdf"
                multiple
                hidden
                onChange={(e) => { handleFiles(e.target.files); e.target.value = '' }}
              />
              <button className="attach-btn" type="button" aria-label="上传图片或文档" onClick={() => fileInputRef.current?.click()} disabled={streaming}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.57 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
                </svg>
              </button>
              <button className="attach-btn skill-trigger-btn" type="button" aria-label="选择技能" onClick={openSkillMenu} disabled={streaming || skillList.length === 0}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v3m0 12v3M3 12h3m12 0h3M5.6 5.6l2.1 2.1m8.6 8.6 2.1 2.1m0-12.8-2.1 2.1M7.7 16.3l-2.1 2.1" />
                </svg>
              </button>
              <textarea
                className="chat-textarea"
                placeholder={selectedKb ? '输入报告、参考资料（可上传图片/文档），输入 / 选择技能，Enter发送，Shift+Enter换行...' : '未选择知识库，仅凭输入资料编制。请输入内容或上传文档，输入 / 选择技能...'}
                rows={1}
                ref={textareaRef}
                value={input}
                onChange={(e) => handleInputChange(e.target.value)}
                onKeyDown={handleKeyDown}
              />
              <button className="send-btn" type="button" aria-label="发送" disabled={!canSend} onClick={handleSend}>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M14.536 21.686a.5.5 0 0 0 .937-.024l6.5-19a.496.496 0 0 0-.635-.635l-19 6.5a.5.5 0 0 0-.024.937l7.93 3.18a2 2 0 0 1 1.112 1.11z" />
                  <path d="m21.854 2.147-10.94 10.939" />
                </svg>
                <span>发送</span>
              </button>
            </div>

            <div className="input-hint">
              支持图片 / docx / txt / md / pdf
              {exportMsg ? ` · ${exportMsg}` : ''}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
