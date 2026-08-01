// Typed API client + SSE chat streaming helper.

/* ---------------------------------- Types ---------------------------------- */

export interface KnowledgeBase {
  id: string
  name: string
  description?: string
  doc_count?: number
  created_at?: string
  updated_at?: string
}

export interface KbListResponse {
  items: KnowledgeBase[]
  total?: number
}

export interface StatusInfo {
  llm_configured: boolean
  active_model?: string
  mineru_available?: boolean
}

export interface LlmConfigItem {
  id: string
  name: string
  provider: string
  api_url: string
  api_key_masked?: string
  model_name: string
  temperature?: number
  max_tokens?: number
  timeout?: number
  is_active?: boolean
  created_at?: string
  updated_at?: string
}

export interface LlmConfigListResponse {
  items: LlmConfigItem[]
}

export interface LlmConfigInput {
  name: string
  provider: string
  api_url: string
  api_key?: string
  model_name: string
  temperature?: number
  max_tokens?: number
  timeout?: number
  is_active?: boolean
}

export interface TestConnectionInput {
  provider: string
  api_url: string
  api_key?: string
  model_name: string
}

export interface TestConnectionResponse {
  success: boolean
  message: string
}

export interface DocumentItem {
  id: string
  kb_id: string
  file_name: string
  file_type?: string
  file_size?: number
  content_text?: string | null
  parse_status?: string
  parsed_content?: string | null
  created_at?: string
  updated_at?: string
}

export interface DocumentListResponse {
  items: DocumentItem[]
}

export interface ParsedPageImage {
  id: string
  src: string
}

export interface ParsedPage {
  page_num: number
  text: string
  tables: string[][][]
  images: ParsedPageImage[]
}

export interface ParsedContent {
  pages: ParsedPage[]
  total_pages: number
}

export interface ParseResult {
  success: boolean
  parse_status: string
  total_pages: number
  total_images: number
  total_tables: number
}

export async function parseDocument(docId: string): Promise<ParseResult> {
  return apiPost<ParseResult>(`/api/admin/documents/${docId}/parse`, {})
}

export interface ChatReference {
  doc_name?: string
  document_name?: string
  source?: string
  [k: string]: unknown
}

/* ----------------------------- Core request helper ------------------------- */

async function parseBody(res: Response): Promise<unknown> {
  const text = await res.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

async function extractError(res: Response): Promise<string> {
  const data = await parseBody(res)
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    if (typeof obj.detail === 'string') return obj.detail
    if (typeof obj.message === 'string') return obj.message
    if (Array.isArray(obj.detail) && obj.detail.length) {
      const first = obj.detail[0] as Record<string, unknown>
      if (first && typeof first.msg === 'string') return first.msg
    }
  }
  if (typeof data === 'string' && data) return data
  return `请求失败 (${res.status})`
}

async function request(
  path: string,
  options: RequestInit = {},
): Promise<unknown> {
  let res: Response
  try {
    res = await fetch(path, options)
  } catch (err) {
    throw new Error(err instanceof Error ? err.message || '网络错误' : '网络错误')
  }
  if (!res.ok) {
    throw new Error(await extractError(res))
  }
  return parseBody(res)
}

