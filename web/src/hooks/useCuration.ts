import { useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchCuration, setFavorite, setExcluded } from '@/api/browse'
import type { CurationState } from '@/api/types'
import { toast } from '@/stores/toast'

const KEY = ['curation']

/** Shared favorites/hidden state with optimistic toggles. Any component using
 *  this hook stays in sync via the single TanStack Query cache entry. */
export function useCuration() {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: KEY, queryFn: fetchCuration, staleTime: 60_000 })

  const favSet = useMemo(() => new Set(data?.favorites ?? []), [data])
  const hiddenSet = useMemo(() => new Set(data?.excluded ?? []), [data])

  function patch(updater: (s: CurationState) => CurationState) {
    qc.setQueryData<CurationState>(KEY, (prev) =>
      updater(prev ?? { favorites: [], excluded: [] }),
    )
  }

  // Reconcile the optimistic cache with the server once the last in-flight
  // toggle settles (skipping while others are pending avoids clobbering
  // rapid-fire toggles with a mid-flight refetch).
  function reconcile() {
    if (qc.isMutating() <= 1) qc.invalidateQueries({ queryKey: KEY })
  }

  const favMutation = useMutation({
    mutationFn: ({ path, value }: { path: string; value: boolean }) => setFavorite(path, value),
    onError: (_e, vars) => {
      // rollback
      patch((s) => ({
        ...s,
        favorites: vars.value
          ? s.favorites.filter((p) => p !== vars.path)
          : [...s.favorites, vars.path],
      }))
      toast.error('Could not update favorite')
    },
    onSettled: reconcile,
  })

  const hideMutation = useMutation({
    mutationFn: ({ path, value }: { path: string; value: boolean }) => setExcluded(path, value),
    onSuccess: (_r, vars) => {
      toast.info(vars.value ? 'Hidden from slideshows' : 'Visible in slideshows again')
    },
    onError: (_e, vars) => {
      patch((s) => ({
        ...s,
        excluded: vars.value
          ? s.excluded.filter((p) => p !== vars.path)
          : [...s.excluded, vars.path],
      }))
      toast.error('Could not update visibility')
    },
    onSettled: reconcile,
  })

  function toggleFavorite(path: string) {
    const value = !favSet.has(path)
    patch((s) => ({
      ...s,
      favorites: value ? [...s.favorites, path] : s.favorites.filter((p) => p !== path),
    }))
    favMutation.mutate({ path, value })
  }

  function toggleHidden(path: string) {
    const value = !hiddenSet.has(path)
    patch((s) => ({
      ...s,
      excluded: value ? [...s.excluded, path] : s.excluded.filter((p) => p !== path),
    }))
    hideMutation.mutate({ path, value })
  }

  return { favSet, hiddenSet, toggleFavorite, toggleHidden }
}
