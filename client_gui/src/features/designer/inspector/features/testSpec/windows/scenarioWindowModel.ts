import { computed, h, nextTick, onBeforeUnmount, ref, render, watch } from 'vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { testSpecCasePayload, type TestSpecCaseDetail, type TestSpecCasePayload } from '@/features/document'
import type ModelessShell from '@/components/ui/ModelessShell.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { useTestCard, type TestCardWindowKind } from '../useTestCard'

export const caseTextFields = ['title', 'requirement', 'precondition', 'procedure', 'expected', 'automation_ref'] as const
export interface CaseDraft {
  docId: string | null
  original: TestSpecCasePayload
  snapshot: string
  title: string; requirement: string; precondition: string; procedure: string; expected: string; automation_ref: string
  execution_mode: string; required: boolean
}
export function scenarioSnapshot(cases: readonly TestSpecCaseDetail[], title: string | null): string {
  return JSON.stringify([title, cases.map(testSpecCasePayload)])
}
export function caseDraft(item: TestSpecCaseDetail, cases: readonly TestSpecCaseDetail[], title: string | null, docId: string | null = null): CaseDraft {
  const original = testSpecCasePayload(item)
  return { original, docId, snapshot: scenarioSnapshot(cases, title),
    title: original.title, requirement: original.requirement ?? '', precondition: original.precondition ?? '',
    procedure: original.procedure ?? '', expected: original.expected ?? '', automation_ref: original.automation_ref ?? '',
    execution_mode: original.execution_mode ?? '', required: original.required }
}
/** Only displayed, changed fields are replaced; hidden fields and untouched nulls survive. */
export function mergeCase(cases: readonly TestSpecCaseDetail[], draft: CaseDraft): TestSpecCasePayload[] {
  return cases.map((item) => {
    const payload = testSpecCasePayload(item)
    if (item.caseId !== draft.original.case_id) return payload
    const edits: Partial<Record<typeof caseTextFields[number], string>> = {}
    for (const field of caseTextFields) {
      if (field === 'automation_ref' && draft.execution_mode !== 'automated') continue
      if (draft[field] !== (draft.original[field] ?? '')) edits[field] = draft[field]
    }
    return { ...payload, ...edits,
      execution_mode: draft.execution_mode !== (draft.original.execution_mode ?? '') ? draft.execution_mode : payload.execution_mode,
      required: draft.required !== draft.original.required ? draft.required : payload.required }
  })
}

/** Shell lifecycle remains in ModelessShell; only its persisted state belongs to the card. */
export function useScenarioWindow(target: () => InspectorTarget, kind: TestCardWindowKind) {
  const card = useTestCard(target)
  const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
  const state = computed(() => card.value?.windows[kind])
  let alive = true
  const owner = card.value
  const current = () => alive && card.value === owner && owner?.target.value !== null
  function remember() {
    const geometry = shell.value?.geometry()
    if (geometry && state.value) Object.assign(state.value.geometry, geometry)
  }
  function close() { remember(); card.value?.closeWindow(kind) }
  watch(() => state.value?.zIndex, async () => { await nextTick(); shell.value?.bringToFront() }, { immediate: true })
  onBeforeUnmount(() => { remember(); alive = false })
  const geometry = (name: string, fallback: number) => {
    const value = state.value?.geometry[name] ?? fallback
    if (name === 'width') return Math.min(value, Math.max(240, window.innerWidth - 32))
    if (name === 'height') return Math.min(value, Math.max(160, window.innerHeight - 72))
    if (name === 'x') return Math.min(value, Math.max(0, window.innerWidth - 320))
    if (name === 'y') return Math.min(value, Math.max(0, window.innerHeight - 200))
    return value
  }
  return { card, shell, state, current, close, geometry }
}

export interface RawDraft { docId: string; baseline: string; text: string; loaded: boolean; open: boolean }
export function sessionDraft<T>(store: Record<string, unknown>, key: string, create: () => T): T {
  if (!(key in store)) store[key] = create()
  return store[key] as T
}

/** A save closes/unmounts its window; the transient toast must outlive that window. */
export function notifySaved(message: string, tone: ToastHostItem['tone'] = 'success') {
  const container = document.createElement('div')
  document.body.append(container)
  const dismiss = () => { render(null, container); container.remove() }
  render(h(ToastHost, { toasts: [{ id: 1, message, tone }], onDismiss: dismiss }), container)
}
