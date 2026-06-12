import { Routes, Route, Link, useLocation } from 'react-router-dom'

// Phase 0 scaffold: placeholder routes prove out the build + SPA serving.
// Real pages (Home, Browse, Admin, Display) and the AppShell nav arrive in
// later phases.

function Placeholder({ title }: { title: string }) {
  const { pathname } = useLocation()
  return (
    <div className="placeholder">
      <h1>Screensaver</h1>
      <p className="placeholder-route">{title}</p>
      <code>{pathname}</code>
      <nav className="placeholder-nav">
        <Link to="/">Home</Link>
        <Link to="/browse">Browse</Link>
        <Link to="/admin">Admin</Link>
      </nav>
    </div>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Placeholder title="Home" />} />
      <Route path="/browse" element={<Placeholder title="Browse" />} />
      <Route path="/admin" element={<Placeholder title="Admin" />} />
      <Route path="/display" element={<Placeholder title="Display" />} />
      <Route path="*" element={<Placeholder title="Not found" />} />
    </Routes>
  )
}
