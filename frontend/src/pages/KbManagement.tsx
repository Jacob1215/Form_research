import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  apiDelete,
  apiGet,
  apiPost,
  apiPut,
  type KbListResponse,
  type KnowledgeBase,
} from '../api'

function formatDate(t?: string): string {
  if (!t) return '-'
  const d = new Date(t)
  if (Number.isNaN(d.getTime())) return t
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

interface ModalState {
  open: boolean
  editing?: KnowledgeBase
  name: string
  description: string
  submitting: boolean
}

const MODAL_CLOSED: ModalState = {
  open: false,
  name: '',
  description: '',
  submitting: false,
}

export default function KbManagement() {
  const navigate = useNavigate()
  const [list, setList] = useState<KnowledgeBase[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [searchInput, setSearchInput] = useState('')
  const [search, setSearch] = useState('')

  const [modal, setModal] = useState<ModalState>(MODAL_CLOSED)

  const loadList = useCallback(
    async (keyword: string) => {
      setLoading(true)
      setError(null)
      try {
        const query = keyword ? `?search=${encodeURIComponent(keyword)}` : ''
        const res = await apiGet<KbListResponse>(
          `/api/admin/knowledge-bases${query}`,
        )
        setList(res.items || [])
      } catch (err) {
        setError(err instanceof Error ? err.message : '加载失败')
      } finally {
        setLoading(false)
      }
    },
    [],
  )

  useEffect(() => {
    loadList('')
  }, [loadList])

  const handleSearch = () => {
    setSearch(searchInput)
    loadList(searchInput.trim())
  }

  const handleSearchKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') handleSearch()
  }

  const openCreate = () => {
    setModal({
      open: true,
      name: '',
      description: '',
      submitting: false,
    })
  }

  const openEdit = (kb: KnowledgeBase) => {
    setModal({
      open: true,
      editing: kb,
      name: kb.name,
      description: kb.description || '',
      submitting: false,
    })
  }

  const closeModal = () => {
    if (modal.submitting) return
    setModal(MODAL_CLOSED)
  }

  // Esc to close
  useEffect(() => {
    if (!modal.open) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeModal()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [modal.open, modal.submitting])

  const handleModalSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const name = modal.name.trim()
    if (!name) {
      alert('请输入知识库名称')
      return
    }
    setModal((prev) => ({ ...prev, submitting: true }))
    try {
      const body = { name, description: modal.description.trim() || undefined }
      if (modal.editing) {
        await apiPut(`/api/admin/knowledge-bases/${modal.editing.id}`, body)
      } else {
        await apiPost('/api/admin/knowledge-bases', body)
      }
      setModal(MODAL_CLOSED)
      await loadList(search.trim())
    } catch (err) {
      alert(err instanceof Error ? err.message : '保存失败')
      setModal((prev) => ({ ...prev, submitting: false }))
    }
  }

  const handleDelete = async (kb: KnowledgeBase) => {
    if (!window.confirm(`确认删除知识库「${kb.name}」吗？该操作将清理其下所有文档且不可恢复。`))
      return
    try {
      await apiDelete(`/api/admin/knowledge-bases/${kb.id}`)
      await loadList(search.trim())
    } catch (err) {
      alert(err instanceof Error ? err.message : '删除失败')
    }
  }

  const goToDocs = (kb: KnowledgeBase) => {
    navigate(`/admin/kb/${kb.id}`)
  }

  return (
    <main className="content">
      <div className="content-inner">
        <div className="page-head-row">
          <div>
            <h1 className="page-title">基准知识库管理</h1>
            <p className="page-subtitle">创建和维护工程规范文档知识库</p>
          </div>
          <button className="btn btn-primary" type="button" onClick={openCreate}>
            <svg
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <line x1="12" y1="5" x2="12" y2="19" />
              <line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            新建知识库
          </button>
        </div>

        <section className="card" aria-label="知识库列表">
          <div className="card-body">
            <div className="toolbar">
              <div className="search-box">
                <svg
                  className="search-icon"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                <input
                  className="search-input"
                  type="text"
                  placeholder="搜索知识库名称..."
                  aria-label="搜索知识库名称"
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                />
              </div>
              <button className="btn btn-secondary" type="button" onClick={handleSearch}>
                <svg
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                >
                  <circle cx="11" cy="11" r="8" />
                  <line x1="21" y1="21" x2="16.65" y2="16.65" />
                </svg>
                搜索
              </button>
            </div>

            {error && <div className="error-banner">{error}</div>}

            <div className="kb-table-wrap">
              <table className="kb-table">
                <colgroup>
                  <col style={{ width: '180px' }} />
                  <col />
                  <col style={{ width: '110px' }} />
                  <col style={{ width: '130px' }} />
                  <col style={{ width: '140px' }} />
                  <col style={{ width: '230px' }} />
                </colgroup>
                <thead>
                  <tr>
                    <th>知识库名称</th>
                    <th>描述</th>
                    <th>文档数量</th>
                    <th>创建时间</th>
                    <th>最后更新时间</th>
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
                        {search ? '未找到匹配的知识库' : '暂无知识库，点击右上角新建'}
                      </td>
                    </tr>
                  )}
                  {!loading &&
                    list.map((kb) => (
                      <tr key={kb.id}>
                        <td className="kb-name-cell">{kb.name}</td>
                        <td className="kb-desc">{kb.description || '-'}</td>
                        <td>
                          <span className="kb-count">{kb.doc_count ?? 0}</span>
                          <span className="kb-count-unit">篇</span>
                        </td>
                        <td className="kb-time">{formatDate(kb.created_at)}</td>
                        <td className="kb-time">{formatDate(kb.updated_at)}</td>
                        <td>
                          <div className="kb-actions">
                            <button
                              className="kb-action is-primary"
                              type="button"
                              onClick={() => goToDocs(kb)}
                            >
                              <svg
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth="2"
                                strokeLinecap="round"
                                strokeLinejoin="round"
                              >
                                <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                              </svg>
                              管理文档
                            </button>
                            <button
                              className="kb-action"
                              type="button"
                              aria-label="编辑"
                              onClick={() => openEdit(kb)}
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
                              className="kb-action is-danger"
                              type="button"
                              aria-label="删除"
                              onClick={() => handleDelete(kb)}
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
                                <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
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
              <span>共 {list.length} 个知识库</span>
              <span>第 1 / 1 页</span>
            </div>
          </div>
        </section>
      </div>

      {/* Create / edit KB modal */}
      {modal.open && (
        <div
          className="modal-backdrop is-open"
          onClick={(e) => {
            if (e.target === e.currentTarget) closeModal()
          }}
        >
          <div className="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title-text">
            <div className="modal-head">
              <h2 className="modal-title" id="modal-title-text">
                {modal.editing ? '编辑知识库' : '新建知识库'}
              </h2>
              <button
                className="modal-close"
                type="button"
                aria-label="关闭"
                onClick={closeModal}
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
            <form className="modal-form" autoComplete="off" onSubmit={handleModalSubmit}>
              <div className="form-field">
                <label className="form-label" htmlFor="kb-name-input">
                  知识库名称<span className="req-mark" aria-hidden="true">*</span>
                </label>
                <input
                  className="form-control"
                  id="kb-name-input"
                  type="text"
                  placeholder="请输入知识库名称"
                  required
                  autoFocus
                  value={modal.name}
                  onChange={(e) => setModal((prev) => ({ ...prev, name: e.target.value }))}
                />
              </div>
              <div className="form-field">
                <label className="form-label" htmlFor="kb-desc-input">
                  描述
                </label>
                <textarea
                  className="form-control"
                  id="kb-desc-input"
                  rows={3}
                  placeholder="请输入知识库描述（选填）"
                  value={modal.description}
                  onChange={(e) =>
                    setModal((prev) => ({ ...prev, description: e.target.value }))
                  }
                />
              </div>
              <div className="modal-actions">
                <button
                  className="btn btn-secondary"
                  type="button"
                  onClick={closeModal}
                  disabled={modal.submitting}
                >
                  取消
                </button>
                <button className="btn btn-primary" type="submit" disabled={modal.submitting}>
                  {modal.submitting ? '保存中...' : modal.editing ? '保存' : '创建'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </main>
  )
}
