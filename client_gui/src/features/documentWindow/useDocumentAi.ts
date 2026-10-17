// flowgate.v02.0056 F2-2 — the state and the calls of a document's AI call window (O3 0009-TR §4-3): the project's AIs
// (settingsChoicesRead — the settings tab's read; the project's default first), the opinion, and [실행] — the only
// place the AI is called (documentAiStart: edit · single · the pinned AI · the opinion, no review loop). The run
// started is followed by documentAiWindows (trackDocumentAiRun) until it ends; the opener hears the end.
import { computed, ref, type ComputedRef, type Ref } from 'vue'
import { documentAiErrorOf, documentAiStart, settingsChoicesRead, type SettingsProvider } from '@/features/document'
import { documentAiFailure, documentAiMessages, documentAiPhase, documentAiRunnable, type DocumentAiFailure, type DocumentAiPhase } from './documentAiModel'
import {
  documentAiActivityOf,
  documentAiRunOf,
  readDocumentAiActivity,
  trackDocumentAiRun,
  type DocumentAiTrackedRun,
  type DocumentAiWindowState,
} from './documentAiWindows'

export interface DocumentAiState {
  readonly providers: Readonly<Ref<readonly SettingsProvider[]>>
  readonly choicesState: Readonly<Ref<'loading' | 'ready' | 'error'>>
  readonly providerId: Ref<string>
  readonly opinion: Ref<string>
  /** The run started here for the document (the last one), or undefined. */
  readonly run: ComputedRef<DocumentAiTrackedRun | undefined>
  readonly phase: ComputedRef<DocumentAiPhase>
  /** [실행] may be pressed. */
  readonly runnable: ComputedRef<boolean>
  /** Another AI run holds the group (read when the window opened): on this document, or on another one; else null. */
  readonly otherRun: ComputedRef<'busy_this' | 'busy_other' | null>
  readonly failure: Readonly<Ref<DocumentAiFailure | null>>
  readChoices(): Promise<void>
  start(): Promise<void>
}

export function useDocumentAi(win: () => DocumentAiWindowState): DocumentAiState {
  const providers = ref<readonly SettingsProvider[]>([])
  const choicesState = ref<'loading' | 'ready' | 'error'>('loading')
  const providerId = ref('')
  const opinion = ref('')
  const starting = ref(false)
  const failure = ref<DocumentAiFailure | null>(null)

  const run = computed(() => documentAiRunOf(win().docId))
  const phase = computed(() => documentAiPhase(starting.value, run.value))
  const runnable = computed(() => documentAiRunnable(providerId.value, phase.value) && choicesState.value === 'ready')
  const otherRun = computed(() => {
    const activity = documentAiActivityOf(win().request.groupId)
    if (!activity?.active || (run.value && activity.runId === run.value.runId)) return null
    return activity.docRef === win().docId ? 'busy_this' : 'busy_other'
  })

  async function readChoices(): Promise<void> {
    choicesState.value = 'loading'
    try {
      const choices = await settingsChoicesRead(win().request.projectId)
      providers.value = choices.providers
      if (!choices.providers.some((provider) => provider.id === providerId.value)) {
        const preferred = choices.providers.find((provider) => provider.id === choices.defaultProviderId) ?? choices.providers[0]
        providerId.value = preferred?.id ?? ''
      }
      choicesState.value = 'ready'
    } catch {
      choicesState.value = 'error'
    }
  }

  async function start(): Promise<void> {
    if (!runnable.value) return
    const current = win().request
    starting.value = true
    failure.value = null
    try {
      const started = await documentAiStart({ groupId: current.groupId, docId: current.docId, providerId: providerId.value, messages: documentAiMessages(opinion.value) })
      trackDocumentAiRun(current.docId, current.groupId, started, current.finished)
      opinion.value = ''
    } catch (error) {
      failure.value = documentAiFailure(documentAiErrorOf(error))
      // The group's live run (the one holding the lease) is read again for the line and the note.
      void readDocumentAiActivity(current.groupId)
    } finally {
      starting.value = false
    }
  }

  return { providers, choicesState, providerId, opinion, run, phase, runnable, otherRun, failure, readChoices, start }
}
