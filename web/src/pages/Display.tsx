import { useEffect } from 'react'
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

  if (!wsUrl) {
    return (
      <div className="display-root">
        <div id="display-fatal">
          <div style={{ fontSize: 48, marginBottom: 20 }}>⚠</div>
          <div>Missing “group” parameter.</div>
          <div style={{ fontSize: 16, color: '#aaa', marginTop: 8 }}>
            Open this page as <code>/display?group=&lt;id&gt;</code>.
          </div>
        </div>
      </div>
    )
  }

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
