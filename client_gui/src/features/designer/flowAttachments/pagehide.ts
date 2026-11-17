import { getBaseUrl } from '@shared/api'
import type { FlowFile } from './api'

/** Use the same configured base as axios; preserve the scheme while normalizing path slashes. */
export function pagehideDeleteUrl(flowId: string, file: FlowFile): string {
  const url = new URL(getBaseUrl(), window.location.origin)
  url.pathname = `${url.pathname}/api/v1/flows/${encodeURIComponent(flowId)}/attachments/${encodeURIComponent(file.key)}`.replace(/\/{2,}/g, '/')
  url.search = `?expected_sha256=${encodeURIComponent(file.content_sha256)}`
  url.hash = ''
  return url.href
}

/** Starts fetch before returning; no refresh, body, Content-Type or sequential await. */
export function deleteOnPagehide(flowId: string, file: FlowFile, token: string): Promise<void> {
  return fetch(pagehideDeleteUrl(flowId, file), {
    method: 'DELETE', keepalive: true, headers: { Authorization: `Bearer ${token}` },
  }).then(async response => {
    if (response.ok) return
    const data = await response.json().catch(() => null)
    // Match the manager's existing error reader. In particular 404 is a failure, not implicit success.
    throw { response: { status: response.status, data } }
  })
}

/** The app manager owns installation/disposal and calls this only once. */
export function installPagehide(flush: () => void, restore: () => void): () => void {
  const show = (event: PageTransitionEvent) => { if (event.persisted) restore() }
  window.addEventListener('pagehide', flush)
  window.addEventListener('pageshow', show)
  return () => {
    window.removeEventListener('pagehide', flush)
    window.removeEventListener('pageshow', show)
  }
}
