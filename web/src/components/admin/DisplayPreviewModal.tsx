import { useQuery } from '@tanstack/react-query'
import type { DisplayGroupSettings } from '@/api/types'
import { fetchEndpointCaption } from '@/api/admin'
import { thumbUrl } from '@/api/browse'
import { Modal } from '@/components/ui'
import { useNow } from '@/hooks/useNow'
import { cn } from '@/lib/cn'
import styles from './DisplayPreviewModal.module.css'

type Position = 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right'

const CAPTION_ORDER = ['filename', 'date_taken', 'folder', 'location']

function posClass(p: Position) {
  return p === 'top-left'
    ? styles.posTopLeft
    : p === 'top-right'
      ? styles.posTopRight
      : p === 'bottom-left'
        ? styles.posBottomLeft
        : styles.posBottomRight
}

function themeClass(theme: string) {
  return theme === 'solid'
    ? styles.themeSolid
    : theme === 'bubble'
      ? styles.themeBubble
      : theme === 'blur'
        ? styles.themeBlur
        : undefined // 'fade' → text-shadow only
}

interface WeatherResp {
  temperature?: number
  temperature_unit?: string
  condition?: string
}

interface Props {
  groupId: string
  endpointId: string
  endpointName?: string | null
  currentImage: string | null
  settings: DisplayGroupSettings
  onClose: () => void
}

/** Reconstructed preview of what a display is showing: the current frame plus
 *  the group's caption / clock / weather overlays. Does NOT open a display
 *  connection, so it never affects image distribution. */
export function DisplayPreviewModal({
  groupId,
  endpointId,
  endpointName,
  currentImage,
  settings,
  onClose,
}: Props) {
  const now = useNow(1000)
  const s = settings
  const theme = themeClass(s.overlay_theme)

  const captionQ = useQuery({
    queryKey: ['ep-caption', groupId, endpointId, currentImage],
    queryFn: () => fetchEndpointCaption(groupId, endpointId),
    enabled: !!s.show_caption && !!currentImage,
  })
  const captionText = (() => {
    const c = captionQ.data?.caption
    if (!c) return ''
    return CAPTION_ORDER.filter((k) => c[k]).map((k) => c[k]).join(' · ')
  })()

  const weatherQ = useQuery({
    queryKey: ['preview-weather', s.weather_ha_entity],
    queryFn: async () => {
      const r = await fetch(`/api/weather?entity_id=${encodeURIComponent(s.weather_ha_entity!)}`)
      if (!r.ok) throw new Error(`HTTP ${r.status}`)
      return (await r.json()) as WeatherResp
    },
    enabled: !!s.show_weather && !!s.weather_ha_entity,
    refetchInterval: 600_000,
  })

  const showClock = s.show_time || s.show_date
  const d = new Date(now)
  const timeStr = d.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: s.time_format === '12',
  })
  const dateStr = d.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })

  return (
    <Modal
      title={`Preview · ${endpointName || endpointId}`}
      subtitle="Reconstructed from the current frame — not a live connection."
      onClose={onClose}
      width={720}
    >
      <div className={styles.stage}>
        {currentImage ? (
          <img
            className={styles.img}
            src={thumbUrl(currentImage)}
            alt=""
            decoding="async"
            loading="eager"
            style={{ objectFit: s.image_fit === 'cover' ? 'cover' : 'contain' }}
          />
        ) : (
          <div className={styles.empty}>No image on screen</div>
        )}

        {showClock && (
          <div className={cn(styles.overlay, posClass(s.clock_position), theme)}>
            {s.show_time && <div className={styles.time}>{timeStr}</div>}
            {s.show_date && <div className={styles.date}>{dateStr}</div>}
          </div>
        )}

        {s.show_weather && weatherQ.data && (
          <div className={cn(styles.overlay, posClass(s.weather_position), theme)}>
            <span className={styles.weatherRow}>
              <span className={styles.temp}>
                {weatherQ.data.temperature != null ? Math.round(weatherQ.data.temperature) : '—'}
                {weatherQ.data.temperature_unit || '°'}
              </span>
              {weatherQ.data.condition && (
                <span className={styles.cond}>{weatherQ.data.condition.replace(/-/g, ' ')}</span>
              )}
            </span>
          </div>
        )}

        {s.show_caption && captionText && (
          <div className={cn(styles.overlay, posClass(s.caption_position), theme)}>{captionText}</div>
        )}
      </div>

      <div className={styles.meta}>
        <span>Fit: {s.image_fit}</span>
        <span>Theme: {s.overlay_theme}</span>
        {currentImage && <span title={currentImage}>{currentImage.split('/').pop()}</span>}
      </div>
    </Modal>
  )
}
