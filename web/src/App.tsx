import { lazy, Suspense } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { Toaster } from '@/components/ui'

// Every page is its own lazy chunk: the kiosk (low-power devices, 24/7)
// downloads none of the dashboard code, and dashboard visitors don't pay for
// tabs they never open.
const Display = lazy(() => import('@/pages/Display'))
const Home = lazy(() => import('@/pages/Home'))
const Browse = lazy(() => import('@/pages/Browse'))
const Admin = lazy(() => import('@/pages/Admin'))

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

        <Route element={<AppShell />}>
          <Route
            path="/"
            element={
              <Suspense fallback={null}>
                <Home />
              </Suspense>
            }
          />
          <Route
            path="/browse"
            element={
              <Suspense fallback={null}>
                <Browse />
              </Suspense>
            }
          />
          <Route
            path="/admin"
            element={
              <Suspense fallback={null}>
                <Admin />
              </Suspense>
            }
          />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <Toaster />
    </>
  )
}
