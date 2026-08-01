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
          <span className="brand-title">规范智能问答助手 <span className="brand-version">V1.0.6</span></span>
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
        </nav>
      </div>
    </header>
  )
}
