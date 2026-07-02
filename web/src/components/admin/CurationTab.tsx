import { useQuery } from '@tanstack/react-query'
import { fetchCuration, thumbUrl } from '@/api/browse'
import { useCuration } from '@/hooks/useCuration'
import { EmptyState, Loading, IconButton, HeartIcon, EyeOffIcon, LoadError } from '@/components/ui'
import styles from '@/pages/Admin.module.css'

export function CurationTab() {
  const { data, isLoading, isError, refetch } = useQuery({ queryKey: ['curation'], queryFn: fetchCuration })
  const { toggleFavorite, toggleHidden } = useCuration()

  if (isLoading) return <Loading label="Loading curation…" />
  if (isError) return <LoadError title="Couldn’t load curation" onRetry={() => refetch()} />

  const favorites = data?.favorites ?? []
  const excluded = data?.excluded ?? []

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 'var(--s5)' }}>
      <section>
        <h3 className={styles.sectionTitleIcon}>
          <HeartIcon size={17} filled /> Favorites
          <span className={styles.tabCount}>{favorites.length}</span>
        </h3>
        {favorites.length === 0 ? (
          <EmptyState icon={<HeartIcon size={32} />} title="No favorites">
            Heart photos in Browse or from a display’s “now showing”.
          </EmptyState>
        ) : (
          <div className={styles.list}>
            {favorites.map((p) => (
              <Row key={p} path={p} action="unfavorite" onAction={() => toggleFavorite(p)} />
            ))}
          </div>
        )}
      </section>

      <section>
        <h3 className={styles.sectionTitleIcon}>
          <EyeOffIcon size={17} /> Hidden
          <span className={styles.tabCount}>{excluded.length}</span>
        </h3>
        {excluded.length === 0 ? (
          <EmptyState icon={<EyeOffIcon size={32} />} title="Nothing hidden">
            Hidden photos stay out of slideshows but remain browsable.
          </EmptyState>
        ) : (
          <div className={styles.list}>
            {excluded.map((p) => (
              <Row key={p} path={p} action="unhide" onAction={() => toggleHidden(p)} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}

function Row({ path, action, onAction }: { path: string; action: 'unfavorite' | 'unhide'; onAction: () => void }) {
  return (
    <div className={styles.item} style={{ padding: 'var(--s2)' }}>
      <img className={styles.nowThumb} src={thumbUrl(path)} alt="" loading="lazy" decoding="async" />
      <div className={styles.itemMain}>
        <div className={styles.epName}>{path.split('/').pop()}</div>
        <div className={styles.epMeta}>{path}</div>
      </div>
      <IconButton label={action === 'unfavorite' ? 'Remove favorite' : 'Unhide'} bordered onClick={onAction}>
        {action === 'unfavorite' ? <HeartIcon size={15} filled /> : <EyeOffIcon size={15} />}
      </IconButton>
    </div>
  )
}
