/** Write text to the clipboard, reporting whether it actually landed —
 *  clipboard access can reject (permissions, insecure context), and callers
 *  must not toast "copied" when it didn't. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // fall through
  }
  return false
}
