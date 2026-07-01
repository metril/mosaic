import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  announceGroup,
  clearGroupAnnouncement,
  type AnnouncePosition,
  type AnnounceTheme,
} from '@/api/admin'
import { Modal, Button, Input, Select, Label } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import styles from '@/pages/Admin.module.css'

/** Push a temporary message onto every screen in a group. */
export function AnnounceModal({ groupId, onClose }: { groupId: string; onClose: () => void }) {
  const [text, setText] = useState('')
  const [position, setPosition] = useState<AnnouncePosition>('bottom-left')
  const [theme, setTheme] = useState<AnnounceTheme>('solid')
  const [duration, setDuration] = useState(15)

  const send = useMutation({
    mutationFn: () => announceGroup(groupId, { text, position, theme, duration_s: duration }),
    onSuccess: (r) => {
      toast.success(r.notified > 0 ? `Sent to ${r.notified} display${r.notified === 1 ? '' : 's'}` : 'Sent (no displays connected)')
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Announce failed'),
  })

  const clear = useMutation({
    mutationFn: () => clearGroupAnnouncement(groupId),
    onSuccess: () => {
      toast.info('Announcement cleared')
      onClose()
    },
    onError: (e) => toast.error(e instanceof ApiError ? e.detail : 'Clear failed'),
  })

  return (
    <Modal
      title={`Announce · ${groupId}`}
      subtitle="Show a temporary message on every screen in this group."
      onClose={onClose}
      width={520}
      footer={
        <>
          <Button variant="ghost" onClick={() => clear.mutate()} loading={clear.isPending}>
            Clear current
          </Button>
          <Button variant="primary" disabled={!text.trim()} loading={send.isPending} onClick={() => send.mutate()}>
            Send announcement
          </Button>
        </>
      }
    >
      <fieldset disabled={send.isPending} className="fieldsetReset">
        <div className={styles.fieldGroup}>
          <Label htmlFor="announce-text">Message</Label>
          <Input
            id="announce-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Dinner's ready!"
            maxLength={280}
            autoFocus
          />

          <div className={styles.grid2}>
            <div>
              <Label>Position</Label>
              <Select value={position} onChange={(e) => setPosition(e.target.value as AnnouncePosition)}>
                <option value="bottom-left">Bottom left</option>
                <option value="bottom-right">Bottom right</option>
                <option value="top-left">Top left</option>
                <option value="top-right">Top right</option>
              </Select>
            </div>
            <div>
              <Label>Style</Label>
              <Select value={theme} onChange={(e) => setTheme(e.target.value as AnnounceTheme)}>
                <option value="solid">Solid</option>
                <option value="bubble">Bubble</option>
                <option value="blur">Blur</option>
                <option value="fade">Fade (text only)</option>
              </Select>
            </div>
          </div>

          <Label htmlFor="announce-duration">Duration (seconds)</Label>
          <Input
            id="announce-duration"
            type="number"
            min={1}
            max={3600}
            value={duration}
            onChange={(e) => setDuration(Math.max(1, Number(e.target.value) || 1))}
          />
        </div>
      </fieldset>
    </Modal>
  )
}
