import { Link } from 'react-router-dom'
import { APP_NAME, APP_VERSION } from '../version'

interface HeaderProps {
  activeNav: 'chat' | 'report' | 'backend'
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
          <span className="brand-title">{APP_NAME} <span className="brand-version">{APP_VERSION}</span></span>
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
            to="/report"
            className={`nav-link${activeNav === 'report' ? ' is-active' : ''}`}
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
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" x2="8" y1="13" y2="13" />
              <line x1="16" x2="8" y1="17" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            <span>报告总结</span>
          </Link>
        </nav>
      </div>
    </header>
  )
}
