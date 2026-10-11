// flowgate.v02.0053 T#7 — the 테스트 card's 실행 block reads the Case counts of the card's TS (its test view,
// the read the test section uses) and reads them again when a test run or the TS moves.
import { computed, type ComputedRef } from 'vue'
import { testSpecRead, type TestSpecView } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource } from '../../useInspectorResource'
import { testRunSettingsSpecDocId, testRunSettingsView, type TestRunSettingsView } from './testRunSettingsModel'

/** Server events after which the TS's Cases are read again. */
export const TEST_RUN_SETTINGS_EVENTS = ['doc_review_status_changed', 'document_explorer_refresh', 'group_view_refresh', 'test_run_finished'] as const

export function useTestRunSettings(target: () => InspectorTarget | null): { readonly view: ComputedRef<TestRunSettingsView> } {
  const spec = useInspectorResource<TestSpecView>({
    target: () => {
      const current = target()
      const docId = current ? testRunSettingsSpecDocId(current) : null
      return current && docId ? { projectId: current.projectId, flowId: current.flowId, docId } : null
    },
    read: testSpecRead,
    events: TEST_RUN_SETTINGS_EVENTS,
  })
  return { view: computed(() => testRunSettingsView(spec.data.value)) }
}