function jsonOptions(body: unknown, method: string): RequestInit {
  return {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

/* ------------------------------- HTTP helpers ------------------------------ */

export async function apiGet<T>(path: string): Promise<T> {
  return (await request(path, { method: 'GET' })) as T
}

export async function apiPost<T>(path: string, body: unknown): Promise<T> {
  return (await request(path, jsonOptions(body, 'POST'))) as T
}

export async function apiPut<T>(path: string, body: unknown): Promise<T> {
  return (await request(path, jsonOptions(body, 'PUT'))) as T
}

export async function apiDelete<T>(path: string): Promise<T> {
  return (await request(path, { method: 'DELETE' })) as T
}

/* --------------------------- Convenience endpoints ------------------------- */

export interface Conversation {
  id: string
  kb_id: string
  title: string
  created_at?: string
  updated_at?: string
}

export interface ConversationListResponse {
  items: Conversation[]
}

export interface Message {
  id: string
  role: string
  content: string
  references?: ChatReference[] | null
  created_at?: string
}

export interface MessageListResponse {
  items: Message[]
}

export const fetchConversations = (kbId: string): Promise<ConversationListResponse> =>
  apiGet<ConversationListResponse>(`/api/conversations?kb_id=${kbId}`)

export const fetchConversationMessages = (convId: string): Promise<MessageListResponse> =>
  apiGet<MessageListResponse>(`/api/conversations/${convId}/messages`)

export const fetchKnowledgeBases = (): Promise<KbListResponse> =>
  apiGet<KbListResponse>('/api/knowledge-bases')

export const fetchStatus = (): Promise<StatusInfo> =>
  apiGet<StatusInfo>('/api/status')

/* ------------------------------- SSE streaming ----------------------------- */

export interface StreamChatParams {
  kb_id: string
  message: string
  conversation_id?: string
  onToken: (content: string) => void
  onReferences?: (refs: ChatReference[]) => void
  onDone?: () => void
  onError?: (err: string) => void
  onStart?: (conversationId: string) => void
}

export interface StreamHandle {
  abort: () => void
}

export function streamChat(params: StreamChatParams): StreamHandle {
  const controller = new AbortController()
  const body = {
    kb_id: params.kb_id,
    message: params.message,
    conversation_id: params.conversation_id,
  }

  const dispatch = (evt: Record<string, unknown>) => {
    const type = evt.type as string
    switch (type) {
      case 'start': {
        const cid = evt.conversation_id
        if (cid !== undefined && cid !== null && cid !== '') {
          params.onStart?.(String(cid))
        }
        break
      }
      case 'references': {
        const refs = (evt.references ?? evt.data) as ChatReference[] | undefined
        params.onReferences?.(Array.isArray(refs) ? refs : [])
        break
      }
      case 'token': {
        const content = evt.content
        if (typeof content === 'string') params.onToken(content)
        break
      }
      case 'delta': {
        // tolerate alternative field name
        const content = evt.content ?? evt.delta
        if (typeof content === 'string') params.onToken(content)
        break
      }
      case 'done': {
        params.onDone?.()
        break
      }
      case 'error': {
        const msg = evt.message ?? evt.error
        params.onError?.(typeof msg === 'string' ? msg : '生成失败')
        break
      }
      default:
        break
    }
  }

  const processBuffer = (buffer: string): string => {
    let remaining = buffer
    let idx: number
    while ((idx = remaining.indexOf('\n\n')) >= 0) {
      const block = remaining.slice(0, idx)
      remaining = remaining.slice(idx + 2)
      const lines = block.split('\n')
      for (const line of lines) {
        const trimmed = line.trimStart()
        if (!trimmed.startsWith('data:')) continue
        const dataStr = trimmed.slice(5).trim()
        if (!dataStr || dataStr === '[DONE]') continue
        try {
          dispatch(JSON.parse(dataStr) as Record<string, unknown>)
        } catch {
          /* ignore malformed JSON line */
        }
      }
    }
    return remaining
  }

  fetch('/api/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal: controller.signal,
  })
    .then(async (res) => {
      if (!res.ok) {
        params.onError?.(await extractError(res))
        return
      }
      const reader = res.body?.getReader()
      if (!reader) {
        params.onError?.('无法读取响应流')
        return
      }
      const decoder = new TextDecoder('utf-8')
      let buffer = ''
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        buffer = processBuffer(buffer)
      }
      // flush any trailing data
      buffer += decoder.decode()
      if (buffer.trim()) processBuffer(buffer)
      // ensure done fires if the stream ended without an explicit done event
    })
    .catch((err: unknown) => {
      if (err instanceof DOMException && err.name === 'AbortError') return
      if (err instanceof Error && err.name === 'AbortError') return
      const message =
        err instanceof Error ? err.message || '网络错误' : '网络错误'
      params.onError?.(message)
    })

  return {
    abort: () => controller.abort(),
  }
}
