import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { Toaster } from '@/components/ui'
import Home from '@/pages/Home'
import Browse from '@/pages/Browse'
import Admin from '@/pages/Admin'

// The kiosk runs on low-power devices and needs none of the dashboard code, so
// it's a separate lazy chunk.
const Display = lazy(() => import('@/pages/Display'))

export default function App() {
  return (
    <>
      <Routes>
        {/* Chrome-free kiosk — outside the AppShell, no nav/atmosphere. */}
        <Route
          path="/display"
          element={
            <Suspense fallback={<div style={{ position: 'fixed', inset: 0, background: '#000' }} />}>
              <Display />
            </Suspense>
          }
        />

        <Route
          element={
            <>
              <div className="app-atmosphere" />
              <AppShell />
            </>
          }
        >
          <Route path="/" element={<Home />} />
          <Route path="/browse" element={<Browse />} />
          <Route path="/admin" element={<Admin />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  )
}
