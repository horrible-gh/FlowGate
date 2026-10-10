import type { InspectorTarget } from '../../inspectorTarget'
import { sampleReason } from './sampleReasonModel'

export function sampleAvailability(target: InspectorTarget) {
  const reason = sampleReason(target)
  return { visible: true, enabled: reason === null, reason }
}
