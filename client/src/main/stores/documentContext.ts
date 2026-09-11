import { defineStore } from 'pinia'
import { getRequest } from '@shared/api'

export interface DocumentGroupContext {
  group_id: string
  title?: string | null
  project_id?: string | null
}

// 0552 T0019 C2/C3: facts shared by document headers belong to the authenticated app
// session, not to each short-lived DocHeader instance. Successful values and overlapping
// reads are reused; failures remain retryable.
export const useDocumentContextStore = defineStore('document-context', () => {
  const ownerNames = new Map<string, string | null>()
  const ownerRequests = new Map<string, Promise<string | null>>()
  const groups = new Map<string, DocumentGroupContext>()
  const groupRequests = new Map<string, Promise<DocumentGroupContext | null>>()

  async function resolveOwnerName(ownerId: string): Promise<string | null> {
    if (ownerNames.has(ownerId)) return ownerNames.get(ownerId) ?? null
    const existing = ownerRequests.get(ownerId)
    if (existing) return existing
    const request = getRequest<any>(`/api/v1/users/${encodeURIComponent(ownerId)}`)
      .then((res) => {
        const user = (res.data as any)?.data ?? res.data
        const name = user?.username ?? user?.display_name ?? null
        ownerNames.set(ownerId, name)
        return name
      })
      .catch(() => null)
      .finally(() => ownerRequests.delete(ownerId))
    ownerRequests.set(ownerId, request)
    return request
  }

  async function resolveGroup(
    projectId: string,
    groupId: string,
    force = false,
  ): Promise<DocumentGroupContext | null> {
    if (!force && groups.has(groupId)) return groups.get(groupId) ?? null
    const existing = groupRequests.get(groupId)
    if (existing) return existing
    const request = getRequest<any>(`/api/v1/groups/${encodeURIComponent(groupId)}`)
      .then((res) => {
        const group = ((res.data as any)?.group ?? (res.data as any)?.data ?? res.data) as DocumentGroupContext
        if (!group || group.group_id !== groupId) return null
        if (group.project_id != null && group.project_id !== projectId) return null
        groups.set(groupId, group)
        return group
      })
      .catch(() => null)
      .finally(() => groupRequests.delete(groupId))
    groupRequests.set(groupId, request)
    return request
  }

  return { resolveOwnerName, resolveGroup }
})