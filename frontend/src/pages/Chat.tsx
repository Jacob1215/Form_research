import { useCallback, useEffect, useRef, useState } from 'react'
import Header from '../components/Header'
import MarkdownRenderer from '../components/MarkdownRenderer'
import CopyButton from '../components/CopyButton'
import DocumentSelector from '../components/DocumentSelector'
import { APP_NAME, APP_VERSION } from '../version'
import {
  fetchKnowledgeBases,
  fetchStatus,
  fetchConversations,
  fetchConversationMessages,
  streamChat,
  type KnowledgeBase,
  type StatusInfo,
  type ChatReference,
  type StreamHandle,
  type Conversation,
  type Message as ApiMessage,
} from '../api'

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  references?: ChatReference[]
  status?: 'thinking' | 'streaming' | 'done' | 'error'
  error?: string
}

function nowTime(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function formatRefs(refs: ChatReference[]): string {
  const names = refs
    .map((r) => r.doc_name || r.document_name || r.source)
    .filter((n): n is string => typeof n === 'string' && n.length > 0)
  if (!names.length) return ''
  const unique = Array.from(new Set(names))
  return unique.map((n) => `《${n}》`).join('、')
}

let idCounter = 0
const nextId = () => `m${Date.now()}_${idCounter++}`

export default function Chat() {
  const [kbs, setKbs] = useState<KnowledgeBase[]>([])
  const [kbLoading, setKbLoading] = useState(true)
  const [selectedKb, setSelectedKb] = useState<KnowledgeBase | null>(null)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  // V1.2.5：已选规范（文档）ID，用于限定检索范围；空 = 搜整个知识库
  const [selectedDocIds, setSelectedDocIds] = useState<number[]>([])

  const [status, setStatus] = useState<StatusInfo | null>(null)

  const [conversations, setConversations] = useState<Conversation[]>([])
  const [currentConvId, setCurrentConvId] = useState<string | null>(null)
  const [convLoading, setConvLoading] = useState(false)

  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)

  const [liked, setLiked] = useState<Record<string, 'up' | 'down' | undefined>>({})

  const messagesRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const streamRef = useRef<StreamHandle | null>(null)
  const conversationIdRef = useRef<string | undefined>(undefined)
  const dropdownRef = useRef<HTMLDivElement>(null)

  /* ----- load knowledge bases + status on mount ----- */
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
      .catch((err: unknown) => {
        if (!cancelled) console.error(err)
      })
      .finally(() => {
        if (!cancelled) setKbLoading(false)
      })
    fetchStatus()
      .then((res) => !cancelled && setStatus(res))
      .catch((err: unknown) => !cancelled && console.error(err))
    return () => { cancelled = true }
  }, [])

  /* ----- load conversations when KB changes ----- */
  const loadConversations = useCallback(async (kbId: string | null) => {
    setConvLoading(true)
    try {
      const res = await fetchConversations(kbId)
      setConversations(res.items || [])
    } catch (err) {
      console.error(err)
      setConversations([])
    } finally {
      setConvLoading(false)
    }
  }, [])

  useEffect(() => {
    // V1.1.1：kb 为空表示不选知识库，加载无知识库会话
    loadConversations(selectedKb?.id ?? null)
  }, [selectedKb, loadConversations])

  /* ----- close dropdown on outside click ----- */
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

  /* ----- auto-scroll to bottom on new content ----- */
  const scrollToBottom = useCallback(() => {
    const el = messagesRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [])

  useEffect(() => { scrollToBottom() }, [messages, scrollToBottom])

  /* ----- auto-grow textarea ----- */
  useEffect(() => {
    const ta = textareaRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, 140)}px`
  }, [input])

  /* ----- load conversation messages ----- */
  const loadConversationMessages = useCallback(async (convId: string) => {
    try {
      const res = await fetchConversationMessages(convId)
      const loaded: ChatMessage[] = (res.items || []).map((m: ApiMessage) => ({
        id: `c${m.id}`,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        references: m.references || undefined,
        status: 'done',
      }))
      setMessages(loaded)
      setCurrentConvId(convId)
      conversationIdRef.current = convId
    } catch (err) {
      console.error(err)
    }
  }, [])

  const selectKb = (kb: KnowledgeBase | null) => {
    setSelectedKb(kb)
    setDropdownOpen(false)
    setSelectedDocIds([]) // V1.2.5：切换知识库时清空已选规范
    if (streamRef.current) {
      streamRef.current.abort()
      streamRef.current = null
    }
    setMessages([])
    setStreaming(false)
    setCurrentConvId(null)
    conversationIdRef.current = undefined
  }

  const startNewConversation = () => {
    setMessages([])
    setCurrentConvId(null)
    conversationIdRef.current = undefined
    if (streamRef.current) {
      streamRef.current.abort()
      streamRef.current = null
    }
    setStreaming(false)
  }

  const statusText = (() => {
    if (!status) return '正在获取状态...'
    if (status.llm_configured && status.active_model) return `已连接 · ${status.active_model} 模型`
    if (status.llm_configured) return '已连接 · 模型已就绪'
    return '未配置模型'
  })()

  const updateMessage = (id: string, patch: Partial<ChatMessage>) => {
    setMessages((prev) => prev.map((m) => (m.id === id ? { ...m, ...patch } : m)))
  }

  const refreshConversations = useCallback(async () => {
    try {
      const res = await fetchConversations(selectedKb?.id ?? null)
      setConversations(res.items || [])
    } catch (err) {
      console.error(err)
    }
  }, [selectedKb])

  /* ----- 发送消息：先插入用户消息与 AI 占位，再发起 SSE 流式请求 ----- */
  const sendMessage = useCallback(
    (text: string) => {
      const trimmed = text.trim()
      if (!trimmed || streaming) return

      const userMsg: ChatMessage = {
        id: nextId(),
        role: 'user',
        content: trimmed,
        status: 'done',
      }
      const aiMsg: ChatMessage = {
        id: nextId(),
        role: 'assistant',
        content: '',
        status: 'thinking',
      }
      setMessages((prev) => [...prev, userMsg, aiMsg])
      setInput('')
      setStreaming(true)

      const aiId = aiMsg.id
      let firstToken = true

      streamRef.current = streamChat({
        kb_id: selectedKb?.id ?? null,
        message: trimmed,
        conversation_id: conversationIdRef.current,
        doc_ids: selectedDocIds.length ? selectedDocIds : null,
        onToken: (content) => {
          if (firstToken) {
            firstToken = false
            updateMessage(aiId, { content, status: 'streaming' })
          } else {
            setMessages((prev) =>
              prev.map((m) =>
                m.id === aiId ? { ...m, content: m.content + content } : m,
              ),
            )
          }
        },
        onReferences: (refs) => {
          updateMessage(aiId, { references: refs })
        },
        onStart: (cid) => {
          conversationIdRef.current = cid
          setCurrentConvId(cid)
          refreshConversations()
        },
        onDone: () => {
          setMessages((prev) =>
            prev.map((m) => (m.id === aiId ? { ...m, status: 'done' } : m)),
          )
          setStreaming(false)
          streamRef.current = null
          refreshConversations()
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
    },
    [selectedKb, streaming, refreshConversations],
  )

  const handleSend = () => { sendMessage(input) }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleRegenerate = (aiMsg: ChatMessage) => {
    if (streaming) return
    const idx = messages.findIndex((m) => m.id === aiMsg.id)
    if (idx <= 0) return
    const userMsg = messages[idx - 1]
    if (!userMsg || userMsg.role !== 'user') return
    setMessages((prev) => prev.filter((m) => m.id !== aiMsg.id))
    sendMessage(userMsg.content)
  }

  const toggleVote = (id: string, vote: 'up' | 'down') => {
    setLiked((prev) => ({ ...prev, [id]: prev[id] === vote ? undefined : vote }))
  }

  const canSend = !!input.trim() && !streaming

  /* ----- helpers for formatting conversation timestamps ----- */
  const formatConvTime = (iso?: string): string => {
    if (!iso) return ''
    const d = new Date(iso)
    const pad = (n: number) => String(n).padStart(2, '0')
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  }

  const truncateTitle = (title: string, max = 20): string => {
    if (!title) return '新对话'
    return title.length > max ? title.slice(0, max) + '...' : title
  }

  return (
    <>
      <Header activeNav="chat" />

      <main className="chat-main">
        {/* ===== 左侧对话历史侧边栏 ===== */}
        <aside className="chat-sidebar">
          <div className="sidebar-header">
            <button className="new-conv-btn" onClick={startNewConversation}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
              <span>新对话</span>
            </button>
          </div>
          <div className="sidebar-list">
            {!selectedKb && (
              <div className="sidebar-empty">请先选择知识库</div>
            )}
            {selectedKb && convLoading && (
              <div className="sidebar-empty">加载中...</div>
            )}
            {selectedKb && !convLoading && conversations.length === 0 && (
              <div className="sidebar-empty">暂无对话记录</div>
            )}
            {selectedKb && !convLoading && conversations.map((conv) => (
              <button
                key={conv.id}
                className={`conv-item${currentConvId === conv.id ? ' is-active' : ''}`}
                onClick={() => loadConversationMessages(conv.id)}
                type="button"
              >
                <div className="conv-item-title">{truncateTitle(conv.title)}</div>
                <div className="conv-item-time">{formatConvTime(conv.updated_at)}</div>
              </button>
            ))}
          </div>
        </aside>

        {/* ===== 右侧对话区域 ===== */}
        <div className="chat-content">
          {/* KB selector top bar */}
          <div className="chat-topbar">
            <div className="topbar-left">
            <div className="kb-selector" ref={dropdownRef}>
              <button
                className={`kb-trigger${dropdownOpen ? ' open' : ''}`}
                type="button"
                aria-haspopup="listbox"
                aria-expanded={dropdownOpen}
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
                      <span className="kb-option-desc">直接与大模型对话，不使用知识库检索</span>
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
            {/* V1.2.5：规范（文档）选择 */}
            <DocumentSelector
              kbId={selectedKb ? Number(selectedKb.id) : null}
              value={selectedDocIds}
              onChange={setSelectedDocIds}
            />
            </div>
            <div className="topbar-status">
              <span className="status-dot" />
              <span className="status-text">{statusText}</span>
            </div>
          </div>

          {/* Scrollable messages */}
          <div className="chat-messages" ref={messagesRef}>
            <div className="chat-column">
              {messages.length === 0 && (
                <div className="welcome-bubble">
                  <div className="welcome-avatar">
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M12 8V4H8" />
                      <rect width="16" height="12" x="4" y="8" rx="2" />
                      <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                    </svg>
                  </div>
                  <div className="welcome-text">
                    {selectedKb
                      ? `你好！我是${APP_NAME} ${APP_VERSION}，已选择知识库「${selectedKb.name}」，请开始提问。`
                      : kbs.length > 0
                        ? '你好！当前未选择知识库，将直接回答；也可从上方选择知识库进行规范问答。'
                        : '你好！当前暂无知识库，可直接提问，或前往后台创建知识库后再进行规范问答。'}
                  </div>
                </div>
              )}

              {messages.map((msg) =>
                msg.role === 'user' ? (
                  <div className="message-row user" key={msg.id}>
                    <div className="message-content">
                      <div className="bubble user-bubble">
                        <p>{msg.content}</p>
                      </div>
                      <div className="message-meta user-meta">
                        <span className="message-time">{nowTime()}</span>
                        <CopyButton text={msg.content} />
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
                        <path d="M12 8V4H8" />
                        <rect width="16" height="12" x="4" y="8" rx="2" />
                        <path d="M2 14h2M20 14h2M15 13v2M9 13v2" />
                      </svg>
                    </div>
                    <div className="message-content">
                      <div className="bubble ai-bubble">
                        {msg.status === 'thinking' ? (
                          <div className="ai-answer">
                            <span className="typing-dots"><span /><span /><span /></span>
                            <span style={{ fontSize: '13px', color: 'var(--qa-muted-foreground)' }}>正在思考...</span>
                          </div>
                        ) : (
                          <div className="ai-answer">
                            {msg.content ? (
                              // 回答以 Markdown 渲染：支持 LLM 内嵌图片，以及后端追加的「### 相关图片」小节
                              <MarkdownRenderer markdown={msg.content} />
                            ) : (
                              <span style={{ color: 'var(--qa-muted-foreground)' }}>（无内容）</span>
                            )}
                            {msg.references && msg.references.length > 0 && (
                              <div className="answer-citation">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                  <path d="M2 3h6a4 4 0 0 1 4 4v14a3 3 0 0 0-3-3H2z" />
                                  <path d="M22 3h-6a4 4 0 0 0-4 4v14a3 3 0 0 1 3-3h7z" />
                                </svg>
                                <span>参考自 {formatRefs(msg.references)}</span>
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="message-meta">
                        <span className="message-time">{nowTime()}</span>
                        {msg.status !== 'thinking' && (
                          <div className="message-actions">
                            <CopyButton text={msg.content} />
                            <button className="action-btn" type="button" aria-label="重新生成" onClick={() => handleRegenerate(msg)} disabled={streaming}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
                                <path d="M21 3v5h-5" />
                                <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
                                <path d="M8 16H3v5" />
                              </svg>
                              <span>重新生成</span>
                            </button>
                            <button className={`action-btn icon-only${liked[msg.id] === 'up' ? ' is-active' : ''}`} type="button" aria-label="点赞" onClick={() => toggleVote(msg.id, 'up')}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M7 10v12" />
                                <path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z" />
                              </svg>
                            </button>
                            <button className={`action-btn icon-only${liked[msg.id] === 'down' ? ' is-active' : ''}`} type="button" aria-label="点踩" onClick={() => toggleVote(msg.id, 'down')}>
                              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                                <path d="M17 14V2" />
                                <path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z" />
                              </svg>
                            </button>
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                ),
              )}
            </div>
          </div>
        </div>
      </main>

      {/* Input bar */}
      <div className="chat-input-bar">
        <div className="input-column">
          <div className="input-wrapper">
            <button className="attach-btn" type="button" aria-label="添加附件" onClick={() => { /* attachments out of scope */ }}>
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="m21.44 11.05-9.19 9.19a6 6 0 0 1-8.49-8.49l8.57-8.57A4 4 0 1 1 17.93 8.8l-8.57 8.57a2 2 0 0 1-2.83-2.83l8.49-8.48" />
              </svg>
            </button>
            <textarea
              className="chat-textarea"
              placeholder="请输入您的问题，Shift+Enter换行..."
              rows={1}
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
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
          <div className="input-hint">Enter发送，Shift+Enter换行</div>
        </div>
      </div>
    </>
  )
}
