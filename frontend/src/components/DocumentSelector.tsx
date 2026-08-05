import { useEffect, useRef, useState } from 'react'
import { listDocuments, type DocumentItem } from '../api'

interface DocumentSelectorProps {
  /** 当前选中的知识库 ID；null = 未选知识库（组件不渲染） */
  kbId: number | null
  /** 已选规范（文档）ID 列表 */
  value: number[]
  /** 选择变化回调 */
  onChange: (ids: number[]) => void
}

/**
 * V1.2.5：知识库「规范（文档）」多选器。
 *
 * 选中知识库后显示；从该知识库拉取文档列表，勾选若干本规范（chips 展示）。
 * 配合后端 doc_ids 实现「只在这些规范内检索」；不选任何规范 = 搜整个知识库。
 */
export default function DocumentSelector({ kbId, value, onChange }: DocumentSelectorProps) {
  const [docs, setDocs] = useState<DocumentItem[]>([])
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // kbId 变化时拉取该知识库下的文档列表
  useEffect(() => {
    if (kbId == null) {
      setDocs([])
      setOpen(false)
      return
    }
    let cancelled = false
    setLoading(true)
    listDocuments(String(kbId))
      .then((res) => {
        if (!cancelled) setDocs(res.items || [])
      })
      .catch(() => {
        if (!cancelled) setDocs([])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [kbId])

  // 点击组件外部时关闭下拉
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  if (kbId == null) return null

  const idSet = new Set(value)
  const toggle = (id: number) => {
    onChange(idSet.has(id) ? value.filter((v) => v !== id) : [...value, id])
  }
  const selectedDocs = docs.filter((d) => idSet.has(Number(d.id)))

  return (
    <div className="doc-selector" ref={ref}>
      <button
        type="button"
        className={`doc-trigger${open ? ' open' : ''}${value.length ? ' has-value' : ''}`}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="doc-trigger-label">
          {value.length ? `已选 ${value.length} 本规范` : '选择规范（可选）'}
        </span>
        <span className="doc-chevron">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </span>
      </button>

      {open && (
        <div className="doc-dropdown" role="listbox" aria-label="选择规范">
          <div className="doc-dropdown-header">
            {loading ? '加载文档中...' : `选择规范（共 ${docs.length} 篇）`}
          </div>
          {!loading && docs.length === 0 && (
            <div className="doc-option" style={{ cursor: 'default' }}>该知识库暂无文档</div>
          )}
          {docs.map((d) => {
            const id = Number(d.id)
            const checked = idSet.has(id)
            return (
              <div
                key={d.id}
                className={`doc-option${checked ? ' selected' : ''}`}
                role="option"
                aria-selected={checked}
                onClick={() => toggle(id)}
              >
                <input
                  type="checkbox"
                  className="doc-checkbox"
                  checked={checked}
                  onChange={() => toggle(id)}
                  onClick={(e) => e.stopPropagation()}
                />
                <span className="doc-option-name" title={d.file_name}>{d.file_name}</span>
              </div>
            )
          })}
          {selectedDocs.length > 0 && (
            <div className="doc-dropdown-footer">
              <button type="button" onClick={() => onChange([])}>清空选择</button>
            </div>
          )}
        </div>
      )}

      {selectedDocs.length > 0 && (
        <div className="doc-chips">
          {selectedDocs.map((d) => (
            <span className="doc-chip" key={d.id}>
              <span className="doc-chip-name">{d.file_name}</span>
              <button
                type="button"
                className="doc-chip-remove"
                aria-label={`移除${d.file_name}`}
                onClick={() => toggle(Number(d.id))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
