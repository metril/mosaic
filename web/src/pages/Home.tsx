import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { listGroups } from '@/api/admin'
import { listPulls } from '@/api/immich'
import { useAdminWs, GROUPS_KEY } from '@/hooks/useAdminWs'
import { fetchFolders, thumbUrl } from '@/api/browse'
import type { DisplayGroup, ImmichPull } from '@/api/types'
import { cn } from '@/lib/cn'
import {
  GridIcon,
  ServerIcon,
  ImageIcon,
  CloudIcon,
  ExternalIcon,
  ChevronRightIcon,
  SettingsIcon,
} from '@/components/ui'
import styles from './Home.module.css'

/** A shared 1s ticker so countdowns stay live without one timer per row. */
function useNow(intervalMs = 1000) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs)
    return () => window.clearInterval(id)
  }, [intervalMs])
  return now
}

function relative(ts: string | null, now: number): string {
  if (!ts) return 'never'
  const t = new Date(ts).getTime()
  if (Number.isNaN(t)) return '—'
  const diff = Math.round((now - t) / 1000)
  const ago = diff >= 0
  const s = Math.abs(diff)
  const out =
    s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`
  return ago ? `${out} ago` : `in ${out}`
}

function nowPlaying(g: DisplayGroup): string | null {
  return (
    g.endpoints.find((e) => e.current_image)?.current_image ??
    g.browsers.find((b) => b.current_image)?.current_image ??
    null
  )
}

type Status = { label: string; tone: 'live' | 'paused' | 'night' | 'idle' }
function groupStatus(g: DisplayGroup): Status {
  if (g.paused) return { label: 'Paused', tone: 'paused' }
  if (g.night_paused) return { label: 'Night mode', tone: 'night' }
  if (g.browser_count > 0) return { label: 'Live', tone: 'live' }
  return { label: 'No displays', tone: 'idle' }
}

function StatCard({
  icon,
  value,
  label,
  i,
}: {
  icon: React.ReactNode
  value: React.ReactNode
  label: string
  i: number
}) {
  return (
    <div className={styles.stat} style={{ ['--i' as string]: i }}>
      <span className={styles.statIcon}>{icon}</span>
      <span className={styles.statValue}>{value}</span>
      <span className={styles.statLabel}>{label}</span>
    </div>
  )
}

function GroupRow({ g, now }: { g: DisplayGroup; now: number }) {
  const status = groupStatus(g)
  const playing = nowPlaying(g)
  return (
    <Link to="/admin" className={styles.groupRow}>
      <span className={styles.thumb}>
        {playing ? (
          <img src={thumbUrl(playing)} alt="" loading="lazy" decoding="async" />
        ) : (
          <ImageIcon size={18} />
        )}
      </span>
      <span className={styles.groupMain}>
        <span className={styles.groupName}>{g.group_id}</span>
        <span className={styles.groupMeta}>
          {playing ? playing.split('/').pop() : 'Nothing playing'}
        </span>
      </span>
      <span className={styles.groupRight}>
        <span className={cn(styles.badge, styles[status.tone])}>
          <span className={styles.dot} />
          {status.label}
        </span>
        <span className={styles.groupSub}>
          {g.browser_count} {g.browser_count === 1 ? 'display' : 'displays'}
          {(() => {
            // Only show the countdown when the next switch is genuinely upcoming;
            // a stale past timestamp (idle group) would read "next … ago".
            if (g.paused || !g.next_switch_at) return ''
            const t = new Date(g.next_switch_at).getTime()
            return t > now ? ` · next ${relative(g.next_switch_at, now)}` : ''
          })()}
        </span>
      </span>
      <ChevronRightIcon size={16} className={styles.chevron} />
    </Link>
  )
}

function PullRow({ p, now }: { p: ImmichPull; now: number }) {
  return (
    <div className={styles.pullRow}>
      <span className={styles.pullIcon}>
        <CloudIcon size={16} />
      </span>
      <span className={styles.groupMain}>
        <span className={styles.groupName}>{p.name}</span>
        <span className={styles.groupMeta}>
          {p.schedule_description || 'Manual'} · → {p.target_folder}
        </span>
      </span>
      <span className={styles.pullTime}>{relative(p.last_run, now)}</span>
    </div>
  )
}

export default function Home() {
  useAdminWs() // keeps the groups cache live
  const now = useNow()

  const { data: groups } = useQuery({ queryKey: GROUPS_KEY, queryFn: listGroups })
  const { data: pulls } = useQuery({ queryKey: ['pulls'], queryFn: listPulls })
  // Shares the ['folders'] cache with Browse; total_files is the true recursive
  // count (unlike /health, which only counts the root folder), and the
  // folders-only scan never opens images so it's fast.
  const { data: tree } = useQuery({ queryKey: ['folders'], queryFn: fetchFolders })

  const groupList = groups ?? []
  const displaysOnline = groupList.reduce((n, g) => n + g.browser_count, 0)
  const lastPull = (pulls ?? [])
    .map((p) => p.last_run)
    .filter(Boolean)
    .sort()
    .pop() as string | undefined

  return (
    <div className={`container ${styles.wrap}`}>
      <header className={styles.head}>
        <h1 className={styles.title}>Overview</h1>
        <p className={styles.sub}>Live status of your displays and library.</p>
      </header>

      <section className={cn(styles.stats, 'reveal')}>
        <StatCard icon={<GridIcon size={18} />} value={groupList.length} label="Display groups" i={0} />
        <StatCard
          icon={<ServerIcon size={18} />}
          value={displaysOnline}
          label={displaysOnline === 1 ? 'Display online' : 'Displays online'}
          i={1}
        />
        <StatCard
          icon={<ImageIcon size={18} />}
          value={(tree?.total_files ?? 0).toLocaleString()}
          label={`Photos · ${(tree?.total_folders ?? 0).toLocaleString()} folders`}
          i={2}
        />
        <StatCard
          icon={<CloudIcon size={18} />}
          value={lastPull ? relative(lastPull, now) : '—'}
          label="Last Immich pull"
          i={3}
        />
      </section>

      <div className={styles.cols}>
        <section className={styles.panel}>
          <div className={styles.panelHead}>
            <h2 className={styles.panelTitle}>Display groups</h2>
            <Link to="/admin" className={styles.panelLink}>
              Manage <ChevronRightIcon size={14} />
            </Link>
          </div>
          {groupList.length === 0 ? (
            <div className={styles.empty}>
              <GridIcon size={28} />
              <span>No display groups yet.</span>
              <Link to="/admin" className={styles.panelLink}>
                Create one <ChevronRightIcon size={14} />
              </Link>
            </div>
          ) : (
            <div className={styles.list}>
              {groupList.map((g) => (
                <GroupRow key={g.group_id} g={g} now={now} />
              ))}
            </div>
          )}
        </section>

        <div className={styles.side}>
          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <h2 className={styles.panelTitle}>Immich pulls</h2>
              <Link to="/admin" className={styles.panelLink}>
                Configure <ChevronRightIcon size={14} />
              </Link>
            </div>
            {(pulls ?? []).length === 0 ? (
              <div className={styles.empty}>
                <CloudIcon size={26} />
                <span>No pulls configured.</span>
              </div>
            ) : (
              <div className={styles.list}>
                {(pulls ?? []).map((p) => (
                  <PullRow key={p.name} p={p} now={now} />
                ))}
              </div>
            )}
          </section>

          <section className={styles.panel}>
            <div className={styles.panelHead}>
              <h2 className={styles.panelTitle}>Quick actions</h2>
            </div>
            <div className={styles.actions}>
              <Link to="/browse" className={styles.action}>
                <ImageIcon size={17} /> Browse library <ChevronRightIcon size={15} className={styles.actionArrow} />
              </Link>
              <Link to="/admin" className={styles.action}>
                <SettingsIcon size={17} /> Manage displays <ChevronRightIcon size={15} className={styles.actionArrow} />
              </Link>
              <a href="/display" className={styles.action}>
                <ExternalIcon size={17} /> Open kiosk <ChevronRightIcon size={15} className={styles.actionArrow} />
              </a>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
