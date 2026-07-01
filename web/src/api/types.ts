/* API response types. Hand-written from the FastAPI/Pydantic models — the
   surface is stable and this avoids a codegen step. Expanded per phase. */

export interface AuthUser {
  sub: string
  email: string
  name: string
}

export interface FileNode {
  type: 'file'
  name: string
  path: string
  size: number
  size_formatted: string
  dimensions: string
  width: number | null
  height: number | null
  format?: string | null
  mode?: string | null
  modified: string | null
  error?: string
}

export interface FolderNode {
  type: 'folder'
  name: string
  path: string
  children: TreeNode[]
  /** Recursive count — propagated up from children by the server. */
  file_count: number
  folder_count: number
  stats?: unknown
  error?: string
}

export type TreeNode = FileNode | FolderNode

/** Shape of GET /browse?format=json and GET /api/browse/folders — the root
 *  folder is under `tree`. The folders endpoint omits file children. */
export interface BrowseTree {
  root: string
  total_folders: number
  total_files: number
  tree: FolderNode
}

/** A page of GET /api/browse/files. */
export interface FilesPage {
  total: number
  offset: number
  files: FileNode[]
}

export interface CurationState {
  favorites: string[]
  excluded: string[]
}

/* ============================ Display groups =============================== */

export type Position = 'bottom-left' | 'bottom-right' | 'top-left' | 'top-right'

export interface WebhookConfig {
  url: string
  trigger: string
  enabled?: boolean
  [k: string]: unknown
}

export interface DisplayGroupSettings {
  // Playback
  duration: number
  auto_cycle: boolean
  folders: string | null
  recursive: boolean
  // Image processing
  width: number | null
  height: number | null
  preserve_aspect: boolean
  mode: 'fit' | 'crop'
  pad_color: string
  format: string | null
  // E-Ink
  eink: boolean
  saturation: number
  contrast: number
  brightness: number
  sharpen: boolean
  // Clock
  show_time: boolean
  show_date: boolean
  time_format: '12' | '24'
  date_format: string
  clock_source: 'client' | 'server' | 'homeassistant'
  clock_position: Position
  overlay_theme: 'fade' | 'solid' | 'bubble' | 'blur'
  // Weather
  show_weather: boolean
  weather_position: Position
  weather_ha_entity: string | null
  weather_show_forecast: boolean
  weather_forecast_type: 'daily' | 'hourly' | 'twice_daily'
  weather_forecast_days: number
  weather_show_high_low: boolean
  weather_show_precipitation: boolean
  // Appearance
  layout: 'single' | 'splitview' | 'splitview-landscape'
  background_blur: boolean
  background_blur_amount: number
  image_fit: 'contain' | 'cover'
  transition_effect: 'none' | 'fade' | 'crossfade' | 'slide' | 'dissolve'
  transition_duration_ms: number
  ken_burns: boolean
  ken_burns_intensity: number
  ken_burns_duration: number
  // Caption
  show_caption: boolean
  caption_fields: string[]
  caption_position: Position
  // Night mode
  night_mode: boolean
  night_mode_source: 'fixed' | 'homeassistant'
  night_sleep_time: string
  night_wake_time: string
  night_ha_sun_entity: string
  night_action: 'blank' | 'dim'
  night_dim_opacity: number
  // Burn-in
  burn_in_protection: boolean
  burn_in_interval: number
  burn_in_duration: number
  burn_in_opacity: number
  burn_in_shift_enabled: boolean
  burn_in_shift_amount: number
  burn_in_shift_interval: number
  // Webhooks
  webhooks: WebhookConfig[]
}

export interface DisplayEndpoint {
  endpoint_id: string
  name: string | null
  is_persistent: boolean
  current_image: string | null
  history_count: number
  forward_count: number
  image_history: string[]
  forward_history: string[]
  created_at: string | null
  last_seen: string | null
  settings_override: Partial<DisplayGroupSettings>
}

export interface BrowserState {
  browser_id: string
  endpoint_id: string
  status: string
  current_image: string | null
  connected_at: string
  /** Health telemetry (admin health view). */
  last_heartbeat?: string
  last_load_ms?: number | null
  last_cycle_ms?: number | null
  [k: string]: unknown
}

