import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAdminWs, GROUPS_KEY } from '@/hooks/useAdminWs'
import { listGroups } from '@/api/admin'
import { listPulls } from '@/api/immich'
import { listKeys } from '@/api/keys'
import { cn } from '@/lib/cn'
import { GridIcon, CloudIcon, HeartIcon, KeyIcon, ServerIcon, SettingsIcon, ActivityIcon } from '@/components/ui'
import { GroupsTab } from '@/components/admin/GroupsTab'
import { ImmichTab } from '@/components/admin/ImmichTab'
import { CurationTab } from '@/components/admin/CurationTab'
import { KeysTab } from '@/components/admin/KeysTab'
import { SystemTab } from '@/components/admin/SystemTab'
import { SettingsTab } from '@/components/admin/SettingsTab'
import { HealthTab } from '@/components/admin/HealthTab'
import styles from './Admin.module.css'

type TabId = 'displays' | 'immich' | 'curation' | 'keys' | 'health' | 'settings' | 'system'

export default function Admin() {
  const wsStatus = useAdminWs()
  const [tab, setTab] = useState<TabId>('displays')

  // light count queries for the tab badges (cheap, cached)
  const groups = useQuery({ queryKey: GROUPS_KEY, queryFn: listGroups })
  const pulls = useQuery({ queryKey: ['pulls'], queryFn: listPulls })
  const keys = useQuery({ queryKey: ['keys'], queryFn: listKeys })

  const tabs: { id: TabId; label: string; icon: React.ReactNode; count?: number }[] = [
    { id: 'displays', label: 'Displays', icon: <GridIcon size={16} />, count: groups.data?.length },
    { id: 'immich', label: 'Immich', icon: <CloudIcon size={16} />, count: pulls.data?.length },
    { id: 'curation', label: 'Curation', icon: <HeartIcon size={16} /> },
    { id: 'keys', label: 'API keys', icon: <KeyIcon size={16} />, count: keys.data?.length },
    { id: 'health', label: 'Health', icon: <ActivityIcon size={16} /> },
    { id: 'settings', label: 'Settings', icon: <SettingsIcon size={16} /> },
    { id: 'system', label: 'System', icon: <ServerIcon size={16} /> },
  ]

  return (
    <div className={`container ${styles.wrap}`}>
      <div className={styles.head}>
        <h1 className={styles.title}>Admin</h1>
        <span className={styles.wsDot}>
          <span
            className={cn(
              styles.wsLed,
              wsStatus === 'connected' && styles.wsLedOn,
              wsStatus === 'disconnected' && styles.wsLedOff,
            )}
          />
          {wsStatus === 'connected' ? 'Live' : wsStatus === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
        </span>
      </div>

      <div className={styles.tabs} role="tablist">
        {tabs.map((t) => (
          <button
            key={t.id}
            role="tab"
            aria-selected={tab === t.id}
            className={cn(styles.tab, tab === t.id && styles.tabActive)}
            onClick={() => setTab(t.id)}
          >
            {t.icon}
            {t.label}
            {t.count !== undefined && <span className={styles.tabCount}>{t.count}</span>}
          </button>
        ))}
      </div>

      {tab === 'displays' && <GroupsTab />}
      {tab === 'immich' && <ImmichTab />}
      {tab === 'curation' && <CurationTab />}
      {tab === 'keys' && <KeysTab />}
      {tab === 'health' && <HealthTab />}
      {tab === 'settings' && <SettingsTab />}
      {tab === 'system' && <SystemTab />}
    </div>
  )
}
