import { api } from './client'
import type { AuthUser } from './types'

/** Current authenticated user, or null when OIDC is disabled / dev mode
 *  returns a session-less response. Throws (→ login redirect) on 401. */
export function fetchMe() {
  return api.get<AuthUser>('/auth/me')
}
