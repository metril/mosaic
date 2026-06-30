/** TS port of the backend `check_scope_access` rule for *display* targets
 *  (app/api/routes.py). Used to show only keys that actually authorize a chosen
 *  group/endpoint — a non-authorizing key would only ever 403.
 *
 *  Scope patterns:
 *   - "*"                         → full access
 *   - "group:<id>"                → that group at group-level / anonymous endpoints
 *   - "endpoint:<group>:<ep>"     → that specific endpoint
 *
 *  A *persistent* endpoint needs "*" or an explicit "endpoint:<group>:<ep>"
 *  scope — a "group:<id>" scope is NOT sufficient for it.
 */
export function authorizesDisplay(
  scopes: string[],
  groupId: string,
  endpointId?: string | null,
  endpointIsPersistent = true,
): boolean {
  // Empty or legacy-only scopes grant nothing.
  if (!scopes || scopes.length === 0) return false
  if (scopes.length === 1 && scopes[0] === 'images:read') return false

  if (scopes.includes('*')) return true

  if (endpointId) {
    if (scopes.includes(`endpoint:${groupId}:${endpointId}`)) return true
    if (scopes.includes(`group:${groupId}`)) {
      if (endpointId.startsWith('anon-')) return true
      // Persistent endpoints require an explicit endpoint scope.
      return !endpointIsPersistent
    }
    return false
  }

  // Group-level (no specific endpoint).
  return scopes.includes(`group:${groupId}`)
}
