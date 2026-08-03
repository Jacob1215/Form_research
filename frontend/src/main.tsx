import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { APP_NAME, APP_VERSION } from './version'
import './index.css'

// 页面标题（含版本号）在运行时由统一常量设置，index.html 无需随版本更新
document.title = `${APP_NAME} ${APP_VERSION}`

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
