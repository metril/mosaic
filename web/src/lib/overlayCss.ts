/** The server-served overlay theme stylesheets (clock/weather/caption gradients
 *  and positioning) used by the kiosk. Loaded on demand as <link>s so both the
 *  real Display page and the admin preview render overlays identically. */
const OVERLAY_CSS = [
  '/css/vars.css',
  '/css/overlays.css',
  '/css/themes/theme-fade.css',
  '/css/themes/theme-solid.css',
  '/css/themes/theme-bubble.css',
  '/css/themes/theme-blur.css',
]

/** Idempotently inject the overlay theme stylesheets into <head>. */
export function ensureOverlayCss(): void {
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
