/* =============================================================================
   Display kiosk controller — a faithful port of the imperative script from the
   original templates/display.html. React renders the static markup (same IDs)
   once; this controller takes over imperatively in a mount effect and tears
   everything down on unmount. Behavior is preserved exactly, with three
   deliberate adaptations for the SPA:
     1. Settings arrive via the WS WELCOME message (not Jinja injection), so
        WELCOME re-applies them to every overlay manager.
     2. date-fns is an npm import instead of a global.
     3. alert() (which blocks kiosk browsers) is replaced with an on-screen
        fatal-error panel.
   ============================================================================= */
import { format as dateFnsFormat } from 'date-fns'
import { DEFAULT_SETTINGS } from '@/lib/settingsDefaults'

type Settings = Record<string, any>

export function mountDisplay(wsUrl: string): () => void {
  let currentSettings: Settings = { ...DEFAULT_SETTINGS }

  const $ = (id: string) => document.getElementById(id)

  // ---- Clock ---------------------------------------------------------------
  class Clock {
    settings: Settings
    timeEl = $('clockTime')!
    dateEl = $('clockDate')!
    overlay = $('clockOverlay')!
    gradient = $('clockGradient')!
    updateInterval: number | null = null
    syncInterval: number | null = null
    serverOffset = 0
    constructor(settings: Settings) {
      this.settings = settings
    }
    start() {
      if (!this.settings.show_time && !this.settings.show_date) {
        this.overlay.classList.add('hidden')
        this.gradient.classList.add('hidden')
        return
      }
      this.overlay.classList.remove('hidden')
      this.overlay.className =
        'overlay clock-overlay clock--theme-' + this.settings.overlay_theme + ' position-' + this.settings.clock_position
      this.gradient.classList.remove('hidden')
      this.gradient.className = 'gradient-backdrop clock-gradient clock-gradient--theme-' + this.settings.overlay_theme
      if (this.settings.clock_source !== 'client') {
        this.syncWithServer()
        this.syncInterval = window.setInterval(() => this.syncWithServer(), 15000)
      }
      this.render()
      this.updateInterval = window.setInterval(() => this.render(), 1000)
    }
    async syncWithServer() {
      try {
        const clientBefore = Date.now()
        const response = await fetch('/api/clock?source=' + this.settings.clock_source)
        const clientAfter = Date.now()
        const data = await response.json()
        if (data.datetime) {
          const serverTime = new Date(data.datetime).getTime()
          const clientMidpoint = (clientBefore + clientAfter) / 2
          this.serverOffset = serverTime - clientMidpoint
        }
      } catch (e) {
        console.error('Failed to sync clock:', e)
      }
    }
    getCurrentTime() {
      if (this.settings.clock_source === 'client') return new Date()
      return new Date(Date.now() + this.serverOffset)
    }
    render() {
      const now = this.getCurrentTime()
      if (this.settings.show_time) {
        this.timeEl.textContent = this.formatTime(now)
        this.timeEl.style.display = 'block'
      } else {
        this.timeEl.style.display = 'none'
      }
      if (this.settings.show_date) {
        this.dateEl.textContent = this.formatDate(now)
        this.dateEl.style.display = 'block'
      } else {
        this.dateEl.style.display = 'none'
      }
    }
    formatTime(date: Date) {
      return date.toLocaleTimeString(undefined, {
        hour: '2-digit',
        minute: '2-digit',
        hour12: this.settings.time_format === '12',
      })
    }
    formatDate(date: Date) {
      const format = this.settings.date_format || 'DDDD, MMMM DD'
      const dateFnsFmt = format
        .replace(/DDDD/g, 'eeee')
        .replace(/DDD/g, 'eee')
        .replace(/DD/g, 'dd')
        .replace(/D/g, 'd')
        .replace(/YYYY/g, 'yyyy')
        .replace(/YY/g, 'yy')
      try {
        return dateFnsFormat(date, dateFnsFmt)
      } catch {
        return date.toLocaleDateString()
      }
    }
    stop() {
      if (this.updateInterval) {
        clearInterval(this.updateInterval)
        this.updateInterval = null
      }
      if (this.syncInterval) {
        clearInterval(this.syncInterval)
        this.syncInterval = null
      }
    }
    updateSettings(newSettings: Settings) {
      this.stop()
      this.settings = { ...this.settings, ...newSettings }
      this.start()
    }
  }

  // ---- Weather -------------------------------------------------------------
  class Weather {
    settings: Settings
    overlay = $('weatherOverlay')!
    gradient = $('weatherGradient')!
    updateInterval: number | null = null
    constructor(settings: Settings) {
      this.settings = settings
    }
    start() {
      if (!this.settings.show_weather || !this.settings.weather_ha_entity) {
        this.overlay.classList.add('hidden')
        this.gradient.classList.add('hidden')
        return
      }
      this.overlay.classList.remove('hidden')
      this.overlay.className =
        'overlay weather-overlay weather--theme-' + this.settings.overlay_theme + ' position-' + this.settings.weather_position
      this.gradient.classList.remove('hidden')
      this.gradient.className = 'gradient-backdrop weather-gradient weather-gradient--theme-' + this.settings.overlay_theme
      this.update()
      this.updateInterval = window.setInterval(() => this.update(), 600000)
    }
    async update() {
      try {
        const response = await fetch('/api/weather?entity_id=' + encodeURIComponent(this.settings.weather_ha_entity))
        if (!response.ok) {
          // Flag the overlay as showing stale data rather than silently freezing.
          this.overlay.classList.add('overlay--stale')
          return
        }
        const data = await response.json()
        this.overlay.classList.remove('overlay--stale')
        let forecastData: any = null
        if (this.settings.weather_show_forecast) {
          try {
            const forecastType = this.settings.weather_forecast_type || 'daily'
            const forecastDays = this.settings.weather_forecast_days || 3
            const url =
              '/api/weather/forecast?entity_id=' +
              encodeURIComponent(this.settings.weather_ha_entity) +
              '&forecast_type=' +
              forecastType +
              '&limit=' +
              forecastDays
            const fr = await fetch(url)
            if (fr.ok) forecastData = await fr.json()
          } catch (e) {
            console.error('Failed to fetch forecast:', e)
          }
        }
        this.render(data, forecastData)
      } catch (e) {
        console.error('Failed to fetch weather:', e)
        this.overlay.classList.add('overlay--stale')
      }
    }
    render(data: any, forecastData: any) {
      $('weatherIcon')!.textContent = this.getWeatherIcon(data.condition)
      $('weatherTemp')!.textContent = Math.round(data.temperature) + (data.temperature_unit || '°')
      $('weatherCondition')!.textContent = data.condition ? data.condition.replace(/-/g, ' ') : ''
      const forecastEl = $('weatherForecast')!
      if (
        this.settings.weather_show_forecast &&
        forecastData &&
        forecastData.forecast &&
        forecastData.forecast.length > 0
      ) {
        const isHourly = this.settings.weather_forecast_type === 'hourly'
        const showHighLow = this.settings.weather_show_high_low ?? true
        const showPrecip = this.settings.weather_show_precipitation || false
        forecastEl.innerHTML = forecastData.forecast
          .map((f: any) => {
            const date = new Date(f.datetime)
            const label = isHourly
              ? date.toLocaleTimeString(undefined, { hour: 'numeric' })
              : date.toLocaleDateString(undefined, { weekday: 'short' })
            let tempDisplay = Math.round(f.temperature) + '°'
            if (showHighLow && f.templow != null && !isHourly) {
              tempDisplay = Math.round(f.temperature) + '°/' + Math.round(f.templow) + '°'
            }
            let precipHtml = ''
            if (showPrecip && f.precipitation_probability != null) {
              precipHtml = '<span class="precip">💧' + f.precipitation_probability + '%</span>'
            }
            return (
              '<div class="forecast-day">' +
              '<span class="forecast-label">' +
              label +
              '</span>' +
              '<span class="forecast-icon">' +
              this.getWeatherIcon(f.condition) +
              '</span>' +
              '<span class="forecast-temp">' +
              tempDisplay +
              '</span>' +
              precipHtml +
              '</div>'
            )
          })
          .join('')
        forecastEl.style.display = 'flex'
      } else {
        forecastEl.style.display = 'none'
      }
    }
    getWeatherIcon(condition: string) {
      if (!condition) return '🌡️'
      const c = condition.toLowerCase()
      if (c.includes('sunny') || c.includes('clear')) return c.includes('night') ? '🌙' : '☀️'
      if (c.includes('cloud') || c.includes('overcast')) return c.includes('partly') ? '⛅' : '☁️'
      if (c.includes('rain') || c.includes('drizzle') || c.includes('shower')) return '🌧️'
      if (c.includes('snow') || c.includes('sleet') || c.includes('hail')) return '❄️'
      if (c.includes('thunder') || c.includes('lightning')) return '⚡'
      if (c.includes('fog') || c.includes('mist') || c.includes('haze')) return '🌫️'
      if (c.includes('wind')) return '💨'
      return '🌡️'
    }
    stop() {
      if (this.updateInterval) {
        clearInterval(this.updateInterval)
        this.updateInterval = null
      }
    }
    updateSettings(newSettings: Settings) {
      this.stop()
      this.settings = { ...this.settings, ...newSettings }
      this.start()
    }
  }

  // ---- BackgroundBlur ------------------------------------------------------
  class BackgroundBlur {
    settings: Settings
    backgroundEl = $('backgroundBlur')!
    enabled: boolean
    constructor(settings: Settings) {
      this.settings = settings
      this.enabled = settings.background_blur && settings.image_fit !== 'cover'
    }
    update(blurDataUrl: string | null) {
      if (!this.enabled || !blurDataUrl) {
        this.backgroundEl.classList.add('hidden')
        return
      }
      this.backgroundEl.style.backgroundImage = 'url(' + blurDataUrl + ')'
      this.backgroundEl.classList.remove('hidden')
    }
    clear() {
      this.backgroundEl.classList.add('hidden')
      this.backgroundEl.style.backgroundImage = ''
    }
    updateSettings(newSettings: Settings) {
      this.settings = { ...this.settings, ...newSettings }
      this.enabled = this.settings.background_blur && this.settings.image_fit !== 'cover'
      if (!this.enabled) this.clear()
    }
  }

  // ---- BurnInManager -------------------------------------------------------
  const burnInManager = {
    enabled: false,
    dimInterval: 5,
    dimDuration: 30,
    dimOpacity: 70,
    dimTimer: null as number | null,
    dimRestoreTimer: null as number | null,
    shiftEnabled: true,
    shiftAmount: 5,
    shiftInterval: 60,
    shiftTimer: null as number | null,
    init(settings: Settings) {
      this.stop()
      this.enabled = settings.burn_in_protection || false
      this.dimInterval = settings.burn_in_interval || 5
      this.dimDuration = settings.burn_in_duration || 30
      this.dimOpacity = settings.burn_in_opacity || 70
      this.shiftEnabled = settings.burn_in_shift_enabled !== false
      this.shiftAmount = settings.burn_in_shift_amount || 5
      this.shiftInterval = settings.burn_in_shift_interval || 60
      if (this.enabled) {
        this.startDimming()
        if (this.shiftEnabled) {
          this.shiftCycle()
          this.startShifting()
        }
      }
    },
    startDimming() {
      this.dimTimer = window.setInterval(() => this.dimCycle(), this.dimInterval * 60 * 1000)
    },
    dimCycle() {
      const self = this
      if (this.dimRestoreTimer) {
        clearTimeout(this.dimRestoreTimer)
        this.dimRestoreTimer = null
      }
      document.querySelectorAll<HTMLElement>('.overlay').forEach((el) => {
        el.style.transition = 'opacity 1s ease'
        el.style.opacity = String(self.dimOpacity / 100)
      })
      this.dimRestoreTimer = window.setTimeout(() => {
        self.dimRestoreTimer = null
        document.querySelectorAll<HTMLElement>('.overlay').forEach((el) => {
          el.style.opacity = '1'
        })
      }, this.dimDuration * 1000)
    },
    startShifting() {
      this.shiftTimer = window.setInterval(() => this.shiftCycle(), this.shiftInterval * 1000)
    },
    shiftCycle() {
      const self = this
      document.querySelectorAll<HTMLElement>('.overlay').forEach((el) => {
        const randX = Math.floor(Math.random() * (self.shiftAmount + 1))
        const randY = Math.floor(Math.random() * (self.shiftAmount + 1))
        let shiftX = 0,
          shiftY = 0
        if (el.classList.contains('position-bottom-left')) {
          shiftX = randX
          shiftY = -randY
        } else if (el.classList.contains('position-bottom-right')) {
          shiftX = -randX
          shiftY = -randY
        } else if (el.classList.contains('position-top-left')) {
          shiftX = randX
          shiftY = randY
        } else if (el.classList.contains('position-top-right')) {
          shiftX = -randX
          shiftY = randY
        }
        el.style.transition = 'transform 0.5s ease'
        el.style.transform = 'translate(' + shiftX + 'px, ' + shiftY + 'px)'
      })
    },
    stop() {
      if (this.dimTimer) {
        clearInterval(this.dimTimer)
        this.dimTimer = null
      }
      if (this.shiftTimer) {
        clearInterval(this.shiftTimer)
        this.shiftTimer = null
      }
      if (this.dimRestoreTimer) {
        clearTimeout(this.dimRestoreTimer)
        this.dimRestoreTimer = null
      }
      document.querySelectorAll<HTMLElement>('.overlay').forEach((el) => {
        el.style.transform = ''
        el.style.opacity = '1'
      })
    },
  }

  // ---- LayoutManager -------------------------------------------------------
  class LayoutManager {
    settings: Settings
    container = $('imageContainer')!
    frame1 = $('frame1')!
    frame2 = $('frame2')!
    constructor(settings: Settings) {
      this.settings = settings
    }
    initialize() {
      const layout = this.settings.layout || 'single'
      this.container.className = 'image-container layout-' + layout
      this.frame1.className = 'frame frame--layout-' + layout
      this.frame2.className = 'frame frame--layout-' + layout
      this.frame2.style.display = layout !== 'single' ? 'flex' : 'none'
    }
    updateSettings(newSettings: Settings) {
      this.settings = { ...this.settings, ...newSettings }
      this.initialize()
    }
  }

  // ---- Caption -------------------------------------------------------------
  const CAPTION_ORDER = ['filename', 'date_taken', 'folder', 'location']
  const captionManager = {
    settings: currentSettings as Settings,
    overlay: null as HTMLElement | null,
    textEl: null as HTMLElement | null,
    _init() {
      this.overlay = $('captionOverlay')
      this.textEl = $('captionText')
    },
    updateSettings(s: Settings) {
      this.settings = Object.assign({}, this.settings, s)
      if (!this.overlay) this._init()
      if (this.overlay) {
        this.overlay.classList.remove(
          'position-bottom-left',
          'position-bottom-right',
          'position-top-left',
          'position-top-right',
        )
        this.overlay.classList.add('position-' + (this.settings.caption_position || 'bottom-right'))
      }
    },
    render(caption: any) {
      if (!this.overlay) this._init()
      if (!this.overlay || !this.textEl) return
      if (!this.settings.show_caption || !caption) {
        this.overlay.classList.add('hidden')
        return
      }
      const parts = CAPTION_ORDER.filter((k) => caption[k]).map((k) => caption[k])
      if (!parts.length) {
        this.overlay.classList.add('hidden')
        return
      }
      this.textEl.textContent = parts.join(' · ')
      this.overlay.classList.remove('hidden')
    }
  }

  const clockManager = new Clock(currentSettings)
  const weatherManager = new Weather(currentSettings)
  const blurManager = new BackgroundBlur(currentSettings)
  const layoutManager = new LayoutManager(currentSettings)

  // ---- Start overlay features ----------------------------------------------
  layoutManager.initialize()
  clockManager.start()
  weatherManager.start()
  burnInManager.init(currentSettings)
  captionManager.updateSettings(currentSettings)

  // ---- Kiosk scroll prevention ---------------------------------------------
  const onTouchMove = (e: TouchEvent) => e.preventDefault()
  const onScroll = () => window.scrollTo(0, 0)
  document.addEventListener('touchmove', onTouchMove, { passive: false })
  window.addEventListener('scroll', onScroll, true)
  window.scrollTo(0, 0)

  // =========================================================================
  // WebSocket controller (ported from the IIFE)
  // =========================================================================
  const HEARTBEAT_INTERVAL = 30000
  const RECONNECT_BASE_DELAY = 1000
  const RECONNECT_MAX_DELAY = 30000
  const IMAGE_RETRY_MAX = 3

  const timers: Record<string, number | null> = {
    heartbeat: null,
    reconnect: null,
    switch: null,
    switchAnimation: null,
    debugUpdate: null,
    statusHide: null,
    announcement: null,
  }

  let ws: WebSocket | null = null
  let browserId: string | null = null
  let currentCycle = 0
  let reconnectAttempts = 0
  let debugVisible = false
  let nextSwitchTime: number | null = null

  let pendingBlurUrl: string | null = null
  let pendingCaption: any = null
  let currentCaption: any = null
  let preloadComplete = false
  let switchPending = false

  // Bandwidth-aware preload: a rolling estimate of delivered image throughput
  // (KB/s) from the Resource Timing API. On a slow link we request a smaller
  // variant so preloads land in time and the display doesn't stall. Infinity =
  // unknown/fast (no downgrade).
  let effKbps = Infinity
  const SLOW_KBPS = 700 // ≈5.5 Mbps; below this, cap the preload width
  const DOWNGRADE_WIDTH = 1280
  function recordThroughput(el: HTMLImageElement) {
    try {
      const src = el.currentSrc || el.src
      const entries = performance.getEntriesByName(src) as PerformanceResourceTiming[]
      const e = entries[entries.length - 1]
      if (!e) return
      const bytes = e.encodedBodySize || e.transferSize || 0
      const ms = e.responseEnd - e.requestStart
      if (bytes > 20000 && ms > 0) {
        const kbps = bytes / 1024 / (ms / 1000)
        effKbps = effKbps === Infinity ? kbps : effKbps * 0.6 + kbps * 0.4
      }
    } catch {
      /* Resource Timing unavailable (e.g. cross-origin) — leave estimate as-is. */
    }
  }
  function maybeDowngrade(url: string): string {
    // Only when we've measured a slow link and the server URL hasn't already
    // pinned a width (so we never upscale or fight an explicit size).
    if (effKbps >= SLOW_KBPS || /[?&]width=/.test(url)) return url
    return url + (url.includes('?') ? '&' : '?') + 'width=' + DOWNGRADE_WIDTH
  }

  const statusIndicator = $('statusIndicator')!
  let currentImage = $('currentImage') as HTMLImageElement
  let preloadImage = $('preloadImage') as HTMLImageElement
  const debugOverlay = $('debugOverlay')!

  function clearAllTimers() {
    Object.keys(timers).forEach((key) => {
      if (timers[key]) {
        if (key === 'heartbeat' || key === 'debugUpdate') clearInterval(timers[key]!)
        else clearTimeout(timers[key]!)
        timers[key] = null
      }
    })
  }

  function updateDebug(field: string, value: any) {
    const el = $('debug' + field)
    if (el) el.textContent = String(value)
  }

  function toggleDebug() {
    debugVisible = !debugVisible
    debugOverlay.classList.toggle('visible', debugVisible)
  }

  const onKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'd' || e.key === 'D') toggleDebug()
  }
  document.addEventListener('keydown', onKeyDown)

  function setStatus(status: string) {
    if (timers.statusHide) {
      clearTimeout(timers.statusHide)
      timers.statusHide = null
    }
    statusIndicator.classList.remove('hidden')
    statusIndicator.className = 'status-indicator ' + status
    updateDebug('Status', status)
    if (status === 'connected') {
      showOfflineNotice(false)
      timers.statusHide = window.setTimeout(() => {
        statusIndicator.classList.add('hidden')
        timers.statusHide = null
      }, 3000)
    }
  }

  /** Show/hide an unobtrusive on-screen notice when the display has been
   *  offline long enough that the operator should notice (silent WS drops on a
   *  wall-mounted TV are otherwise invisible). */
  function showOfflineNotice(show: boolean) {
    const existing = document.getElementById('display-offline')
    if (show) {
      if (existing) return
      const el = document.createElement('div')
      el.id = 'display-offline'
      el.textContent = 'Reconnecting… display offline'
      document.body.appendChild(el)
    } else {
      existing?.remove()
    }
  }

  function resetState() {
    browserId = null
    currentCycle = 0
    nextSwitchTime = null
    updateDebug('BrowserId', '-')
    updateDebug('Cycle', '0')
    updateDebug('NextSwitch', '-')
    updateDebug('CurrentImage', '-')
    updateDebug('PreloadImage', '-')
  }

  function connect() {
    if (timers.reconnect) {
      clearTimeout(timers.reconnect)
      timers.reconnect = null
    }
    if (ws && ws.readyState !== WebSocket.CLOSED) ws.close()
    setStatus('connecting')
    try {
      ws = new WebSocket(wsUrl)
    } catch (e) {
      console.error('WebSocket creation failed:', e)
      scheduleReconnect()
      return
    }
    ws.onopen = () => {
      setStatus('connected')
      reconnectAttempts = 0
      startHeartbeat()
    }
    ws.onclose = (event) => {
      setStatus('disconnected')
      stopHeartbeat()
      if (event.code === 4004) {
        showFatal('Sync group not found. Create it via /admin first.')
      } else if (reconnectAttempts !== Infinity) {
        scheduleReconnect()
      }
    }
    ws.onerror = (error) => console.error('WebSocket error:', error)
    ws.onmessage = (event) => {
      try {
        handleMessage(JSON.parse(event.data))
      } catch (e) {
        console.error('Failed to parse message:', e)
      }
    }
  }

  function scheduleReconnect() {
    if (timers.reconnect) clearTimeout(timers.reconnect)
    reconnectAttempts++
    // Surface a persistent notice once reconnects have been failing for a while.
    if (reconnectAttempts >= 3) showOfflineNotice(true)
    const delay = Math.min(RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts - 1), RECONNECT_MAX_DELAY)
    timers.reconnect = window.setTimeout(connect, delay)
  }

  function startHeartbeat() {
    stopHeartbeat()
    timers.heartbeat = window.setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: 'HEARTBEAT' }))
    }, HEARTBEAT_INTERVAL)
  }
  function stopHeartbeat() {
    if (timers.heartbeat) {
      clearInterval(timers.heartbeat)
      timers.heartbeat = null
    }
  }

  function handleMessage(msg: any) {
    switch (msg.type) {
      case 'WELCOME':
        handleWelcome(msg)
        break
      case 'ASSIGN':
        handleAssign(msg)
        break
      case 'PRELOAD':
        handlePreload(msg)
        break
      case 'SWITCH':
        handleSwitch(msg)
        break
      case 'SETTINGS_CHANGED':
        handleSettingsChanged(msg)
        break
      case 'NIGHT_MODE':
        handleNightMode(msg)
        break
      case 'DISCONNECT':
        handleDisconnect(msg)
        break
      case 'NO_IMAGES':
        handleNoImages(msg)
        break
      case 'ANNOUNCEMENT':
        handleAnnouncement(msg)
        break
      case 'CLEAR_ANNOUNCEMENT':
        clearAnnouncement()
        break
    }
  }

  // ---- Announcements (admin-pushed messages) ------------------------------
  function clearAnnouncement() {
    if (timers.announcement) {
      clearTimeout(timers.announcement)
      timers.announcement = null
    }
    const el = $('announcementOverlay')
    if (el) el.classList.add('hidden')
  }

  function handleAnnouncement(msg: any) {
    const el = $('announcementOverlay')
    const textEl = $('announcementText')
    if (!el || !textEl) return
    if (timers.announcement) {
      clearTimeout(timers.announcement)
      timers.announcement = null
    }
    textEl.textContent = String(msg.text || '')
    el.className =
      'overlay announcement-overlay position-' +
      (msg.position || 'bottom-left') +
      ' announce--theme-' +
      (msg.theme || 'solid')
    // reflow so the entry animation replays on a repeated announcement
    void el.offsetWidth
    el.classList.remove('hidden')
    const durMs = Math.max(1, Number(msg.duration_s) || 15) * 1000
    timers.announcement = window.setTimeout(clearAnnouncement, durMs)
  }

  function applyAllSettings() {
    clockManager.updateSettings(currentSettings)
    weatherManager.updateSettings(currentSettings)
    blurManager.updateSettings(currentSettings)
    layoutManager.updateSettings(currentSettings)
    burnInManager.init(currentSettings)
    captionManager.updateSettings(currentSettings)
    applyImageFit()
  }

  function handleWelcome(msg: any) {
    resetState()
    clearNoImagesError()
    // Settings now arrive over the WS (not Jinja) — apply them to every manager.
    if (msg.settings) {
      currentSettings = Object.assign({}, currentSettings, msg.settings)
      applyAllSettings()
    }
    browserId = msg.browser_id
    updateDebug('BrowserId', browserId)
    updateDebug('BrowserCount', msg.browser_count)
    captionManager.updateSettings(currentSettings)
    handleNightMode({
      active: !!msg.night_active,
      action: currentSettings.night_action,
      opacity: currentSettings.night_dim_opacity,
    })
  }

  function applyImageFit() {
    const fit = currentSettings.image_fit === 'cover' ? 'fit-cover' : 'fit-contain'
    document.querySelectorAll<HTMLElement>('.screensaver-image').forEach((img) => {
      img.classList.remove('fit-contain', 'fit-cover')
      img.classList.add(fit)
    })
  }

  function handleNightMode(msg: any) {
    let overlay = document.getElementById('nightOverlay')
    if (!overlay) {
      overlay = document.createElement('div')
      overlay.id = 'nightOverlay'
      document.body.appendChild(overlay)
    }
    if (msg.active) {
      const action = msg.action || 'blank'
      const opacity = action === 'dim' ? Math.min(0.9, Math.max(0, (parseInt(msg.opacity) || 20) / 100)) : 1
      overlay.style.background = '#000'
      overlay.style.opacity = String(action === 'dim' ? opacity : 1)
      overlay.style.display = 'block'
      overlay.classList.add('active')
    } else {
      overlay.classList.remove('active')
      overlay.style.opacity = '0'
      setTimeout(() => {
        if (overlay && !overlay.classList.contains('active')) overlay.style.display = 'none'
      }, 800)
    }
  }

  function handleAssign(msg: any) {
    clearNoImagesError()
    // An ASSIGN is authoritative (e.g. a history jump arriving mid-cycle): cancel
    // any in-flight preload/switch so a pending client-side switch can't override
    // the image we're about to show.
    if (timers.switch) {
      clearTimeout(timers.switch)
      timers.switch = null
    }
    preloadComplete = false
    switchPending = false
    nextSwitchTime = null
    preloadImage.classList.remove('visible')
    preloadImage.classList.add('hidden')
    currentCycle = msg.cycle
    updateDebug('Cycle', currentCycle)
    updateDebug('CurrentImage', msg.image_id)
    const blurUrl = blurManager.enabled && msg.blur_url ? msg.blur_url : null
    loadImage(currentImage, msg.image_url, () => {
      if (blurUrl) blurManager.update(blurUrl)
      applyKenBurns(currentImage)
      currentCaption = msg.caption || null
      captionManager.render(currentCaption)
      sendReady()
    })
  }

  function handlePreload(msg: any) {
    preloadComplete = false
    switchPending = false
    clearNoImagesError()
    nextSwitchTime = msg.switch_at
    updateDebug('PreloadImage', msg.image_id)
    const now = Date.now()
    updateDebug('NextSwitch', Math.round(Math.max(0, msg.switch_at - now) / 1000) + 's')
    pendingBlurUrl = blurManager.enabled && msg.blur_url ? msg.blur_url : null
    pendingCaption = msg.caption || null
    loadImage(
      preloadImage,
      maybeDowngrade(msg.image_url),
      () => {
        preloadComplete = true
        sendPreloadReady()
        if (switchPending) {
          switchPending = false
          performSwitch()
        }
      },
      () => {
        switchPending = false
      },
    )
  }

  function handleSwitch(_msg: any) {
    const now = Date.now()
    const switchDelay = nextSwitchTime ? Math.max(0, nextSwitchTime - now) : 0
    if (timers.switch) {
      clearTimeout(timers.switch)
      timers.switch = null
    }
    if (timers.switchAnimation) {
      clearTimeout(timers.switchAnimation)
      timers.switchAnimation = null
    }
    if (switchDelay > 0) timers.switch = window.setTimeout(performSwitch, switchDelay)
    else performSwitch()
  }

  function transitionDuration() {
    const eff = currentSettings.transition_effect || 'fade'
    if (eff === 'none') return 0
    return Math.max(0, parseInt(currentSettings.transition_duration_ms) || 500)
  }

  function applyKenBurns(imgEl: HTMLElement) {
    imgEl.style.animation = ''
    imgEl.style.removeProperty('--kb-scale')
    imgEl.style.removeProperty('--kb-x')
    imgEl.style.removeProperty('--kb-y')
    if (!currentSettings.ken_burns) {
      imgEl.style.transform = ''
      return
    }
    const intensity = Math.max(0, parseInt(currentSettings.ken_burns_intensity) || 15)
    const scale = 1 + intensity / 100
    let dx = 0,
      dy = 0
    while (dx === 0 && dy === 0) {
      dx = Math.floor(Math.random() * 3) - 1
      dy = Math.floor(Math.random() * 3) - 1
    }
    const pan = Math.min(6, intensity / 3)
    imgEl.style.setProperty('--kb-scale', scale.toFixed(3))
    imgEl.style.setProperty('--kb-x', (dx * pan).toFixed(2) + '%')
    imgEl.style.setProperty('--kb-y', (dy * pan).toFixed(2) + '%')
    const dur = Math.max(5, parseInt(currentSettings.ken_burns_duration) || 20)
    // Finish the pan/zoom within ~80% of the actual time on screen, then hold
    // (ease-out + forwards) so each image "breathes" instead of cutting mid-move
    // when the cycle is shorter than the configured duration.
    const remaining = nextSwitchTime ? (nextSwitchTime - Date.now()) / 1000 : dur
    const effDur = remaining > 1 ? Math.max(5, Math.min(dur, remaining * 0.8)) : dur
    void imgEl.offsetWidth
    imgEl.style.animation = `kenburns ${effDur.toFixed(1)}s ease-out forwards`
  }

  function clearKenBurns(imgEl: HTMLElement) {
    imgEl.style.animation = ''
    imgEl.style.transform = ''
  }

  function performSwitch() {
    timers.switch = null
    if (!preloadComplete) {
      switchPending = true
      return
    }
    if (pendingBlurUrl) {
      blurManager.update(pendingBlurUrl)
      pendingBlurUrl = null
    }
    currentCaption = pendingCaption
    captionManager.render(currentCaption)
    const durMs = transitionDuration()
    const effect = currentSettings.transition_effect || 'fade'
    currentImage.style.setProperty('--transition-ms', durMs + 'ms')
    preloadImage.style.setProperty('--transition-ms', durMs + 'ms')
    currentImage.classList.add('img-transition')
    preloadImage.classList.add('img-transition')
    if ((effect === 'slide' || effect === 'dissolve') && durMs > 0) {
      const anim = effect === 'slide' ? 'slide-in' : 'dissolve-in'
      preloadImage.style.animation = `${anim} ${durMs}ms ease-in-out forwards`
    }
    currentImage.classList.remove('visible')
    currentImage.classList.add('hidden')
    preloadImage.classList.remove('hidden')
    preloadImage.classList.add('visible')
    timers.switchAnimation = window.setTimeout(() => {
      timers.switchAnimation = null
      currentImage.classList.remove('img-transition', 'fade-transition')
      preloadImage.classList.remove('img-transition', 'fade-transition')
      preloadImage.style.animation = ''
      applyKenBurns(preloadImage)
      const temp = currentImage
      currentImage = preloadImage
      preloadImage = temp
      clearKenBurns(preloadImage)
      preloadImage.onload = null
      preloadImage.onerror = null
      preloadComplete = false
      switchPending = false
      updateDebug('CurrentImage', currentImage.src.split('/').pop()!.split('?')[0])
      updateDebug('PreloadImage', '-')
      updateDebug('NextSwitch', '-')
      sendSwitchComplete()
    }, Math.max(50, durMs))
  }

  function handleSettingsChanged(msg: any) {
    if (msg.settings) {
      currentSettings = Object.assign({}, currentSettings, msg.settings)
      clockManager.updateSettings(msg.settings)
      weatherManager.updateSettings(msg.settings)
      blurManager.updateSettings(msg.settings)
      layoutManager.updateSettings(msg.settings)
      burnInManager.init(msg.settings)
      captionManager.updateSettings(msg.settings)
      captionManager.render(currentCaption)
      applyImageFit()
      applyKenBurns(currentImage)
    }
  }

  function handleDisconnect(msg: any) {
    if (timers.reconnect) {
      clearTimeout(timers.reconnect)
      timers.reconnect = null
    }
    reconnectAttempts = Infinity
    if (ws) ws.close()
    showFatal('Disconnected: ' + (msg.reason || 'Group deleted'))
  }

  function handleNoImages(msg: any) {
    clearNoImagesError()
    const errorDiv = document.createElement('div')
    errorDiv.id = 'no-images-error'
    const errorMsg = (msg.error || 'No images configured for this group')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
    errorDiv.innerHTML =
      '<div style="font-size:48px;margin-bottom:20px;">⚠</div>' +
      '<div style="margin-bottom:10px;">No Images Available</div>' +
      '<div style="font-size:16px;color:#aaa;">' +
      errorMsg +
      '</div>'
    document.body.appendChild(errorDiv)
  }

  function clearNoImagesError() {
    document.getElementById('no-images-error')?.remove()
  }

  /** On-screen replacement for alert() (which blocks kiosk browsers). */
  function showFatal(message: string) {
    document.getElementById('display-fatal')?.remove()
    const div = document.createElement('div')
    div.id = 'display-fatal'
    div.innerHTML =
      '<div style="font-size:48px;margin-bottom:20px;">⚠</div>' +
      '<div>' +
      message.replace(/</g, '&lt;').replace(/>/g, '&gt;') +
      '</div>'
    document.body.appendChild(div)
  }

  function loadImage(
    imgElement: HTMLImageElement,
    url: string,
    onSuccess?: () => void,
    onFailure?: () => void,
  ) {
    imgElement.onload = null
    imgElement.onerror = null
    let retryCount = 0
    imgElement.onload = () => {
      recordThroughput(imgElement)
      if (onSuccess) onSuccess()
    }
    imgElement.onerror = () => {
      retryCount++
      if (retryCount < IMAGE_RETRY_MAX) {
        const delay = 1000 * Math.pow(2, retryCount - 1)
        setTimeout(() => {
          imgElement.src = url + (url.includes('?') ? '&' : '?') + 'retry=' + Date.now()
        }, delay)
      } else {
        const pathMatch = url.match(/\/api\/image\/([^?]+)/)
        if (pathMatch) {
          send({ type: 'IMAGE_LOAD_FAILED', image_path: decodeURIComponent(pathMatch[1]) })
        }
        if (onFailure) onFailure()
      }
    }
    imgElement.src = url
  }

  function sendReady() {
    send({ type: 'READY' })
  }
  function sendPreloadReady() {
    send({ type: 'PRELOAD_READY' })
  }
  function sendSwitchComplete() {
    send({ type: 'SWITCH_COMPLETE' })
  }
  function send(msg: any) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg))
  }

  timers.debugUpdate = window.setInterval(() => {
    if (nextSwitchTime) {
      const remaining = Math.max(0, nextSwitchTime - Date.now())
      if (remaining > 0) updateDebug('NextSwitch', (remaining / 1000).toFixed(1) + 's')
    }
  }, 100)

  const onBeforeUnload = () => {
    clearAllTimers()
    if (ws) ws.close()
  }
  window.addEventListener('beforeunload', onBeforeUnload)

  connect()

  // ---- Teardown (React unmount / hard navigation) --------------------------
  return function teardown() {
    reconnectAttempts = Infinity
    clearAllTimers()
    clockManager.stop()
    weatherManager.stop()
    burnInManager.stop()
    if (ws) {
      ws.onclose = null
      ws.close()
      ws = null
    }
    document.removeEventListener('touchmove', onTouchMove)
    window.removeEventListener('scroll', onScroll, true)
    document.removeEventListener('keydown', onKeyDown)
    window.removeEventListener('beforeunload', onBeforeUnload)
    document.getElementById('nightOverlay')?.remove()
    clearNoImagesError()
    document.getElementById('display-fatal')?.remove()
    document.getElementById('display-offline')?.remove()
  }
}
