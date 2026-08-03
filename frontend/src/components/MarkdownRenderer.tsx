import ReactMarkdown, { Components } from 'react-markdown'
import type { ComponentProps } from 'react'
import remarkGfm from 'remark-gfm'

interface MarkdownRendererProps {
  markdown: string
  remarkPlugins?: ComponentProps<typeof ReactMarkdown>['remarkPlugins']
  rehypePlugins?: ComponentProps<typeof ReactMarkdown>['rehypePlugins']
}

// 共享的 Markdown 渲染样式：统一「对话回答」与「知识库预览」的排版（图片/表格/代码块）
const sharedComponents: Components = {
  img: ({ src, alt }) => (
    <img
      src={src as string}
      alt={alt || ''}
      loading="lazy"
      style={{ maxWidth: '100%', borderRadius: 4, margin: '8px 0', display: 'block' }}
    />
  ),
  table: ({ children }) => (
    <table style={{ borderCollapse: 'collapse', width: '100%', margin: '8px 0', border: '1px solid #ddd' }}>{children}</table>
  ),
  th: ({ children }) => (
    <th style={{ border: '1px solid #ddd', padding: '6px 10px', background: '#f5f5f5', textAlign: 'left' }}>{children}</th>
  ),
  td: ({ children }) => (
    <td style={{ border: '1px solid #ddd', padding: '6px 10px' }}>{children}</td>
  ),
  code: ({ className, children, ...props }) => (
    <code className={className} {...props}>{children}</code>
  ),
  pre: ({ children }) => (
    <pre style={{ background: '#f6f8fa', padding: '12px', borderRadius: 6, overflow: 'auto' }}>{children}</pre>
  ),
}

export default function MarkdownRenderer({
  markdown,
  remarkPlugins = [remarkGfm],
  rehypePlugins,
}: MarkdownRendererProps) {
  return (
    <ReactMarkdown
      remarkPlugins={remarkPlugins}
      rehypePlugins={rehypePlugins}
      components={sharedComponents}
    >
      {markdown}
    </ReactMarkdown>
  )
}
