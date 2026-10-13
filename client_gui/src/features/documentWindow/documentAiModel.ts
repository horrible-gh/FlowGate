// flowgate.v02.0056 F2-2 — what the AI call window decides without a screen or a server call (O3 0009-TR §4-3):
// whether [실행] may be pressed, what the run line says and how a refused start is shown. Pure.
//
// A refused start keeps the v0.1 envelope: 409 run_in_progress (another AI run holds the group) shows the
// server's message as it is (x-locale) — the code's own words only when it sent none; the other refusals show
// the code's words with the server's message after them.
import type { DocumentAiError, DocumentAiRunState } from '@/features/document'

/** idle: nothing started here; starting: [실행] waits for its answer; running / finished: the run started here. */
export type DocumentAiPhase = 'idle' | 'starting' | 'running' | 'finished'

export function documentAiPhase(starting: boolean, run: Pick<DocumentAiRunState, 'finished'> | null | undefined): DocumentAiPhase {
  if (starting) return 'starting'
  if (!run) return 'idle'
  return run.finished ? 'finished' : 'running'
}

/** [실행] may be pressed: an AI is picked and no run started here is under way. */
export function documentAiRunnable(providerId: string | null | undefined, phase: DocumentAiPhase): boolean {
  return !!providerId && phase !== 'starting' && phase !== 'running'
}

/** The opinion as it is sent: one message, or none when blank (the server leaves blank ones out too). */
export function documentAiMessages(opinion: string): string[] {
  return opinion.trim() === '' ? [] : [opinion]
}

export interface DocumentAiFailure {
  /** Message key under `gui.document_window.ai.failure.` */
  readonly key: string
  /** The server's message (x-locale), or ''. */
  readonly message: string
  /** run_in_progress: the server's message is the whole text (shown as it is). */
  readonly verbatim: boolean
}

/** A refused start as the window shows it. */
export function documentAiFailure(error: Pick<DocumentAiError, 'code' | 'status' | 'network' | 'message'>): DocumentAiFailure {
  const message = error.message && error.message !== error.code ? error.message : ''
  if (error.network) return { key: 'network', message: '', verbatim: false }
  if (error.code === 'run_in_progress') return { key: 'run_in_progress', message, verbatim: message !== '' }
  if (error.status === 400 || error.status === 422) return { key: 'invalid', message, verbatim: false }
  if (error.status === 401 || error.status === 403) return { key: 'forbidden', message, verbatim: false }
  if (error.status === 404) return { key: 'not_found', message, verbatim: false }
  return { key: 'other', message, verbatim: false }
}

/** Every failure key the window may show (each has ko · en · ja words). */
export const DOCUMENT_AI_FAILURE_KEYS: readonly string[] = ['run_in_progress', 'invalid', 'forbidden', 'not_found', 'network', 'other']
