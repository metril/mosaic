import type { SVGProps } from 'react'

/* A small set of consistent 1.6px line icons. `size` controls width/height;
   color inherits from `currentColor`. Heart can render filled. */

type Props = SVGProps<SVGSVGElement> & { size?: number }

function Svg({ size = 18, children, ...rest }: Props & { children: React.ReactNode }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...rest}
    >
      {children}
    </svg>
  )
}

export const HeartIcon = ({ filled, ...p }: Props & { filled?: boolean }) => (
  <Svg {...p} fill={filled ? 'currentColor' : 'none'}>
    <path d="M19.5 12.6 12 20l-7.5-7.4A4.7 4.7 0 0 1 12 6a4.7 4.7 0 0 1 7.5 6.6Z" />
  </Svg>
)

export const EyeOffIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M10.6 6.2A9.6 9.6 0 0 1 12 6c5 0 9 4.5 9 6-.3 1-1.4 2.5-3 3.7M6 7.5C3.9 8.8 3 10.6 3 12c0 1.5 4 6 9 6 1.2 0 2.3-.2 3.3-.6M3 3l18 18M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </Svg>
)

export const SunIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Svg>
)

export const MoonIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </Svg>
)

export const SearchIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.2-3.2" />
  </Svg>
)

export const ChevronRightIcon = (p: Props) => (
  <Svg {...p}>
    <path d="m9 6 6 6-6 6" />
  </Svg>
)

export const FolderIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
  </Svg>
)

export const ImageIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <circle cx="8.5" cy="9.5" r="1.5" />
    <path d="m21 16-5-5L5 20" />
  </Svg>
)

export const XIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Svg>
)

export const ArrowLeftIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M19 12H5M11 18l-6-6 6-6" />
  </Svg>
)

export const ArrowRightIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Svg>
)

export const SlidersIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 6h10M18 6h2M4 12h2M10 12h10M4 18h7M15 18h5" />
    <circle cx="16" cy="6" r="2" />
    <circle cx="8" cy="12" r="2" />
    <circle cx="13" cy="18" r="2" />
  </Svg>
)

export const LogOutIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Svg>
)

export const SparkleIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8Z" />
  </Svg>
)

export const GridIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3" y="3" width="7" height="7" rx="1.5" />
    <rect x="14" y="3" width="7" height="7" rx="1.5" />
    <rect x="3" y="14" width="7" height="7" rx="1.5" />
    <rect x="14" y="14" width="7" height="7" rx="1.5" />
  </Svg>
)

export const SettingsIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="12" cy="12" r="3" />
    <path d="M19.4 13.5a7.6 7.6 0 0 0 0-3l1.7-1.3-1.7-3-2 .8a7.6 7.6 0 0 0-2.6-1.5L14.5 3h-3l-.3 2a7.6 7.6 0 0 0-2.6 1.5l-2-.8-1.7 3L4.6 9a7.6 7.6 0 0 0 0 3l-1.7 1.3 1.7 3 2-.8a7.6 7.6 0 0 0 2.6 1.5l.3 2h3l.3-2a7.6 7.6 0 0 0 2.6-1.5l2 .8 1.7-3Z" />
  </Svg>
)

export const PlayIcon = (p: Props) => (
  <Svg {...p} fill="currentColor" stroke="none">
    <path d="M7 5.5v13a1 1 0 0 0 1.5.87l11-6.5a1 1 0 0 0 0-1.74l-11-6.5A1 1 0 0 0 7 5.5Z" />
  </Svg>
)

export const PauseIcon = (p: Props) => (
  <Svg {...p} fill="currentColor" stroke="none">
    <rect x="6" y="5" width="4" height="14" rx="1" />
    <rect x="14" y="5" width="4" height="14" rx="1" />
  </Svg>
)

export const SkipBackIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M16 6 9 12l7 6V6Z" fill="currentColor" />
    <path d="M8 6v12" />
  </Svg>
)

export const SkipForwardIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M8 6l7 6-7 6V6Z" fill="currentColor" />
    <path d="M16 6v12" />
  </Svg>
)

export const PrevIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M15 6l-6 6 6 6V6Z" fill="currentColor" />
  </Svg>
)

export const NextIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M9 6l6 6-6 6V6Z" fill="currentColor" />
  </Svg>
)

export const RefreshIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M21 12a9 9 0 1 1-2.6-6.4M21 4v5h-5" />
  </Svg>
)

export const PlusIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 5v14M5 12h14" />
  </Svg>
)

export const TrashIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13M10 11v6M14 11v6" />
  </Svg>
)

export const CopyIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15V5a2 2 0 0 1 2-2h8" />
  </Svg>
)

export const EditIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
  </Svg>
)

export const ExternalIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3" />
  </Svg>
)

export const CheckIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M5 12l5 5L20 7" />
  </Svg>
)

export const KeyIcon = (p: Props) => (
  <Svg {...p}>
    <circle cx="7.5" cy="15.5" r="4.5" />
    <path d="m10.5 12.5 8-8M16 5l3 3M14 7l2 2" />
  </Svg>
)

export const CloudIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M17.5 19a4.5 4.5 0 0 0 .3-9 6 6 0 0 0-11.5 1.5A3.75 3.75 0 0 0 6.5 19h11Z" />
  </Svg>
)

export const ServerIcon = (p: Props) => (
  <Svg {...p}>
    <rect x="3" y="4" width="18" height="7" rx="2" />
    <rect x="3" y="13" width="18" height="7" rx="2" />
    <path d="M7 7.5h.01M7 16.5h.01" />
  </Svg>
)

export const HistoryIcon = (p: Props) => (
  <Svg {...p}>
    <path d="M3 3v5h5" />
    <path d="M3.05 13A9 9 0 1 0 6 5.3L3 8" />
    <path d="M12 7v5l3.5 2" />
  </Svg>
)
