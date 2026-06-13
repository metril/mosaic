/** CSRF token management for the SPA.
 *
 *  The Jinja pages inject the session CSRF token into the template; the SPA has
 *  no such injection point, so it reads the same session-bound token from
 *  GET /api/csrf and replays it as the X-CSRF-Token header on mutations.
 *  Cached in memory (never localStorage). On a 403 the caller can force a
 *  refresh and retry once, covering session/token rotation. */

let cached: string | null = null
let inflight: Promise<string> | null = null

export async function getCsrfToken(force = false): Promise<string> {
  if (cached && !force) return cached
  if (inflight && !force) return inflight

  inflight = fetch('/api/csrf', { credentials: 'same-origin' })
    .then((r) => {
      if (!r.ok) throw new Error(`csrf ${r.status}`)
      return r.json()
    })
    .then((j: { csrf_token: string }) => {
      cached = j.csrf_token
      inflight = null
      return cached
    })
    .catch((e) => {
      inflight = null
      throw e
    })

  return inflight
}

export function clearCsrfToken() {
  cached = null
}
