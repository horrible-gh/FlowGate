import { getRequest } from '@shared/api'
import { documentErrorOf } from '../documentError'

export async function documentRead(docId: string) {
  try {
    return (await getRequest(`/api/v1/documents/${docId}`)).data
  } catch (error) {
    throw documentErrorOf(error)
  }
}
