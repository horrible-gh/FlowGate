import { postFormRequest, deleteRequest, downloadBlobRequest, postRequest, getRequest } from '@shared/api'
import type { Origin } from './words'
export interface FlowFile {
  key: string; filename: string; display_name: string; doc_id: string; size: number
  content_sha256: string; content_type?: string; uploaded_at?: string | null
  source_type: string; source_title: string; origin?: Origin
}
const base = (flow: string) => `/api/v1/flows/${encodeURIComponent(flow)}/attachments`
export async function readFiles(flow: string): Promise<FlowFile[]> {
  return (await getRequest<{ attachments: FlowFile[] }>(base(flow))).data.attachments
}
export async function uploadFile(flow: string, file: File, choice = 'ask', existing?: { key: string; content_sha256: string }) {
  const form = new FormData()
  form.append('file', file); form.append('conflict', choice)
  if (existing) { form.append('key', existing.key); form.append('expected_sha256', existing.content_sha256) }
  return postFormRequest(base(flow), form)
}
export function deleteFile(flow: string, file: FlowFile) {
  return deleteRequest(`${base(flow)}/${encodeURIComponent(file.key)}?expected_sha256=${encodeURIComponent(file.content_sha256)}`)
}
export function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url; anchor.download = name; document.body.appendChild(anchor); anchor.click(); anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export async function downloadFile(flow: string, file: FlowFile) {
  saveBlob((await downloadBlobRequest(`${base(flow)}/${encodeURIComponent(file.key)}`)).data, file.display_name)
}
export async function downloadZip(flow: string, keys: string[]) {
  saveBlob((await postRequest<Blob>(`/api/v1/flows/${encodeURIComponent(flow)}/attachments-zip`, { keys }, { responseType: 'blob' })).data, `${flow}_attachments.zip`)
}
