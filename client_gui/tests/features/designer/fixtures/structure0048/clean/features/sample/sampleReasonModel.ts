import type { InspectorTarget } from '../../inspectorTarget'

export function sampleReason(target: InspectorTarget): string | null {
  if (target.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (target.readOnly === 'revision') return 'reason.read_only_revision'
  return null
}
