import { BrowserRouter, Routes, Route } from 'react-router-dom'
import type { ReactNode } from 'react'
import Header from './components/Header'
import Sidebar from './components/Sidebar'
import Chat from './pages/Chat'
import Report from './pages/Report'
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

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Chat />} />
        <Route path="/report" element={<Report />} />
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
      </Routes>
    </BrowserRouter>
  )
}
