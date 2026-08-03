import { Link } from 'react-router-dom'
import { APP_VERSION } from '../version'

interface SidebarProps {
  active: 'llm' | 'kb'
}

export default function Sidebar({ active }: SidebarProps) {
  return (
    <aside className="app-sidebar">
      <nav className="sidebar-nav">
        <Link
          to="/admin/llm"
          className={`sidebar-item${active === 'llm' ? ' is-active' : ''}`}
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M12 2a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3v1a3 3 0 0 0-3 3v3a3 3 0 0 0 3 3h1a3 3 0 0 0 3 3h4a3 3 0 0 0 3-3h1a3 3 0 0 0 3-3v-3a3 3 0 0 0-3-3V9a3 3 0 0 0-3-3V5a3 3 0 0 0-3-3z" />
          </svg>
          <span>LLM配置</span>
        </Link>
        <Link
          to="/admin/kb"
          className={`sidebar-item${active === 'kb' ? ' is-active' : ''}`}
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
          <span>知识库管理</span>
        </Link>
      </nav>
      <div className="sidebar-footer">
        <div className="sidebar-version">{APP_VERSION}</div>
      </div>
    </aside>
  )
}
