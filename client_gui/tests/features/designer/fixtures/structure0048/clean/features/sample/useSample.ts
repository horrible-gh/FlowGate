import { inject, reactive, type Ref } from 'vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'

export function useSample(target: Ref<InspectorTarget>) {
  const host = inject(INSPECTOR_HOST)
  const state = reactive({ loading: false, admin: useCurrentUser() !== null })
  return { state, open: () => host?.request({ kind: 'select-node', nodeId: target.value.nodeId }) }
}
