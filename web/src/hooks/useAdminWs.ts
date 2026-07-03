import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { DisplayGroup } from '@/api/types'
import { listGroups } from '@/api/admin'

export const GROUPS_KEY = ['groups']

/** Read the groups list. The admin WS ([useAdminWs]) is the live source of
 *  truth for this cache entry, so REST only bootstraps an empty cache (or a
 *  mutation-triggered invalidation) — staleTime: Infinity stops every tab
 *  mount from re-fetching what the socket already keeps fresh. */
export function useGroups() {
  return useQuery({ queryKey: GROUPS_KEY, queryFn: listGroups, staleTime: Infinity })
}

/* Narrow selectors on the same ['groups'] cache. TanStack re-renders a component
 * only when its SELECTED value changes (structural sharing), so a single group's
 * update no longer re-renders every groups consumer — it re-renders only the one
 * card whose object actually changed. `upsert` preserves unchanged group object
 * references, which is what makes `useGroup(id)` isolate per card. */

/** Just the group count — for badges. Re-renders only on add/remove. */
export function useGroupCount() {
  return useQuery({
    queryKey: GROUPS_KEY,
    queryFn: listGroups,
    staleTime: Infinity,
    select: (groups) => groups.length,
  }).data
}

/** The list of group ids — for the list container. Re-renders only when the set
 *  of ids changes (add/remove/reorder), not when a group's live state ticks. */
export function useGroupIds() {
  return useQuery({
    queryKey: GROUPS_KEY,
    queryFn: listGroups,
    staleTime: Infinity,
    select: (groups) => groups.map((g) => g.group_id),
  })
}

/** One group by id. Re-renders only when THAT group's object changes. */
export function useGroup(groupId: string) {
  return useQuery({
    queryKey: GROUPS_KEY,
    queryFn: listGroups,
    staleTime: Infinity,
    select: (groups) => groups.find((g) => g.group_id === groupId) ?? null,
  }).data
}

type WsStatus = 'connecting' | 'connected' | 'reconnecting' | 'disconnected'

function wsUrl() {
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${window.location.host}/ws/admin`
}

/** Subscribe to /ws/admin and keep the ['groups'] query cache live. Components
 *  read groups from the cache (single source of truth); this hook mutates it on
 *  INITIAL_STATE / GROUP_CREATED / GROUP_UPDATED / GROUP_DELETED and answers
 *  PING with PONG. Reconnects with capped exponential backoff. */
export function useAdminWs(): WsStatus {
  const qc = useQueryClient()
  const [status, setStatus] = useState<WsStatus>('connecting')
  const wsRef = useRef<WebSocket | null>(null)
  const retryRef = useRef(0)
  const closedRef = useRef(false)

  useEffect(() => {
    closedRef.current = false

    function upsert(group: DisplayGroup) {
      qc.setQueryData<DisplayGroup[]>(GROUPS_KEY, (prev) => {
        const list = prev ?? []
        const i = list.findIndex((g) => g.group_id === group.group_id)
        if (i === -1) return [...list, group]
        const copy = list.slice()
        copy[i] = group
        return copy
      })
    }

    function connect() {
      if (closedRef.current) return
      setStatus(retryRef.current === 0 ? 'connecting' : 'reconnecting')
      const ws = new WebSocket(wsUrl())
      wsRef.current = ws

      ws.onopen = () => {
        retryRef.current = 0
        setStatus('connected')
      }

      ws.onmessage = (ev) => {
        let msg: any
        try {
          msg = JSON.parse(ev.data)
        } catch {
          return
        }
        switch (msg.type) {
          case 'INITIAL_STATE':
            qc.setQueryData(GROUPS_KEY, msg.groups as DisplayGroup[])
            break
          case 'GROUP_CREATED':
          case 'GROUP_UPDATED':
            upsert(msg.group as DisplayGroup)
            break
          case 'GROUP_DELETED':
            qc.setQueryData<DisplayGroup[]>(GROUPS_KEY, (prev) =>
              (prev ?? []).filter((g) => g.group_id !== msg.group_id),
            )
            break
          case 'PING':
            ws.send(JSON.stringify({ type: 'PONG' }))
            break
        }
      }

      ws.onclose = () => {
        if (closedRef.current) return
        setStatus('disconnected')
        const delay = Math.min(30_000, 1000 * 2 ** retryRef.current)
        retryRef.current += 1
        window.setTimeout(connect, delay)
      }

      ws.onerror = () => ws.close()
    }

    connect()

    return () => {
      closedRef.current = true
      wsRef.current?.close()
    }
  }, [qc])

  return status
}
