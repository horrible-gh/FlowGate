import { normalizeApiError } from '@shared/apiErrors'

export const documentErrorOf = (error: unknown) => normalizeApiError(error).status
