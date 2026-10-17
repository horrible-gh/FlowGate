// flowgate.v02.0064 T#4 rework (P0006 §3.3) — where a succeeded root came from, for the 「전환 전 문서」 header link:
// the plain document read (kindSwitchOriginRead, no switch judgement) on every ownership. A document's origin never
// changes after the succession that made it, so no server event reads it again.
import { computed, type ComputedRef } from 'vue'
import { kindSwitchOriginRead, type KindSwitchConvertedFrom } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource } from '../../useInspectorResource'

/** The document the target came from by a succession, or null (not read yet, none, or the read failed). */
export function useKindSwitchOrigin(target: () => InspectorTarget | null): ComputedRef<KindSwitchConvertedFrom | null> {
  const origin = useInspectorResource({ target, read: kindSwitchOriginRead, events: [] })
  return computed(() => origin.data.value ?? null)
}
