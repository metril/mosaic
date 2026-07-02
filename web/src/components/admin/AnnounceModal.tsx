import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import {
  announceGroup,
  clearGroupAnnouncement,
  type AnnouncePosition,
  type AnnounceTheme,
  type AnnounceSize,
  type AnnounceColor,
} from '@/api/admin'
import { Modal, Button, Input, Select, Label, ToggleField } from '@/components/ui'
import { toast } from '@/stores/toast'
import { ApiError } from '@/api/client'
import { cn } from '@/lib/cn'
import styles from '@/pages/Admin.module.css'
import formStyles from '@/components/ui/Form.module.css'
import preview from './AnnounceModal.module.css'

const POS_CLASS: Record<AnnouncePosition, string> = {
  'top-left': preview.posTopLeft,
  'top-center': preview.posTopCenter,
  'top-right': preview.posTopRight,
  center: preview.posCenter,
  'bottom-left': preview.posBottomLeft,
  'bottom-center': preview.posBottomCenter,
  'bottom-right': preview.posBottomRight,
}
const SIZE_CLASS: Record<AnnounceSize, string> = {
  s: preview.sizeS,
  m: preview.sizeM,
  l: preview.sizeL,
  xl: preview.sizeXl,
}
const COLOR_CLASS: Record<AnnounceColor, string | undefined> = {
  default: undefined,
  blue: preview.colorBlue,
  green: preview.colorGreen,
  amber: preview.colorAmber,
  red: preview.colorRed,
  pink: preview.colorPink,
}
const THEME_CLASS: Record<AnnounceTheme, string | undefined> = {
  solid: undefined,
  bubble: preview.themeBubble,
  blur: preview.themeBlur,
  fade: preview.themeFade,
}

/** Push a temporary message onto every screen in a group. */
export function AnnounceModal({ groupId, onClose }: { groupId: string; onClose: () => void }) {
  const [text, setText] = useState('')
  const [position, setPosition] = useState<AnnouncePosition>('center')
  const [theme, setTheme] = useState<AnnounceTheme>('bubble')
  const [size, setSize] = useState<AnnounceSize>('l')
  const [color, setColor] = useState<AnnounceColor>('default')
  const [scroll, setScroll] = useState(false)
  const [duration, setDuration] = useState(15)

  const send = useMutation({
    mutationFn: () => announceGroup(groupId, { text, position, theme, size, color, scroll, duration_s: duration }),
    onSuccess: (r) => {
      toast.success(
        r.notified > 0
          ? `Sent to ${r.notified} display${r.notified === 1 ? '' : 's'}`
          : 'Sent, but no displays are connected to this group',
      )
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
      width={560}
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
          {/* Live preview — exactly how it will land on a display. */}
          <div className={preview.stage}>
            <span className={preview.stageHint}>Preview</span>
            <div
              className={cn(
                preview.msg,
                POS_CLASS[position],
                SIZE_CLASS[size],
                COLOR_CLASS[color],
                THEME_CLASS[theme],
                scroll && preview.scroll,
              )}
            >
              {text.trim() ? (
                scroll ? (
                  <span className={preview.marquee}>{text}</span>
                ) : (
                  text
                )
              ) : (
                <span className={preview.empty}>Your message…</span>
              )}
            </div>
          </div>

          <Label htmlFor="announce-text">Message</Label>
          <Input
            id="announce-text"
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="Dinner's ready!"
            maxLength={500}
            autoFocus
          />

          <div className={styles.grid2}>
            <div>
              <Label>Position</Label>
              <Select className={formStyles.controlMd} value={position} onChange={(e) => setPosition(e.target.value as AnnouncePosition)}>
                <option value="top-left">Top left</option>
                <option value="top-center">Top center</option>
                <option value="top-right">Top right</option>
                <option value="center">Center</option>
                <option value="bottom-left">Bottom left</option>
                <option value="bottom-center">Bottom center</option>
                <option value="bottom-right">Bottom right</option>
              </Select>
            </div>
            <div>
              <Label>Style</Label>
              <Select className={formStyles.controlMd} value={theme} onChange={(e) => setTheme(e.target.value as AnnounceTheme)}>
                <option value="solid">Solid</option>
                <option value="bubble">Bubble</option>
                <option value="blur">Blur</option>
                <option value="fade">Fade (text only)</option>
              </Select>
            </div>
            <div>
              <Label>Size</Label>
              <Select className={formStyles.controlMd} value={size} onChange={(e) => setSize(e.target.value as AnnounceSize)}>
                <option value="s">Small</option>
                <option value="m">Medium</option>
                <option value="l">Large</option>
                <option value="xl">Extra large</option>
              </Select>
            </div>
            <div>
              <Label>Color</Label>
              <Select className={formStyles.controlMd} value={color} onChange={(e) => setColor(e.target.value as AnnounceColor)}>
                <option value="default">Default</option>
                <option value="blue">Blue</option>
                <option value="green">Green</option>
                <option value="amber">Amber</option>
                <option value="red">Red</option>
                <option value="pink">Pink</option>
              </Select>
            </div>
          </div>

          <ToggleField checked={scroll} onChange={setScroll}>
            Scroll text (single-line ticker instead of wrapping)
          </ToggleField>

          <Label htmlFor="announce-duration">Duration (seconds)</Label>
          <Input
            id="announce-duration"
            type="number"
            className={formStyles.controlNum}
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
