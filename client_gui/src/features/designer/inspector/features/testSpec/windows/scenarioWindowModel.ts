import { computed, h, nextTick, onBeforeUnmount, ref, render, watch } from 'vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { testSpecCasePayload, type TestCaseCheck, type TestCaseChecker, type TestSpecCaseDetail, type TestSpecCasePayload } from '@/features/document'
import type ModelessShell from '@/components/ui/ModelessShell.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { useTestCard, type TestCardWindowKind } from '../useTestCard'
import type { CasesPin } from '../testCardSession'
import type { TsBase } from '@/features/documentWindow'

// F81 case fields: title · content · procedure · expected · check (code | manual) · run command (code only).
export const caseTextFields = ['title', 'content', 'procedure', 'expected', 'run_command'] as const
export interface CaseDraft {
  docId: string | null
  original: TestSpecCasePayload
  snapshot: string
  title: string; content: string; procedure: string; expected: string
  /** The code case's one execution input (0094 B0005); its target and cwd are not edited here. */
  run_command: string
  /** `code`: the server runs the command; `manual`: checked by hand (who did is recorded with the result). */
  check: TestCaseCheck
  /** Kept as stored for a manual case; the window does not choose it. */
  checker: TestCaseChecker | null
  /** Review fix (§2.15.3): the v2 snapshot (docId, revision, coordinates, cases) this draft saves against; set once, never replaced by a reopen / another draft's ACK / a late GET. */
  pin: CasesPin | null
  pinState: 'idle' | 'loading' | 'failed'
}
export function scenarioSnapshot(cases: readonly TestSpecCaseDetail[], title: string | null): string {
  return JSON.stringify([title, cases.map(testSpecCasePayload)])
}
/**
 * Whether the draft's pin is the very snapshot the draft was shown from (§2.15.3).
 * F81 8a: a scenario without a title of its own (the empty scenario before its first case) is shown with the
 * document's title (the test view falls back to `doc_title`), while the structured read gives no title at all.
 * That pin is the same snapshot when its cases are; any other difference stays a conflict, never a rebase.
 */
export function pinMatches(draft: Pick<CaseDraft, 'snapshot' | 'pin'>): boolean {
  const pin = draft.pin
  if (!pin) return false
  if (draft.snapshot === scenarioSnapshot(pin.cases, pin.title)) return true
  if (pin.title !== null) return false
  const shown = JSON.parse(draft.snapshot) as [string | null, unknown]
  return draft.snapshot === scenarioSnapshot(pin.cases, shown[0])
}

const LOCATOR = /^([A-Za-z0-9_./-]+)(?:::(\w+(?:::\w+)*))?$/
/** `path` relative to `from` (both repository-relative POSIX paths), as the server's posixpath.relpath. */
function relativePath(from: string, path: string): string {
  const a = from.split('/').filter(s => s && s !== '.'), b = path.split('/').filter(s => s && s !== '.')
  let i = 0
  while (i < a.length && i < b.length && a[i] === b[i]) i++
  return [...a.slice(i).map(() => '..'), ...b.slice(i)].join('/') || '.'
}
/**
 * The command the window shows. A case saved with a target and `{target}` / `{node}` in its command shows
 * them written out as they run, so the command alone says what runs; any other case shows its command.
 */
export function shownCommand(item: TestSpecCasePayload): string {
  const command = item.run_command ?? '', match = LOCATOR.exec((item.automation_ref ?? '').trim())
  if (!match || !/\{target\}|\{node\}/.test(command)) return command
  const cwd = (item.run_cwd ?? '').trim() || '.', node = match[2] ?? ''
  const target = cwd === '.' ? match[1] : relativePath(cwd, match[1])
  return command.split(/(\s+)/).filter((part, i, parts) => node || part !== '{node}' && !(/^\s+$/.test(part) && parts[i + 1] === '{node}'))
    .map(part => part.split('{target}').join(target).split('{node}').join(node)).join('')
}
/** The draft changed what runs: the server judges the command again (B0005 §2). */
export function runChanged(draft: CaseDraft): boolean {
  return draft.check !== draft.original.check || draft.run_command !== shownCommand(draft.original)
}

export function caseDraft(item: TestSpecCaseDetail, cases: readonly TestSpecCaseDetail[], title: string | null, docId: string | null = null): CaseDraft {
  const original = testSpecCasePayload(item)
  return { original, docId, snapshot: scenarioSnapshot(cases, title),
    title: original.title, content: original.content ?? '',
    procedure: original.procedure ?? '', expected: original.expected ?? '', run_command: shownCommand(original),
    check: original.check ?? 'manual', checker: original.checker, pin: null, pinState: 'idle' }
}
export function newCaseDraft(cases: readonly TestSpecCaseDetail[], title: string | null, docId: string | null): CaseDraft {
  const ids = new Set(cases.map(c => c.caseId.replace(/-0+(\d)/, '-$1')))
  let n = 1
  while (ids.has(`TC-${n}`)) n++
  const original: TestSpecCasePayload = { case_id: `TC-${n}`, title: '', content: '', procedure: '', expected: '',
    check: 'code', checker: null, category: null, required: true, input: null, check_points: null,
    automation_ref: null, test_assets: null }
  return { original, docId, snapshot: scenarioSnapshot(cases, title), title: '', content: '',
    procedure: '', expected: '', run_command: '', check: 'code', checker: null, pin: null, pinState: 'idle' }
}
export function addedCase(cases: readonly TestSpecCaseDetail[], draft: CaseDraft): TestSpecCasePayload[] {
  return [...cases.map(testSpecCasePayload), { ...draft.original, title: draft.title, content: draft.content,
    procedure: draft.procedure, expected: draft.expected, check: draft.check,
    checker: draft.check === 'manual' ? draft.checker : null, automation_ref: null,
    ...(draft.check === 'code' && draft.run_command ? { run_command: draft.run_command } : {}) }]
}

/**
 * Only displayed, changed fields are replaced; hidden fields and untouched nulls survive.
 * A changed command replaces the stored target: the command alone says what runs. A stored cwd is kept.
 * A case made manual sends no command (the server drops one left from before).
 */
export function mergeCase(cases: readonly TestSpecCaseDetail[], draft: CaseDraft): TestSpecCasePayload[] {
  return cases.map((item) => {
    const payload = testSpecCasePayload(item)
    if (item.caseId !== draft.original.case_id) return payload
    const edits: Partial<Record<typeof caseTextFields[number], string>> & { automation_ref?: null } = {}
    for (const field of caseTextFields) {
      if (field === 'run_command') continue
      if (draft[field] !== (draft.original[field] ?? '')) edits[field] = draft[field]
    }
    if (draft.check === 'code' && draft.run_command !== shownCommand(draft.original)) {
      edits.run_command = draft.run_command
      edits.automation_ref = null
    }
    const check = draft.check !== draft.original.check ? draft.check : payload.check
    const { run_command: storedCommand, ...rest } = { ...payload, ...edits }
    return { ...rest, ...(check === 'code' && storedCommand ? { run_command: storedCommand } : {}),
      check, checker: check === 'manual' ? draft.checker ?? payload.checker : null }
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

export interface RawDraft {
  docId: string; baseline: string; text: string; loaded: boolean; open: boolean
  /** The raw draft's pinned base (doc, revision, coordinates) of the snapshot `baseline` was read from. */
  tsBase: TsBase | null
  /** The latest snapshot read apart on a conflict: its whole text (frontmatter included) and base. */
  latest: string | null; latestBase: TsBase | null
}
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
