import { useEffect, useState } from 'react'
import { mountDisplay } from '@/lib/displayController'
import '@/styles/display.css'

// Overlay theme CSS served by the backend (same files the original kiosk used).
const OVERLAY_CSS = [
  '/css/vars.css',
  '/css/overlays.css',
  '/css/themes/theme-fade.css',
  '/css/themes/theme-solid.css',
  '/css/themes/theme-bubble.css',
  '/css/themes/theme-blur.css',
]

function ensureOverlayCss() {
  for (const href of OVERLAY_CSS) {
    if (!document.querySelector(`link[data-display-css="${href}"]`)) {
      const link = document.createElement('link')
      link.rel = 'stylesheet'
      link.href = href
      link.setAttribute('data-display-css', href)
      document.head.appendChild(link)
    }
  }
}

function buildWsUrl(): string | null {
  const params = new URLSearchParams(window.location.search)
  const group = params.get('group')
  if (!group) return null
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  let url = `${proto}://${window.location.host}/ws/display/${encodeURIComponent(group)}`
  const qs = new URLSearchParams()
  const endpoint = params.get('endpoint')
  const key = params.get('key')
  if (endpoint) qs.set('endpoint', endpoint)
  if (key) qs.set('key', key)
  const q = qs.toString()
  return q ? `${url}?${q}` : url
}

interface GroupLite {
  group_id: string
  image_count: number
  browser_count: number
}

/** Shown when /display is opened without a group (e.g. the dashboard's "Open
 *  kiosk" action). Lists groups to join; auto-opens when there's exactly one. */
function GroupPicker() {
  const [groups, setGroups] = useState<GroupLite[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    fetch('/api/display/groups', { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: GroupLite[]) => {
        if (!alive) return
        if (data.length === 1) {
          // Single group → just open its kiosk.
          window.location.replace(`/display?group=${encodeURIComponent(data[0].group_id)}`)
          return
        }
        setGroups(data)
      })
      .catch((e) => alive && setError(String(e)))
    return () => {
      alive = false
    }
  }, [])

  let body
  if (error) {
    body = <div className="dp-picker__msg">Couldn’t load display groups ({error}).</div>
  } else if (!groups) {
    body = <div className="dp-picker__msg">Loading display groups…</div>
  } else if (groups.length === 0) {
    body = (
      <div className="dp-picker__msg">
        No display groups yet. Create one in <a href="/admin">Admin</a>.
      </div>
    )
  } else {
    body = (
      <div className="dp-picker__list">
        {groups.map((g) => (
          <a key={g.group_id} className="dp-group" href={`/display?group=${encodeURIComponent(g.group_id)}`}>
            <span className="dp-group__name">{g.group_id}</span>
            <span className="dp-group__count">{g.image_count} photos</span>
          </a>
        ))}
      </div>
    )
  }

  return (
    <div className="display-root">
      <div id="display-fatal" className="dp-picker">
        <div className="dp-picker__title">Open a display</div>
        <div className="dp-picker__hint">Pick a group to start its kiosk slideshow.</div>
        {body}
      </div>
    </div>
  )
}

export default function Display() {
  const wsUrl = buildWsUrl()

  useEffect(() => {
    if (!wsUrl) return
    ensureOverlayCss()
    document.body.style.overflow = 'hidden'
    const teardown = mountDisplay(wsUrl)
    return () => {
      teardown()
      document.body.style.overflow = ''
    }
  }, [wsUrl])

  if (!wsUrl) return <GroupPicker />

  // Static markup — rendered once, then the controller drives it imperatively.
  // IDs/classes match the original display.html so the ported logic works.
  return (
    <div className="display-root">
      <div className="status-indicator connecting" id="statusIndicator" />

      <div className="image-container layout-single" id="imageContainer">
        <div className="frame--background hidden" id="backgroundBlur" />

        <div className="frame frame--layout-single" id="frame1">
          <img id="currentImage" className="screensaver-image visible fit-contain" alt="" />
          <img id="preloadImage" className="screensaver-image hidden fit-contain" alt="" />
        </div>

        <div className="frame frame--layout-single" id="frame2" style={{ display: 'none' }}>
          <img id="currentImage2" className="screensaver-image visible fit-contain" alt="" />
          <img id="preloadImage2" className="screensaver-image hidden fit-contain" alt="" />
        </div>
      </div>

      <div className="overlay-container" id="overlayContainer">
        <div className="gradient-backdrop clock-gradient clock-gradient--theme-fade hidden" id="clockGradient" />
        <div className="gradient-backdrop weather-gradient weather-gradient--theme-fade hidden" id="weatherGradient" />

        <div className="overlay clock-overlay clock--theme-fade position-bottom-left hidden" id="clockOverlay">
          <div className="clock-time" id="clockTime" />
          <div className="clock-date" id="clockDate" />
        </div>

        <div className="overlay weather-overlay weather--theme-fade position-bottom-right hidden" id="weatherOverlay">
          <div className="weather-icon" id="weatherIcon" />
          <div className="weather-temp" id="weatherTemp" />
          <div className="weather-condition" id="weatherCondition" />
          <div className="weather-forecast" id="weatherForecast" />
        </div>

        <div className="overlay caption-overlay clock--theme-fade position-bottom-right hidden" id="captionOverlay">
          <div className="caption-text" id="captionText" />
        </div>
      </div>

      <div className="debug-overlay" id="debugOverlay">
        <dl>
          <dt>Browser ID:</dt>
          <dd id="debugBrowserId">-</dd>
          <dt>Status:</dt>
          <dd id="debugStatus">connecting</dd>
          <dt>Cycle:</dt>
          <dd id="debugCycle">0</dd>
          <dt>Next switch:</dt>
          <dd id="debugNextSwitch">-</dd>
          <dt>Current:</dt>
          <dd id="debugCurrentImage">-</dd>
          <dt>Preload:</dt>
          <dd id="debugPreloadImage">-</dd>
          <dt>Browsers:</dt>
          <dd id="debugBrowserCount">0</dd>
        </dl>
      </div>
    </div>
  )
}
