import { documentRead } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource } from '../../useInspectorResource'

export function useSample(target: () => InspectorTarget) {
  return useInspectorResource({ target, read: documentRead, events: ['doc_review_status_changed'] })
}
