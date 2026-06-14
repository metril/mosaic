import { useEffect, useState } from 'react'

/** A shared ticker that returns Date.now() and re-renders on an interval, so
 *  multiple countdowns stay live without each spinning up its own timer. */
export function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

/** Whole seconds until `ts` (an ISO timestamp), or null if absent/past. */
export function secondsUntil(ts: string | null | undefined, now: number): number | null {
  if (!ts) return null
  const t = new Date(ts).getTime()
  if (Number.isNaN(t)) return null
  const s = Math.round((t - now) / 1000)
  return s > 0 ? s : null
}
