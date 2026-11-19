// flowgate.v02test.0029 T0005 — what the screen knows about message images apart from the requests (those are in
// api.ts): the limits, which mirror the server constants (message_image_service.py), and how a refusal is told to the
// user. One place, so the composer, the shrink step and the notices read the same values.
import { ConversationRequestError } from './api'

export const MAX_IMAGE_BYTES = 5 * 1024 * 1024
export const MAX_IMAGE_DIMENSION = 1568
export const MAX_IMAGES_PER_MESSAGE = 5
export const ACCEPTED_IMAGE_TYPES: readonly string[] = ['image/png', 'image/jpeg', 'image/gif', 'image/webp']

/** Why an image was refused, as the short key the composer shows (`gui.conversation.image.error.<key>`). */
export type ImageErrorKey = 'too_large' | 'unsupported' | 'unreadable' | 'dimension' | 'forbidden' | 'disposed' | 'failed'

export function imageErrorKey(error: unknown): ImageErrorKey {
  const status = error instanceof ConversationRequestError ? error.status : null
  const code = error instanceof ConversationRequestError ? error.code : null
  if (code === 'message_image_too_large' || status === 413) return 'too_large'
  if (code === 'message_image_unsupported_type' || status === 415) return 'unsupported'
  if (code === 'message_image_too_large_dimension' || code === 'message_image_invalid_dimensions') return 'dimension'
  if (code === 'message_image_flow_disposed' || status === 409) return 'disposed'
  if (status === 403) return 'forbidden'
  return 'failed'
}
