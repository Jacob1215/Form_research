import { Link } from 'react-router-dom'

interface HeaderProps {
  activeNav: 'chat' | 'backend'
}

export default function Header({ activeNav }: HeaderProps) {
  return (
    <header className="app-header">
      <div className="header-inner">
        <div className="header-brand">
          <svg
            className="brand-logo"
            width="32"
            height="32"
            viewBox="0 0 32 32"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <rect width="32" height="32" rx="8" fill="var(--qa-primary)" />
            <path
              d="M10 12.5C10 10.567 11.567 9 13.5 9H18.5C20.433 9 22 10.567 22 12.5V19.5C22 21.433 20.433 23 18.5 23H13.5C11.567 23 10 21.433 10 19.5V12.5Z"
              stroke="white"
              strokeWidth="2"
            />
            <circle cx="16" cy="16" r="2.5" fill="white" />
            <path
              d="M14 16L12 14M18 16L20 14"
              stroke="white"
              strokeWidth="1.5"
              strokeLinecap="round"
            />
          </svg>
          <span className="brand-title">智能问答助手</span>
        </div>
        <nav className="header-nav">
          <Link
            to="/"
            className={`nav-link${activeNav === 'chat' ? ' is-active' : ''}`}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
            </svg>
            <span>前台对话</span>
          </Link>
          <Link
            to="/admin/llm"
            className={`nav-link${activeNav === 'backend' ? ' is-active' : ''}`}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
            </svg>
            <span>后台管理</span>
          </Link>
        </nav>
        <div className="header-right">
          <div className="user-chip">
            <div className="user-avatar">A</div>
            <span className="user-name">管理员</span>
          </div>
          <button
            className="icon-btn"
            type="button"
            aria-label="退出登录"
            onClick={() => {
              /* no-op: auth handled out of scope */
            }}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <polyline points="16 17 21 12 16 7" />
              <line x1="21" y1="12" x2="9" y2="12" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  )
}
