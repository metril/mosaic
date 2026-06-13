import { useThemeStore } from '@/stores/theme'
import { IconButton } from '@/components/ui'
import { SunIcon, MoonIcon } from '@/components/ui'

export function ThemeToggle() {
  const theme = useThemeStore((s) => s.theme)
  const toggle = useThemeStore((s) => s.toggle)
  const isDark = theme === 'dark'
  return (
    <IconButton
      label={isDark ? 'Switch to light theme' : 'Switch to dark theme'}
      onClick={toggle}
    >
      {isDark ? <MoonIcon size={18} /> : <SunIcon size={18} />}
    </IconButton>
  )
}
