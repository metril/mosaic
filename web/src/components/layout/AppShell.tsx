import { useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { cn } from '@/lib/cn'
import { IconButton, GridIcon, ImageIcon, SettingsIcon, LogOutIcon } from '@/components/ui'
import { ThemeToggle } from './ThemeToggle'
import styles from './AppShell.module.css'

interface NavItem {
  to: string
  label: string
  icon: React.ReactNode
  /** When true, navigate with a full page load (route still served by Jinja). */
  external?: boolean
}

const NAV: NavItem[] = [
  { to: '/', label: 'Home', icon: <GridIcon size={17} /> },
  { to: '/browse', label: 'Browse', icon: <ImageIcon size={17} /> },
  { to: '/admin', label: 'Admin', icon: <SettingsIcon size={17} /> },
]

function initials(name: string) {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '')).toUpperCase() || '?'
}

export function AppShell() {
  const { user } = useAuth()
  const { pathname } = useLocation()
  const [open, setOpen] = useState(false)

  return (
    <div className={styles.shell}>
      <header className={styles.nav}>
        <NavLink to="/" className={styles.brand} onClick={() => setOpen(false)}>
          <span className={styles.brandMark} aria-hidden="true">
            <ImageIcon size={18} />
          </span>
          Mosaic
        </NavLink>

        <nav className={cn(styles.links, !open && styles.linksClosed)} onClick={() => setOpen(false)}>
          {NAV.map((item) =>
            item.external ? (
              <a key={item.to} href={item.to} className={styles.link}>
                {item.icon}
                {item.label}
              </a>
            ) : (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) => cn(styles.link, isActive && styles.linkActive)}
              >
                {item.icon}
                {item.label}
              </NavLink>
            ),
          )}
        </nav>

        <span className={styles.spacer} />

        <div className={styles.right}>
          <ThemeToggle />
          <IconButton
            label="Log out"
            onClick={() => window.location.assign('/auth/logout?redirect=/auth/login')}
          >
            <LogOutIcon size={18} />
          </IconButton>
          {user && (
            <div className={styles.user}>
              <span className={styles.avatar} aria-hidden="true">
                {initials(user.name || user.email || '?')}
              </span>
              <span className={styles.userName}>{user.name || user.email}</span>
            </div>
          )}
          <IconButton
            label="Menu"
            className={styles.burger}
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
          >
            <span style={{ fontSize: 18, lineHeight: 1 }}>{open ? '✕' : '☰'}</span>
          </IconButton>
        </div>
      </header>

      <main className={styles.main} key={pathname}>
        <Outlet />
      </main>
    </div>
  )
}
