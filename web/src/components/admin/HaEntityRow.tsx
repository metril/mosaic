import { useQuery } from '@tanstack/react-query'
import { fetchHaEntities } from '@/api/system'
import { Row, TextRow, Select, Input } from '@/components/ui'
import styles from '@/pages/Admin.module.css'

interface Props {
  label: string
  hint?: string
  /** HA domain to list, e.g. "weather" or "sun". */
  domain: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  /** Allow an empty selection (e.g. weather entity is optional). */
  allowEmpty?: boolean
}

/** A Home Assistant entity field: a dropdown of live entities (fetched by
 *  domain) with a text-input fallback when HA isn't configured or returns
 *  nothing. Mirrors the folder picker's "edit as text" escape. */
export function HaEntityRow({ label, hint, domain, value, onChange, placeholder, allowEmpty }: Props) {
  const { data, isError } = useQuery({
    queryKey: ['ha-entities', domain],
    queryFn: () => fetchHaEntities(domain),
    retry: false,
    staleTime: 60_000,
  })

  const entities = data ?? []
  // Fallback to free text when HA isn't configured / has no such entities.
  if (isError || entities.length === 0) {
    return (
      <TextRow label={label} hint={hint ?? 'e.g. weather.home'} value={value} onChange={onChange} placeholder={placeholder} />
    )
  }

  // Keep the current value selectable even if it's not in the live list.
  const hasCurrent = !value || entities.some((e) => e.entity_id === value)

  return (
    <Row label={label} hint={hint} stack>
      <Select value={value} onChange={(e) => onChange(e.target.value)}>
        {allowEmpty && <option value="">(none)</option>}
        {!hasCurrent && <option value={value}>{value} (custom)</option>}
        {entities.map((e) => (
          <option key={e.entity_id} value={e.entity_id}>
            {e.friendly_name === e.entity_id ? e.entity_id : `${e.friendly_name} · ${e.entity_id}`}
          </option>
        ))}
      </Select>
      <details className={styles.foldersText}>
        <summary>Edit as text</summary>
        <Input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
      </details>
    </Row>
  )
}
