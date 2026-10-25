import { computed, h, nextTick, onBeforeUnmount, ref, render, watch } from 'vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { testSpecCasePayload, type TestSpecCaseDetail, type TestSpecCasePayload } from '@/features/document'
import type ModelessShell from '@/components/ui/ModelessShell.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { useTestCard, type TestCardWindowKind } from '../useTestCard'

export const caseTextFields = ['title', 'requirement', 'precondition', 'procedure', 'expected', 'automation_ref', 'run_command', 'run_cwd'] as const
/** The automated Case's execution settings: target, command and working directory. */
const executionFields = new Set<string>(['automation_ref', 'run_command', 'run_cwd'])
export interface CaseDraft {
  docId: string | null
  original: TestSpecCasePayload
  snapshot: string
  title: string; requirement: string; precondition: string; procedure: string; expected: string; automation_ref: string
  run_command: string; run_cwd: string
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
    run_command: original.run_command ?? '', run_cwd: original.run_cwd ?? '',
    execution_mode: original.execution_mode ?? '', required: original.required }
}
export function newCaseDraft(cases: readonly TestSpecCaseDetail[], title: string | null, docId: string | null): CaseDraft {
  const ids = new Set(cases.map(c => c.caseId.replace(/-0+(\d)/, '-$1')))
  let n = 1
  while (ids.has(`TC-${n}`)) n++
  const original: TestSpecCasePayload = { case_id: `TC-${n}`, title: '', category: 'normal', requirement: '',
    execution_mode: 'automated', required: false, precondition: '', input: '', procedure: '', expected: '',
    check_points: '', automation_ref: null, test_assets: null }
  return { original, docId, snapshot: scenarioSnapshot(cases, title), title: '', requirement: '', precondition: '',
    procedure: '', expected: '', automation_ref: '', run_command: '', run_cwd: '', execution_mode: 'automated', required: false }
}
export function addedCase(cases: readonly TestSpecCaseDetail[], draft: CaseDraft): TestSpecCasePayload[] {
  return [...cases.map(testSpecCasePayload), { ...draft.original, title: draft.title, requirement: draft.requirement,
    precondition: draft.precondition, procedure: draft.procedure, expected: draft.expected, check_points: draft.expected,
    execution_mode: draft.execution_mode, required: draft.required,
    automation_ref: draft.execution_mode === 'automated' ? draft.automation_ref || null : null,
    ...(draft.execution_mode === 'automated' && draft.run_command ? { run_command: draft.run_command } : {}),
    ...(draft.execution_mode === 'automated' && draft.run_cwd ? { run_cwd: draft.run_cwd } : {}) }]
}

/**
 * Only displayed, changed fields are replaced; hidden fields and untouched nulls survive. The two hidden
 * fields the server requires get the same defaults as a new case only when the edited case has none.
 */
export function mergeCase(cases: readonly TestSpecCaseDetail[], draft: CaseDraft): TestSpecCasePayload[] {
  return cases.map((item) => {
    const payload = testSpecCasePayload(item)
    if (item.caseId !== draft.original.case_id) return payload
    const edits: Partial<Record<typeof caseTextFields[number], string>> = {}
    for (const field of caseTextFields) {
      if (executionFields.has(field) && draft.execution_mode !== 'automated') continue
      if (draft[field] !== (draft.original[field] ?? '')) edits[field] = draft[field]
    }
    return { ...payload, ...edits, category: payload.category ?? 'normal', check_points: payload.check_points ?? draft.expected,
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