/** GET /api/display/health — per-endpoint status for dashboards / external monitors. */
export interface DisplayHealthEndpoint {
  endpoint_id: string
  name: string | null
  status: 'online' | 'idle' | 'stale' | 'offline'
  current_image: string | null
  heartbeat_age_s: number | null
  last_load_ms: number | null
  last_cycle_ms: number | null
  uptime_s: number | null
  last_seen: string | null
}
export interface DisplayHealth {
  displays: number
  online: number
  offline: number
  worst_load_ms: number | null
  groups: {
    group_id: string
    image_count: number
    browser_count: number
    paused: boolean
    night_paused: boolean
    error: string | null
    endpoints: DisplayHealthEndpoint[]
  }[]
}

/** Server-built caption for the admin display preview. Keys are a subset of
 *  filename / date_taken / folder / location (per the group's caption_fields). */
export interface EndpointCaption {
  endpoint_id: string
  current_image: string | null
  caption: Record<string, string> | null
}

export interface DisplayGroup {
  group_id: string
  error: string | null
  image_pool_error: string | null
  image_count: number
  browser_count: number
  current_cycle: number
  paused: boolean
  night_paused: boolean
  created_at: string
  next_switch_at: string | null
  settings: DisplayGroupSettings
  browsers: BrowserState[]
  endpoints: DisplayEndpoint[]
}

/** Full in-memory image history for one endpoint (newest first). `forward` holds
 *  images shown after the current one (preserved when you jump back). */
export interface EndpointHistory {
  endpoint_id: string
  current_image: string | null
  history: string[]
  forward: string[]
  history_count: number
  forward_count: number
}

/** Per-endpoint override whitelist (must mirror ENDPOINT_OVERRIDE_KEYS). */
export const ENDPOINT_OVERRIDE_KEYS = [
  'layout',
  'image_fit',
  'clock_position',
  'overlay_theme',
  'transition_effect',
  'transition_duration_ms',
  'ken_burns',
  'ken_burns_intensity',
  'ken_burns_duration',
  'show_caption',
  'caption_fields',
  'caption_position',
  'weather_position',
] as const

/* ============================== Immich ==================================== */

export interface ImmichPull {
  name: string
  server_url: string
  target_folder: string
  count: number
  album_ids: string[] | null
  album_filter_mode: 'all' | 'any'
  person_ids: string[] | null
  person_filter_mode: 'all' | 'any'
  is_favorite: boolean | null
  taken_after: string | null
  taken_before: string | null
  media_type: 'IMAGE' | 'VIDEO'
  replace_existing: boolean
  cron_expression: string | null
  cron_timezone: string | null
  protect_favorites: boolean
  skip_excluded: boolean
  last_run: string | null
  // server-augmented
  schedule_description?: string | null
  next_runs?: string[]
  has_api_key?: boolean
}

export interface PullResult {
  pull_name: string
  status: 'idle' | 'running' | 'completed' | 'failed' | 'cancelled'
  phase: 'idle' | 'searching' | 'downloading' | 'completed' | 'cancelled' | 'failed'
  total: number
  downloaded: number
  search_collected: number
  skipped: number
  errors: string[]
  started_at: string | null
  completed_at: string | null
}

export interface ImmichAlbum {
  id: string
  name: string
  asset_count: number
}
export interface ImmichPerson {
  id: string
  name: string
  thumbnail_path: string | null
}
export interface ImmichTestResult {
  success: boolean
  server_version?: string
  album_count?: number
  error?: string
}

/* ============================== API keys ================================== */

export interface ApiKey {
  id: string
  name: string
  key_prefix: string
  created_at: string
  last_used: string | null
  scopes: string[]
  enabled: boolean
  /** Whether the full secret can be recovered (built a display URL). False for
   *  keys migrated from the legacy hash-only store. */
  recoverable: boolean
}

export interface AvailableResources {
  groups: { group_id: string; endpoints: string[]; folders: string[] }[]
  folders: string[]
}

/* ============================== System =================================== */

export interface Metrics {
  uptime_seconds: number
  counters: Record<string, number>
  gauges: Record<string, number>
}
export interface Health {
  status: string
  image_count: number
  image_directory: string
}

/* ============================== Settings ================================= */

export interface AppSettings {
  ha_url: string
  has_ha_token: boolean
  ha_enabled: boolean
  ha_http_timeout: number
  ha_weather_cache_ttl: number
  immich_http_timeout: number
  max_immich_pull_count: number
  preload_lead_time: number
  browser_stale_timeout: number
  cleanup_interval: number
  endpoint_cleanup: string
  max_browsers_per_group: number
  max_groups: number
  base_url: string
}

export interface HaEntity {
  entity_id: string
  friendly_name: string
}
