/** Copy text with a legacy fallback for insecure contexts and denied Clipboard API calls. */
export async function copyToClipboard(text: string, container?: HTMLElement | null): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // Permission or focus denial can still allow the legacy copy command.
  }

  const area = document.createElement('textarea')
  area.value = text
  area.readOnly = true
  area.tabIndex = -1
  area.setAttribute('aria-hidden', 'true')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  const host = container ?? document.body
  const previousFocus = document.activeElement
  try {
    host.appendChild(area)
    area.focus()
    area.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    area.remove()
    if (previousFocus instanceof HTMLElement && previousFocus.isConnected) {
      try { previousFocus.focus() } catch { /* Focus return is best effort. */ }
    }
  }
}
