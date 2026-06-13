import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card } from '@/components/ui'
import { ImageIcon, GridIcon, SettingsIcon, ChevronRightIcon } from '@/components/ui'
import styles from './Home.module.css'

const ROTATE_MS = 7000

/** Live preview that rotates random images from the library via the existing
 *  `/?api=random-image` endpoint. Cross-fades between two layers. */
function LivePreview() {
  const [layers, setLayers] = useState<{ a: string; b: string; showA: boolean }>({
    a: '',
    b: '',
    showA: true,
  })
  const [failed, setFailed] = useState(false)
  const timer = useRef<number>()

  function nextUrl() {
    // cache-bust so each request returns a fresh random image
    return `/?api=random-image&width=1100&t=${Date.now()}`
  }

  useEffect(() => {
    // preload then swap so we never show a half-loaded image
    function load() {
      const url = nextUrl()
      const img = new Image()
      img.onload = () => {
        setFailed(false)
        setLayers((prev) =>
          prev.showA ? { ...prev, b: url, showA: false } : { ...prev, a: url, showA: true },
        )
      }
      img.onerror = () => setFailed(true)
      img.src = url
    }
    load()
    timer.current = window.setInterval(load, ROTATE_MS)
    return () => window.clearInterval(timer.current)
  }, [])

  if (failed && !layers.a && !layers.b) {
    return (
      <div className={styles.frame}>
        <div className={styles.frameEmpty}>
          <ImageIcon size={28} />
          <span>No images in the library yet</span>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.frame}>
      {layers.a && (
        <img
          className={`${styles.photo} ${layers.showA ? styles.photoShown : ''}`}
          src={layers.a}
          alt=""
        />
      )}
      {layers.b && (
        <img
          className={`${styles.photo} ${!layers.showA ? styles.photoShown : ''}`}
          src={layers.b}
          alt=""
        />
      )}
      <span className={styles.frameLabel}>
        <span className={styles.pulse} />
        Live preview
      </span>
    </div>
  )
}

export default function Home() {
  return (
    <div className={`container ${styles.wrap}`}>
      <section className={`${styles.hero} reveal`}>
        <div className={styles.lede} style={{ ['--i' as string]: 0 }}>
          <span className="eyebrow">Self-hosted photo displays</span>
          <h1 className={styles.title}>
            Your photos, on <em>every</em> screen in the house.
          </h1>
          <p className={styles.sub}>
            Curate a library, pull from Immich, and drive synchronized slideshows on TVs,
            tablets, and E-Ink frames — all from one place.
          </p>
          <div className={styles.actions}>
            <Link to="/browse">
              <Button variant="primary" size="lg">
                Browse the library
                <ChevronRightIcon size={16} />
              </Button>
            </Link>
            <a href="/admin">
              <Button size="lg">Manage displays</Button>
            </a>
          </div>
        </div>

        <div style={{ ['--i' as string]: 1 }}>
          <LivePreview />
        </div>
      </section>

      <section className={`${styles.cards} reveal`}>
        <Card hover className={styles.card} style={{ ['--i' as string]: 2 }}>
          <span className={styles.cardIcon}>
            <ImageIcon size={20} />
          </span>
          <span className={styles.cardTitle}>Browse &amp; curate</span>
          <span className={styles.cardBody}>
            Explore folders, favorite the keepers, and hide what shouldn’t appear in
            slideshows.
          </span>
          <Link to="/browse" className={styles.cardLink}>
            Open library <ChevronRightIcon size={14} />
          </Link>
        </Card>

        <Card hover className={styles.card} style={{ ['--i' as string]: 3 }}>
          <span className={styles.cardIcon}>
            <GridIcon size={20} />
          </span>
          <span className={styles.cardTitle}>Display groups</span>
          <span className={styles.cardBody}>
            Group screens, sync their cycling, and fine-tune transitions, overlays, and night
            mode per display.
          </span>
          <a href="/admin" className={styles.cardLink}>
            Open admin <ChevronRightIcon size={14} />
          </a>
        </Card>

        <Card hover className={styles.card} style={{ ['--i' as string]: 4 }}>
          <span className={styles.cardIcon}>
            <SettingsIcon size={20} />
          </span>
          <span className={styles.cardTitle}>Immich &amp; schedules</span>
          <span className={styles.cardBody}>
            Pull albums and people from Immich on a cron schedule, with curation-aware
            backfill.
          </span>
          <a href="/admin" className={styles.cardLink}>
            Configure pulls <ChevronRightIcon size={14} />
          </a>
        </Card>
      </section>
    </div>
  )
}
