// The file of an APPROVED attachment request. Once a request is decided its staged copy is gone (the server answers
// 410 on /attachment-requests/{id}/preview and /content), so an approved row reads the attachment it became on the
// target document instead: `finalDocId` + `finalFilename` through the document attachment API (read / download, both
// already there, read permission only). A rejected / expired / failed request has no file at all.
import { downloadBlobRequest, getRequest } from '@shared/api'
import type { AttachmentRequestPreview } from '@/features/document/api/attachmentRequests'

const url = (docId: string, name: string) => `/api/v1/documents/${encodeURIComponent(docId)}/attachments/${encodeURIComponent(name)}`

/**
 * The attachment key the Flow attachment window uses for a file (server `flow.key`: url-safe base64 of
 * "doc_id\nfilename", no padding). The request row puts it on the file name element so the later "go to this file
 * in the attachment window" link only has to read it.
 */
export function attachmentFileKey(docId: string, filename: string): string {
  const bytes = new TextEncoder().encode(`${docId}\n${filename}`)
  let binary = ''
  for (const byte of bytes) binary += String.fromCharCode(byte)
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

/** The registered file's bytes (download, or an inline image). */
export async function registeredFileContent(docId: string, name: string): Promise<Blob> {
  return (await downloadBlobRequest(url(docId, name))).data
}

const record = (value: unknown): Record<string, unknown> =>
  value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : {}

/**
 * The registered file as the row's preview reads it. `hint` is the request's own `preview` kind: an image is shown from
 * its bytes (the row fetches them), so no text read is made for it. A file the server will not read inline (over its
 * read ceiling, 413) is `binary`: the row offers the download instead.
 */
export async function registeredFilePreview(docId: string, name: string, hint: 'text' | 'image' | null): Promise<AttachmentRequestPreview> {
  if (hint === 'image') return { kind: 'image', content: null, truncated: false, mediaType: null }
  try {
    const response = await getRequest<unknown>(`${url(docId, name)}/read?mode=auto`)
    const body = record(response.data)
    const data = record(body.data ?? body)
    if (data.kind === 'text' && typeof data.content === 'string') {
      return { kind: 'text', content: data.content, truncated: data.truncated === true, mediaType: null }
    }
    return { kind: 'binary', content: null, truncated: false, mediaType: null }
  } catch (error) {
    if ((error as { response?: { status?: number } })?.response?.status === 413) {
      return { kind: 'binary', content: null, truncated: false, mediaType: null }
    }
    throw error
  }
}
