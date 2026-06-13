import { useQuery } from '@tanstack/react-query'
import { fetchMe } from '@/api/auth'

/** Current user. A 401 inside the API client triggers a login redirect, so this
 *  resolves to a user whenever the app is actually rendering. */
export function useAuth() {
  const { data, isLoading } = useQuery({
    queryKey: ['auth', 'me'],
    queryFn: fetchMe,
    staleTime: 5 * 60_000,
    retry: false,
  })
  return { user: data ?? null, isLoading }
}
