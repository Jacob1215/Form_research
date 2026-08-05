import { useState } from 'react'

/**
 * 复制文本到剪贴板。成功返回 true。
 * - 优先使用 navigator.clipboard（HTTPS / localhost 安全上下文）。
 * - 降级为临时 textarea + document.execCommand('copy')，
 *   覆盖局域网明文 http（navigator.clipboard 不存在或权限被拒）的场景。
 */
export async function copyText(text: string): Promise<boolean> {
  const value = text ?? ''
  if (!value) return false

  if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
    try {
      await navigator.clipboard.writeText(value)
      return true
    } catch {
      // 权限被拒等情况，继续走降级路径
    }
  }

  try {
    const ta = document.createElement('textarea')
    ta.value = value
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.style.left = '-9999px'
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, ta.value.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

interface CopyButtonProps {
  /** 要复制的内容 */
  text: string
  /** 按钮文字，默认「复制」 */
  label?: string
  /** 额外类名，默认复用 .action-btn 样式 */
  className?: string
}

/**
 * 带「已复制」反馈的复制按钮。
 * 成功后图标切换为勾选、文字变为「已复制」，约 1.5s 后恢复。
 */
export default function CopyButton({ text, label = '复制', className = 'action-btn' }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const handleClick = async () => {
    const ok = await copyText(text)
    if (ok) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <button
      type="button"
      className={`${className}${copied ? ' is-copied' : ''}`}
      aria-label="复制"
      onClick={handleClick}
    >
      {copied ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M20 6 9 17l-5-5" />
        </svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
          <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
        </svg>
      )}
      <span>{copied ? '已复制' : label}</span>
    </button>
  )
}
