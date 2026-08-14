import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import type { ReactNode, CSSProperties } from 'react'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import Chat from './pages/Chat'
import Report from './pages/Report'
import Ppt from './pages/Ppt'
import LlmConfig from './pages/LlmConfig'
import KbManagement from './pages/KbManagement'
import KbDocuments from './pages/KbDocuments'

function BackendLayout({
  children,
  active,
}: {
  children: ReactNode
  active: 'llm' | 'kb'
}) {
  return (
    <>
      <Header activeNav="backend" />
      <div className="layout-wrapper">
        <Sidebar active={active} />
        {children}
      </div>
    </>
  )
}

// V1.2.7：三个对话界面「常驻 + 显隐」——切换 tab 不卸载页面，正在进行中的 SSE 流
// 与局部 state（消息/进度/结果）都保留，切回时输出仍在持续；只有手动停止才中断。
function MainTabs() {
  const { pathname } = useLocation()
  // 可见页必须保持 flex 纵向布局（body/#root 是 flex column），否则 .chat-main 拿不到
  // flex:1 的高度、消息区无法滚动；隐藏页用 display:none。
  const show = (p: string): CSSProperties => (pathname === p
    ? { display: 'flex', flexDirection: 'column', flex: '1 1 auto', minHeight: 0, overflow: 'hidden' }
    : { display: 'none' })
  return (
    <>
      <div style={show('/')}><Chat /></div>
      <div style={show('/report')}><Report /></div>
      <div style={show('/ppt')}><Ppt /></div>
    </>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route
          path="/admin/llm"
          element={
            <BackendLayout active="llm">
              <LlmConfig />
            </BackendLayout>
          }
        />
        <Route
          path="/admin/kb"
          element={
            <BackendLayout active="kb">
              <KbManagement />
            </BackendLayout>
          }
        />
        <Route
          path="/admin/kb/:id"
          element={
            <BackendLayout active="kb">
              <KbDocuments />
            </BackendLayout>
          }
        />
        <Route path="*" element={<MainTabs />} />
      </Routes>
    </BrowserRouter>
  )
}
