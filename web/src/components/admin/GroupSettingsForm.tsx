import type { DisplayGroupSettings } from '@/api/types'
import {
  FormSection,
  SubHead,
  Row,
  ToggleRow,
  SelectRow,
  NumberRow,
  TextRow,
  ChipsRow,
  Input,
} from '@/components/ui'
import { POSITION_OPTS, CAPTION_FIELD_OPTS } from '@/lib/settingsDefaults'
import { FolderCheckTree } from './FolderCheckTree'
import { HaEntityRow } from './HaEntityRow'
import styles from '@/pages/Admin.module.css'

function parseFolders(s: string | null): Set<string> {
  return new Set((s ?? '').split(',').map((x) => x.trim()).filter(Boolean))
}

interface Props {
  value: DisplayGroupSettings
  onChange: (next: DisplayGroupSettings) => void
}

export function GroupSettingsForm({ value, onChange }: Props) {
  function set<K extends keyof DisplayGroupSettings>(key: K, v: DisplayGroupSettings[K]) {
    onChange({ ...value, [key]: v })
  }

  const selectedFolders = parseFolders(value.folders)
  function toggleFolder(path: string) {
    const next = new Set(selectedFolders)
    next.has(path) ? next.delete(path) : next.add(path)
    // Empty selection clears to the whole library. Use '' (not null) so a PATCH
    // actually applies it — the update endpoint drops null fields as "no change".
    set('folders', next.size ? [...next].join(',') : '')
  }

  return (
    <>
      {/* 1. Playback ------------------------------------------------------- */}
      <FormSection title="Playback" defaultOpen>
        <NumberRow
          label="Duration"
          hint="Seconds each image is shown"
          value={value.duration}
          onChange={(v) => set('duration', v ?? 30)}
          min={5}
          max={3600}
          unit="sec"
        />
        <ToggleRow
          label="Auto-cycle"
          hint="Off = images change only on manual request"
          value={value.auto_cycle}
          onChange={(v) => set('auto_cycle', v)}
        />
        <Row
          label="Folders"
          hint={
            selectedFolders.size
              ? `${selectedFolders.size} folder${selectedFolders.size === 1 ? '' : 's'} selected`
              : 'None selected = whole library'
          }
          stack
        >
          <FolderCheckTree selected={selectedFolders} onToggle={toggleFolder} />
          <details className={styles.foldersText}>
            <summary>Edit as text</summary>
            <Input
              value={value.folders ?? ''}
              onChange={(e) => set('folders', e.target.value)}
              placeholder="earth, space/nebulae"
            />
          </details>
        </Row>
        <ToggleRow
          label="Recursive"
          hint="Include images in subfolders"
          value={value.recursive}
          onChange={(v) => set('recursive', v)}
        />
      </FormSection>

      {/* 2. Appearance ---------------------------------------------------- */}
      <FormSection title="Appearance">
        <SelectRow
          label="Layout"
          value={value.layout}
          onChange={(v) => set('layout', v)}
          options={[
            { value: 'single', label: 'Single' },
            { value: 'splitview', label: 'Split view' },
            { value: 'splitview-landscape', label: 'Split (landscape)' },
          ]}
        />
        <SelectRow
          label="Image fit"
          value={value.image_fit}
          onChange={(v) => set('image_fit', v)}
          options={[
            { value: 'contain', label: 'Contain' },
            { value: 'cover', label: 'Cover' },
          ]}
        />
        <ToggleRow
          label="Background blur"
          hint="Blurred fill behind letterboxed images"
          value={value.background_blur}
          onChange={(v) => set('background_blur', v)}
        />
        {value.background_blur && (
          <NumberRow
            label="Blur amount"
            value={value.background_blur_amount}
            onChange={(v) => set('background_blur_amount', v ?? 50)}
            min={0}
            max={100}
            unit="%"
          />
        )}
        <SubHead>Transition</SubHead>
        <SelectRow
          label="Effect"
          value={value.transition_effect}
          onChange={(v) => set('transition_effect', v)}
          options={[
            { value: 'none', label: 'None' },
            { value: 'fade', label: 'Fade' },
            { value: 'crossfade', label: 'Crossfade' },
            { value: 'slide', label: 'Slide' },
            { value: 'dissolve', label: 'Dissolve' },
          ]}
        />
        {value.transition_effect !== 'none' && (
          <NumberRow
            label="Duration"
            value={value.transition_duration_ms}
            onChange={(v) => set('transition_duration_ms', v ?? 500)}
            min={100}
            max={3000}
            step={50}
            unit="ms"
          />
        )}
        <SubHead>Ken Burns</SubHead>
        <ToggleRow
          label="Ken Burns pan/zoom"
          value={value.ken_burns}
          onChange={(v) => set('ken_burns', v)}
        />
        {value.ken_burns && (
          <>
            <NumberRow
              label="Intensity"
              value={value.ken_burns_intensity}
              onChange={(v) => set('ken_burns_intensity', v ?? 15)}
              min={0}
              max={50}
              unit="%"
            />
            <NumberRow
              label="Duration"
              value={value.ken_burns_duration}
              onChange={(v) => set('ken_burns_duration', v ?? 20)}
              min={5}
              max={120}
              unit="sec"
            />
          </>
        )}
      </FormSection>

      {/* 3. Image processing --------------------------------------------- */}
      <FormSection title="Image processing">
        <NumberRow
          label="Width"
          hint="Blank = source width"
          value={value.width}
          onChange={(v) => set('width', v)}
          min={1}
          unit="px"
          placeholder="auto"
        />
        <NumberRow
          label="Height"
          value={value.height}
          onChange={(v) => set('height', v)}
          min={1}
          unit="px"
          placeholder="auto"
        />
        <SelectRow
          label="Resize mode"
          value={value.mode}
          onChange={(v) => set('mode', v)}
          options={[
            { value: 'fit', label: 'Fit (letterbox)' },
            { value: 'crop', label: 'Crop (fill)' },
          ]}
        />
        <TextRow
          label="Pad color"
          hint="Letterbox color (name / hex / rgb)"
          value={value.pad_color}
          onChange={(v) => set('pad_color', v)}
          placeholder="black"
        />
        <SelectRow
          label="Format"
          value={value.format ?? ''}
          onChange={(v) => set('format', v || null)}
          options={[
            { value: '', label: 'Original' },
            { value: 'jpeg', label: 'JPEG' },
            { value: 'png', label: 'PNG' },
            { value: 'webp', label: 'WebP' },
            { value: 'bmp', label: 'BMP' },
            { value: 'gif', label: 'GIF' },
          ]}
        />
        <ToggleRow
          label="Preserve aspect"
          value={value.preserve_aspect}
          onChange={(v) => set('preserve_aspect', v)}
        />
        <SubHead>E-Ink</SubHead>
        <ToggleRow
          label="E-Ink color mapping"
          hint="Spectra-6 dithering for E-Ink displays"
          value={value.eink}
          onChange={(v) => set('eink', v)}
        />
        {value.eink && (
          <>
            <NumberRow
              label="Saturation"
              value={value.saturation}
              onChange={(v) => set('saturation', v ?? 2.5)}
              min={0}
              max={10}
              step={0.1}
            />
            <NumberRow
              label="Contrast"
              value={value.contrast}
              onChange={(v) => set('contrast', v ?? 1.2)}
              min={0}
              max={5}
              step={0.1}
            />
            <NumberRow
              label="Brightness"
              value={value.brightness}
              onChange={(v) => set('brightness', v ?? 1.1)}
              min={0}
              max={5}
              step={0.1}
            />
            <ToggleRow label="Sharpen" value={value.sharpen} onChange={(v) => set('sharpen', v)} />
          </>
        )}
      </FormSection>

      {/* 4. Overlays ------------------------------------------------------ */}
      <FormSection title="Overlays">
        <SelectRow
          label="Overlay theme"
          value={value.overlay_theme}
          onChange={(v) => set('overlay_theme', v)}
          options={[
            { value: 'fade', label: 'Fade' },
            { value: 'solid', label: 'Solid' },
            { value: 'bubble', label: 'Bubble' },
            { value: 'blur', label: 'Blur' },
          ]}
        />
        <SubHead>Clock</SubHead>
        <ToggleRow label="Show time" value={value.show_time} onChange={(v) => set('show_time', v)} />
        <ToggleRow label="Show date" value={value.show_date} onChange={(v) => set('show_date', v)} />
        {(value.show_time || value.show_date) && (
          <>
            <SelectRow
              label="Time format"
              value={value.time_format}
              onChange={(v) => set('time_format', v)}
              options={[
                { value: '24', label: '24-hour' },
                { value: '12', label: '12-hour' },
              ]}
            />
            <TextRow
              label="Date format"
              hint="Immich Kiosk tokens"
              value={value.date_format}
              onChange={(v) => set('date_format', v)}
            />
            <SelectRow
              label="Clock source"
              value={value.clock_source}
              onChange={(v) => set('clock_source', v)}
              options={[
                { value: 'client', label: 'Client' },
                { value: 'server', label: 'Server' },
                { value: 'homeassistant', label: 'Home Assistant' },
              ]}
            />
            <SelectRow
              label="Position"
              value={value.clock_position}
              onChange={(v) => set('clock_position', v)}
              options={[...POSITION_OPTS]}
            />
          </>
        )}
        <SubHead>Weather</SubHead>
        <ToggleRow
          label="Show weather"
          value={value.show_weather}
          onChange={(v) => set('show_weather', v)}
        />
        {value.show_weather && (
          <>
            <HaEntityRow
              label="HA entity"
              domain="weather"
              value={value.weather_ha_entity ?? ''}
              onChange={(v) => set('weather_ha_entity', v || null)}
              placeholder="weather.home"
              allowEmpty
            />
            <SelectRow
              label="Position"
              value={value.weather_position}
              onChange={(v) => set('weather_position', v)}
              options={[...POSITION_OPTS]}
            />
            <ToggleRow
              label="Show forecast"
              value={value.weather_show_forecast}
              onChange={(v) => set('weather_show_forecast', v)}
            />
            {value.weather_show_forecast && (
              <>
                <SelectRow
                  label="Forecast type"
                  value={value.weather_forecast_type}
                  onChange={(v) => set('weather_forecast_type', v)}
                  options={[
                    { value: 'daily', label: 'Daily' },
                    { value: 'hourly', label: 'Hourly' },
                    { value: 'twice_daily', label: 'Twice daily' },
                  ]}
                />
                <NumberRow
                  label="Periods"
                  value={value.weather_forecast_days}
                  onChange={(v) => set('weather_forecast_days', v ?? 3)}
                  min={1}
                  max={24}
                />
                <ToggleRow
                  label="High / low"
                  value={value.weather_show_high_low}
                  onChange={(v) => set('weather_show_high_low', v)}
                />
                <ToggleRow
                  label="Precipitation"
                  value={value.weather_show_precipitation}
                  onChange={(v) => set('weather_show_precipitation', v)}
                />
              </>
            )}
          </>
        )}
        <SubHead>Caption</SubHead>
        <ToggleRow
          label="Show caption"
          value={value.show_caption}
          onChange={(v) => set('show_caption', v)}
        />
        {value.show_caption && (
          <>
            <ChipsRow
              label="Fields"
              value={value.caption_fields as string[]}
              onChange={(v) => set('caption_fields', v)}
              options={[...CAPTION_FIELD_OPTS]}
            />
            <SelectRow
              label="Position"
              value={value.caption_position}
              onChange={(v) => set('caption_position', v)}
              options={[...POSITION_OPTS]}
            />
          </>
        )}
      </FormSection>

      {/* 5. Schedule & burn-in ------------------------------------------- */}
      <FormSection title="Schedule & burn-in">
        <SubHead>Night mode</SubHead>
        <ToggleRow
          label="Night mode"
          hint="Blank or dim the display overnight"
          value={value.night_mode}
          onChange={(v) => set('night_mode', v)}
        />
        {value.night_mode && (
          <>
            <SelectRow
              label="Source"
              value={value.night_mode_source}
              onChange={(v) => set('night_mode_source', v)}
              options={[
                { value: 'fixed', label: 'Fixed times' },
                { value: 'homeassistant', label: 'HA sun entity' },
              ]}
            />
            {value.night_mode_source === 'fixed' ? (
              <>
                <TextRow
                  label="Sleep time"
                  value={value.night_sleep_time}
                  onChange={(v) => set('night_sleep_time', v)}
                  placeholder="23:00"
                />
                <TextRow
                  label="Wake time"
                  value={value.night_wake_time}
                  onChange={(v) => set('night_wake_time', v)}
                  placeholder="07:00"
                />
              </>
            ) : (
              <HaEntityRow
                label="Sun entity"
                domain="sun"
                value={value.night_ha_sun_entity}
                onChange={(v) => set('night_ha_sun_entity', v)}
                placeholder="sun.sun"
              />
            )}
            <SelectRow
              label="Action"
              value={value.night_action}
              onChange={(v) => set('night_action', v)}
              options={[
                { value: 'blank', label: 'Blank screen' },
                { value: 'dim', label: 'Dim' },
              ]}
            />
            {value.night_action === 'dim' && (
              <NumberRow
                label="Dim opacity"
                value={value.night_dim_opacity}
                onChange={(v) => set('night_dim_opacity', v ?? 20)}
                min={0}
                max={90}
                unit="%"
              />
            )}
          </>
        )}
        <SubHead>Burn-in prevention</SubHead>
        <ToggleRow
          label="Burn-in protection"
          hint="Periodic dimming + pixel shift for OLED"
          value={value.burn_in_protection}
          onChange={(v) => set('burn_in_protection', v)}
        />
        {value.burn_in_protection && (
          <>
            <NumberRow
              label="Dim interval"
              value={value.burn_in_interval}
              onChange={(v) => set('burn_in_interval', v ?? 5)}
              min={1}
              max={60}
              unit="min"
            />
            <NumberRow
              label="Dim duration"
              value={value.burn_in_duration}
              onChange={(v) => set('burn_in_duration', v ?? 30)}
              min={5}
              max={120}
              unit="sec"
            />
            <NumberRow
              label="Dim opacity"
              value={value.burn_in_opacity}
              onChange={(v) => set('burn_in_opacity', v ?? 70)}
              min={10}
              max={90}
              unit="%"
            />
            <ToggleRow
              label="Pixel shift"
              value={value.burn_in_shift_enabled}
              onChange={(v) => set('burn_in_shift_enabled', v)}
            />
            {value.burn_in_shift_enabled && (
              <>
                <NumberRow
                  label="Shift amount"
                  value={value.burn_in_shift_amount}
                  onChange={(v) => set('burn_in_shift_amount', v ?? 5)}
                  min={1}
                  max={20}
                  unit="px"
                />
                <NumberRow
                  label="Shift interval"
                  value={value.burn_in_shift_interval}
                  onChange={(v) => set('burn_in_shift_interval', v ?? 60)}
                  min={10}
                  max={300}
                  unit="sec"
                />
              </>
            )}
          </>
        )}
      </FormSection>
    </>
  )
}
