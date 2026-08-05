import { useCallback, useEffect, useState } from 'react'
import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  type LlmConfigItem,
  type LlmConfigListResponse,
  type TestConnectionResponse,
} from '../api'

interface FormState {
  id?: string
  name: string
  provider: string
  api_url: string
  api_key: string
  model_name: string
  temperature: number
  max_tokens: number
  context_window: number
  timeout: number
  is_active: boolean
}

const PROVIDERS: { value: string; label: string }[] = [
  { value: 'openai', label: 'OpenAI' },
  { value: 'azure', label: 'Azure OpenAI' },
  { value: 'anthropic', label: 'Anthropic' },
  { value: 'wenxin', label: '百度文心' },
  { value: 'qwen', label: '阿里通义' },
  { value: 'zhipu', label: '智谱AI' },
  { value: 'custom', label: '自定义' },
]

const providerLabel = (value: string): string =>
  PROVIDERS.find((p) => p.value === value)?.label || value || '-'

const EMPTY_FORM: FormState = {
  name: '',
  provider: 'openai',
  api_url: '',
  api_key: '',
  model_name: '',
  temperature: 0.7,
  max_tokens: 2048,
  context_window: 64000,
  timeout: 30,
  is_active: false,
}

function formatTime(t?: string): string {
  if (!t) return '-'
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return t
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

export default function LlmConfig() {
  const [list, setList] = useState<LlmConfigItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [form, setForm] = useState<FormState>(EMPTY_FORM)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [apiKeyTouched, setApiKeyTouched] = useState(false)
  const [showKey, setShowKey] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)

  const loadList = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await apiGet<LlmConfigListResponse>('/api/admin/llm-configs')
      setList(res.items || [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadList()
  }, [loadList])

  const setField = <K extends keyof FormState>(key: K, value: FormState[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }))
  }

  const resetForm = () => {
    setForm(EMPTY_FORM)
    setEditingId(null)
    setApiKeyTouched(false)
    setShowKey(false)
  }

  const handleEdit = (item: LlmConfigItem) => {
    setEditingId(item.id)
    setForm({
      id: item.id,
      name: item.name || '',
      provider: item.provider || 'openai',
      api_url: item.api_url || '',
      api_key: '',
      model_name: item.model_name || '',
      temperature: typeof item.temperature === 'number' ? item.temperature : 0.7,
      max_tokens: typeof item.max_tokens === 'number' ? item.max_tokens : 2048,
      context_window: typeof item.context_window === 'number' ? item.context_window : 64000,
      timeout: typeof item.timeout === 'number' ? item.timeout : 30,
      is_active: !!item.is_active,
    })
    setApiKeyTouched(false)
    setShowKey(false)
    window.scrollTo({ top: 0, behavior: 'smooth' })
  }

  const handleDelete = async (item: LlmConfigItem) => {
    if (!window.confirm(`确认删除配置「${item.name}」吗？此操作不可恢复。`)) return
    try {
      await apiDelete(`/api/admin/llm-configs/${item.id}`)
      await loadList()
      if (editingId === item.id) resetForm()
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  const handleTest = async () => {
    if (!form.provider || !form.api_url || !form.model_name) {
      alert('请先填写模型提供商、API地址和模型名称')
      return
    }
    if (!form.api_key) {
      alert('请先填写API密钥')
      return
    }
    setTesting(true)
    try {
      const res = await apiPost<TestConnectionResponse>(
        '/api/admin/llm-configs/test',
        {
          provider: form.provider,
          api_url: form.api_url,
          api_key: form.api_key,
          model_name: form.model_name,
        },
      )
      alert(res.success ? `连接成功：${res.message || 'OK'}` : `连接失败：${res.message || '未知错误'}`)
    } catch (err) {
      alert(`连接失败：${err instanceof Error ? err.message : '网络错误'}`)
    } finally {
      setTesting(false)
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name || !form.provider || !form.api_url || !form.model_name) {
      alert('请完整填写必填项')
      return
    }
    // api_key required on create; optional on update (only send if user typed)
    if (!editingId && !form.api_key) {
      alert('请填写API密钥')
      return
    }

    setSaving(true)
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        provider: form.provider,
        api_url: form.api_url,
        model_name: form.model_name,
        temperature: form.temperature,
        max_tokens: Number(form.max_tokens),
        context_window: Number(form.context_window),
        timeout: Number(form.timeout),
        is_active: form.is_active,
      }
      if (!editingId) {
        payload.api_key = form.api_key
        await apiPost('/api/admin/llm-configs', payload)
      } else {
        // only send api_key if the user typed a new one
        if (apiKeyTouched && form.api_key) payload.api_key = form.api_key
        await apiPut(`/api/admin/llm-configs/${editingId}`, payload)
      }
      resetForm()
      await loadList()
      alert(editingId ? '配置已更新' : '配置已保存')
    } catch (err) {
      alert(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleCancel = () => {
    resetForm()
  }

  const activeCount = list.filter((c) => c.is_active).length

  return (
    <main className="app-content">
      <div className="page-head">
        <h1 className="page-title">大语言模型API配置</h1>
        <p className="page-subtitle">
          管理系统对接的大语言模型服务参数{activeCount === 0 && list.length > 0
            ? '（当前无启用配置，前台对话将不可用）'
            : ''}
        </p>
      </div>

      {error && <div className="error-banner">{error}</div>}

      {/* Config form card */}
      <section className="card card-padded" aria-labelledby="form-card-title">
        <h2 className="card-title" id="form-card-title">
          {editingId ? '编辑模型配置' : '模型配置'}
        </h2>
        <form className="form-grid" autoComplete="off" onSubmit={handleSubmit}>
          <div className="form-field">
            <label className="form-label" htmlFor="cfg-name">
              配置名称<span className="req-mark" aria-hidden="true">*</span>
            </label>
            <input
              className="form-control"
              id="cfg-name"
              type="text"
              value={form.name}
              required
              onChange={(e) => setField('name', e.target.value)}
              placeholder="如 OpenAI-GPT4"
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="cfg-provider">
              模型提供商<span className="req-mark" aria-hidden="true">*</span>
            </label>
            <select
              className="form-control"
              id="cfg-provider"
              required
              value={form.provider}
              onChange={(e) => setField('provider', e.target.value)}
            >
              {PROVIDERS.map((p) => (
                <option key={p.value} value={p.value}>
                  {p.label}
                </option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="cfg-url">
              API地址<span className="req-mark" aria-hidden="true">*</span>
            </label>
            <input
              className="form-control"
              id="cfg-url"
              type="text"
              value={form.api_url}
              required
              onChange={(e) => setField('api_url', e.target.value)}
              placeholder="https://api.openai.com/v1"
            />
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="cfg-model">
              模型名称<span className="req-mark" aria-hidden="true">*</span>
            </label>
            <input
              className="form-control"
              id="cfg-model"
              type="text"
              value={form.model_name}
              required
              onChange={(e) => setField('model_name', e.target.value)}
              placeholder="如 gpt-4"
            />
          </div>

          <div className="form-field span-2">
            <label className="form-label" htmlFor="cfg-key">
              API密钥
              <span className="req-mark" aria-hidden="true">*</span>
              {editingId && (
                <span style={{ fontSize: '12px', color: 'var(--qa-muted-foreground)', marginLeft: 4 }}>
                  （留空保持不变）
                </span>
              )}
            </label>
            <div className="input-group">
              <input
                className="form-control"
                id="cfg-key"
                type={showKey ? 'text' : 'password'}
                value={form.api_key}
                onChange={(e) => {
                  setField('api_key', e.target.value)
                  setApiKeyTouched(true)
                }}
                placeholder={editingId ? '保持不变' : '请输入API密钥'}
              />
              <button
                className="input-toggle-btn"
                type="button"
                aria-label="显示/隐藏 API 密钥"
                aria-pressed={showKey}
                onClick={() => setShowKey((v) => !v)}
              >
                {showKey ? (
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M9.88 9.88a3 3 0 1 0 4.24 4.24" />
                    <path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68" />
                    <path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61" />
                    <line x1="2" y1="2" x2="22" y2="22" />
                  </svg>
                ) : (
                  <svg
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7z" />
                    <circle cx="12" cy="12" r="3" />
                  </svg>
                )}
              </button>
            </div>
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="cfg-temp">
              温度参数
              <span className="range-value">{form.temperature.toFixed(1)}</span>
            </label>
            <div className="range-row">
              <input
                className="form-range"
                id="cfg-temp"
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={form.temperature}
                onChange={(e) => setField('temperature', parseFloat(e.target.value))}
              />
              <div className="range-meta">
                <span>0 · 精确</span>
                <span>2 · 发散</span>
              </div>
            </div>
          </div>
          <div className="form-field">
            <label className="form-label" htmlFor="cfg-tokens">
              最大令牌数
            </label>
            <input
              className="form-control"
              id="cfg-tokens"
              type="number"
              min="1"
              value={form.max_tokens}
              onChange={(e) => setField('max_tokens', parseInt(e.target.value || '0', 10))}
            />
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="cfg-ctx">
              上下文窗口（tokens）
            </label>
            <input
              className="form-control"
              id="cfg-ctx"
              type="number"
              min="1000"
              step="1000"
              value={form.context_window}
              onChange={(e) => setField('context_window', parseInt(e.target.value || '0', 10))}
            />
            <span style={{ fontSize: '12px', color: 'var(--qa-muted-foreground)' }}>
              对话历史注入的裁剪上限，按模型实际窗口填写（如 DeepSeek 64000）
            </span>
          </div>

          <div className="form-field">
            <label className="form-label" htmlFor="cfg-timeout">
              请求超时时间
            </label>
            <div className="input-group">
              <input
                className="form-control"
                id="cfg-timeout"
                type="number"
                min="1"
                value={form.timeout}
                onChange={(e) => setField('timeout', parseInt(e.target.value || '0', 10))}
              />
              <span className="input-suffix">秒</span>
            </div>
          </div>
          <div className="form-field toggle-field">
            <span className="form-label">是否启用</span>
            <label className="toggle-switch">
              <input
                type="checkbox"
                checked={form.is_active}
                onChange={(e) => setField('is_active', e.target.checked)}
              />
              <span className="toggle-slider" aria-hidden="true" />
            </label>
          </div>

          <div className="form-actions span-2">
            <button
              className="btn btn-secondary"
              type="button"
              onClick={handleCancel}
              disabled={saving}
            >
              取消
            </button>
            <button
              className="btn btn-secondary"
              type="button"
              onClick={handleTest}
              disabled={testing}
            >
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M12 22v-5" />
                <path d="M9 7V2" />
                <path d="M15 7V2" />
                <path d="M6 7h12l-1 5a5 5 0 0 1-10 0z" />
                <path d="M9 17a3 3 0 0 0 6 0" />
              </svg>
              {testing ? '测试中...' : '测试连接'}
            </button>
            <button className="btn btn-primary" type="submit" disabled={saving}>
              <svg
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
                <polyline points="17 21 17 13 7 13 7 21" />
                <polyline points="7 3 7 8 15 8" />
              </svg>
              {saving ? '保存中...' : '保存配置'}
            </button>
          </div>
        </form>
      </section>

      {/* Config list card */}
      <section className="card card-padded" aria-labelledby="list-card-title">
        <h2 className="card-title" id="list-card-title">
          配置列表
        </h2>
        <div className="table-wrap">
          <table className="config-table">
            <thead>
              <tr>
                <th>配置名称</th>
                <th>模型提供商</th>
                <th>模型名称</th>
                <th>状态</th>
                <th>更新时间</th>
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
              {!loading && list.length === 0 && (
                <tr>
                  <td colSpan={6} className="empty-state">
                    暂无配置，请在上方新建
                  </td>
                </tr>
              )}
              {!loading &&
                list.map((item) => (
                  <tr key={item.id}>
                    <td className="col-name">{item.name}</td>
                    <td>{providerLabel(item.provider)}</td>
                    <td className="col-mono">{item.model_name}</td>
                    <td>
                      <span className={`badge ${item.is_active ? 'badge-success' : 'badge-muted'}`}>
                        {item.is_active ? '已启用' : '未启用'}
                      </span>
                    </td>
                    <td className="col-time">{formatTime(item.updated_at)}</td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="row-action"
                          type="button"
                          aria-label="编辑"
                          onClick={() => handleEdit(item)}
                        >
                          <svg
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <path d="M12 20h9" />
                            <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4z" />
                          </svg>
                          编辑
                        </button>
                        <button
                          className="row-action danger"
                          type="button"
                          aria-label="删除"
                          onClick={() => handleDelete(item)}
                        >
                          <svg
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          >
                            <polyline points="3 6 5 6 21 6" />
                            <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                            <path d="M10 11v6M14 11v6" />
                            <path d="M9 6V4a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
                          </svg>
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <div className="table-footer">
          <span>共 {list.length} 条配置</span>
          <span>{activeCount} 个已启用</span>
        </div>
      </section>
    </main>
  )
}
