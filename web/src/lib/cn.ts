/** Join truthy class names. Tiny replacement for clsx. */
export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}
