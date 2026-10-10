import { getRequest } from '@shared/api'

export const documentRead = (docId: string) => getRequest(`/api/v1/documents/${docId}`)
