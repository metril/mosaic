import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { Toaster } from '@/components/ui'
import Home from '@/pages/Home'
import Browse from '@/pages/Browse'
import Admin from '@/pages/Admin'

export default function App() {
  return (
    <>
      <div className="app-atmosphere" />
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/" element={<Home />} />
          <Route path="/browse" element={<Browse />} />
          <Route path="/admin" element={<Admin />} />
        </Route>
        {/* /display is still server-rendered in this phase; any other client
            path falls back home. */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  )
}
