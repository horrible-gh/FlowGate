// flowgate.v02.0031 — Conversation session: the single owner of the part's state (current
// scope, chat list, the active chat's turns / activities / participants, providers, chat
// settings, the send and AI-run state). Not a Pinia store: ConversationPanel creates one and it
// lives and dies with the panel, like the Designer Center session. Screens read `state` and
// change it only through the actions; every server call goes through the ConversationSource.
//
// The send / retry / read / AI-run / settings rules are the ones the v0.1 ConversationView
// already follows (P0003, L0004, L0008, L0010, 0264, 0568); only the screen around them is new.
//
// flowgate.v02.0032 (D0006 §3.9.2, §6.2, §6.3): command requests and run changes are re-read
// from the server — when a chat opens, on every AI-run polling tick, once right after the run
// ends and once after each decision — and each read replaces them as a whole. A new chat is a
// standalone CH; nothing is shown before the server has it.
import { computed, reactive, readonly, watch, type ComputedRef, type DeepReadonly } from 'vue'
import { draftUserId, type DraftSendSnapshot } from './drafts'
import { createSubmissionTrace, type SubmissionTrace, type SubmissionPhase } from './submissionTrace'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS, newIdempotencyKey, type CreateChatOptions } from './api'
import { createHttpConversationSource, type ConversationSource } from './conversationSource'
import { buildTimeline, mergeTurns, replaceServerActivities, sendFailureOf, upsertActivity, type PendingTurn, type SendFailure, type TimelineItem } from './timeline'
import { runBelongsToChat, runBodyState, runStatusLines, type TurnReadResult } from './runAssessment'
import { chatErrorActivity, keepLocalChatError, newChatErrorId, readLocalChatErrors, redactChatError, type ChatErrorRecord } from './chatErrors'
import type {
  AiRunState,
  ChatProvider,
  ChatSettings,
  ChatSettingsPatch,
  ChatSummary,
  ChatAutoTitle,
  CheckpointKind,
  CommandDecision,
  CommandRequestActivity,
  ConversationActivity,
  ConversationParticipant,
  ConversationScope,
  ConversationTurn,
  ChatSourceAccess,
  RunDisplay,
  SourceAccessMode,
  StatusActivity,
  StatusTone,
} from './types'

// L0004 §1-5 screen parameters (same values as the v0.1 ConversationView).
export const TURN_PAGE_SIZE = 50
export const PREPEND_PAGE_SIZE = 30
export const CATCHUP_MAX_ROUNDS = 20
export const SEND_RETRY_MAX = 2
export const RUN_POLL_MS = 2500
export const RUN_POLL_MAX = 480
export const FINAL_TURN_READ_ATTEMPTS = 3
// flowgate.v02.0151 T-A2 (NR §2.6-3 ⑤): the activity read after a run ended is tried as often as its final turn read.
export const FINAL_ACTIVITY_READ_ATTEMPTS = FINAL_TURN_READ_ATTEMPTS
// flowgate.v02.0151 T-A2 (NR §2.6-3 ②): the shortest gap between two catch-ups started by coming back to the panel.
export const REVISIT_CATCHUP_MIN_MS = 3000
export const RUN_HISTORY_INITIAL_LIMIT = 20
export const RUN_HISTORY_MAX_LIMIT = 100 // server services/ai_invoke/runtime.py
export const RUN_HISTORY_DETAIL_LIMIT = 50 // sequential reads: concurrency = 1
// flowgate.v02.0084 T#2: a run adopted before the server has registered it (409 right after the
// lease, or a /start that timed out) reads 404 for a while; it is polled this long before giving up.
export const RUN_ADOPT_GRACE_MS = 60_000
// flowgate.v02.0152: a /start that got no answer (or 409 `start_pending`: the server is still admitting that same
// press) is sent again with the same client_request_id, RUN_POLL_MS apart, for at most START_RECOVERY_MS (a group's
// first source sync can hold admission for minutes) and at most START_RESEND_MAX times (what bounds it under a test
// clock). The server answers a known press with the run it admitted, so a resend never starts a second run.
export const START_RESEND_MAX = 120
export const START_RECOVERY_MS = 300_000
// flowgate.v02test.0013: the chat's active run looked up when a chat opens is asked this often, RUN_POLL_MS apart,
// when the query itself fails; a run missed then would otherwise go untracked until the next signal.
export const ACTIVE_RUN_ADOPT_ATTEMPTS = FINAL_TURN_READ_ATTEMPTS

/**
 * flowgate.v02test.0013 — what a signal (a server event, a stream reconnect, coming back to the panel) says may have
 * changed in the open chat: `run` its active run (adopt it and poll), `turns` its messages after the last one shown,
 * `activities` its command requests and run changes, `history` the terminal state of runs that ended unseen.
 */
export interface ChatRefresh {
  run?: boolean
  turns?: boolean
  activities?: boolean
  history?: boolean
}

/** Everything: a reconnect or a return to the panel, after which any of it may be missing. */
export const FULL_CHAT_REFRESH: Readonly<Required<ChatRefresh>> = Object.freeze({ run: true, turns: true, activities: true, history: true })

/** The union of two refresh requests (null: none yet). */
export function mergeChatRefresh(a: ChatRefresh | null, b: ChatRefresh): ChatRefresh {
  return {
    run: !!(a?.run || b.run),
    turns: !!(a?.turns || b.turns),
    activities: !!(a?.activities || b.activities),
    history: !!(a?.history || b.history),
  }
}

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** Inline feedback line (the part opens no dialog — 0235 R0001: chat stays dialog-free). */
export interface ConversationNotice {
  id: number
  tone: StatusTone
  /** `gui.conversation.notice.<code>` */
  code: string
  detail: string | null
}

export interface ConversationState {
  scope: ConversationScope | null
  chats: ChatSummary[]
  chatsStatus: LoadStatus
  chatsError: string | null
  activeChatId: string | null
  turnsStatus: LoadStatus
  turnsError: string | null
  turns: ConversationTurn[]
  pending: PendingTurn[]
  activities: ConversationActivity[]
  participants: ConversationParticipant[]
  headSeq: number
  readSeq: number
  hasMoreBefore: boolean
  loadingOlder: boolean
  providers: ChatProvider[]
  providersStatus: LoadStatus
  /** flowgate.v02test.0025: the open chat's AI (each chat keeps its own; a new chat starts on my default AI). */
  selectedProviderId: string
  /** flowgate.v02test.0025: seq of the message the open chat's AI context starts from; null = no checkpoint. */
  checkpointSeq: number | null
  /** 'include' = the AI context starts at that message, 'exclude' = at the next one; null without a checkpoint. */
  checkpointKind: CheckpointKind | null
  savingCheckpoint: boolean
  settings: ChatSettings
  savingSettings: boolean
  pendingSourceMode: SourceAccessMode | null
  sending: boolean
  creatingChat: boolean
  renaming: boolean
  runId: string | null
  /**
   * flowgate.v02test.0024 D: what the one run in progress line shows while `runId` is set — the AI connection name
   * and the read / edit access, from the call itself first and then from the server's run detail (the applied value).
   */
  runDisplay: RunDisplay | null
  cancelling: boolean
  /** Command ids whose decision is in flight. */
  deciding: string[]
  /** Invalidates panel-owned change windows whenever clearChat resets this chat lifetime. */
  changeWindowEpoch: number
  copyingMention: boolean
  /** Text of a mention copy that did not reach the clipboard (inline manual copy, 0240). */
  manualCopyText: string | null
  notice: ConversationNotice | null
}

export interface CreatedChat {
  status: 'created'
  chatId: string
  scope: ConversationScope
  draftIntentId: string | null
  createIntentId: string
}
/**
 * flowgate.v02.0142 T#1: what `send` addresses — the chat just made by a first send (`CreatedChat`), the AI the
 * message is pinned to (`providerId`), both, or neither (the open chat on the current selection).
 */
export type SendTarget = (CreatedChat & { providerId?: string }) | { providerId?: string }
export type CreateChatResult = CreatedChat | { status: 'cancelled' | 'failed'; chatId: string | null; scope: ConversationScope | null; draftIntentId: string | null; createIntentId: string | null }
export interface CreateChatContext {
  draftIntentId: string
  createIntentId: string
  isCurrent(): boolean
  onCreated(result: CreatedChat): void
  onLoading?(): void
  trace?: SubmissionTrace
}

export interface ConversationSession {
  readonly state: DeepReadonly<ConversationState>
  readonly activeChat: ComputedRef<DeepReadonly<ChatSummary> | null>
  readonly timeline: ComputedRef<TimelineItem[]>
  readonly running: ComputedRef<boolean>
  readonly hasProviders: ComputedRef<boolean>
  readonly submission: ComputedRef<SubmissionPhase | null>
  readonly supportsActivities: boolean
  /** Title-only lifetime/revision guard; no second title store. */
  readonly titleGuard: DeepReadonly<{ disposed: boolean; revision: number; listRevision: number; userId: string }>
  titleRevision(chatId: string): number

  setScope(scope: ConversationScope | null): Promise<void>
  /** Settles once the provider list of the current scope has loaded (or failed to). */
  providersLoaded(): Promise<void>
  reloadChats(trace?: SubmissionTrace): Promise<void>
  selectChat(chatId: string, trace?: SubmissionTrace): Promise<void>
  renameActiveChat(title: string): Promise<boolean>
  createChat(title: string, options?: CreateChatOptions): Promise<boolean>
  createChat(title: string, options: CreateChatOptions | undefined, context: CreateChatContext): Promise<CreateChatResult>
  /** Leaves the open chat for an unsaved new one: nothing exists until its first send. */
  startDraft(): void
  /** Shows a title the server already stored (the AI title of a new chat). */
  setChatTitle(chatId: string, title: string, marker?: ChatAutoTitle): void
  loadOlder(trace?: SubmissionTrace): Promise<void>
  catchUp(): Promise<void>
  /**
   * flowgate.v02test.0013 — brings the open chat back in line with the server after a signal (default: everything).
   * One recovery runs at a time: a signal while it runs is merged into one more round after it, so nothing asked for
   * is dropped and nothing is read twice side by side. A run already polled is not read beside its poll: the poll's
   * wait is cut short instead. While the chat is still opening, or a message of it is being sent, the request waits
   * and runs right after (a turn read then could show the message twice: once sending, once stored).
   */
  recover(parts?: ChatRefresh): Promise<void>
  /**
   * flowgate.v02.0142 T#1: the AI of the message is pinned when it is sent: `target.providerId` when given (the first
   * message of a new chat, whose AI was chosen before the chat was created), else the current selection.
   */
  send(text: string, target?: SendTarget, trace?: SubmissionTrace, snapshot?: DraftSendSnapshot): Promise<boolean>
  retry(localId: string): Promise<boolean>
  reportRead(seq: number): Promise<void>
  decideCommand(commandId: string, decision: CommandDecision): Promise<void>
  /** Picks the open chat's AI and stores it as that chat's pick (a draft keeps it for the chat its first send makes). */
  selectProvider(providerId: string): void
  /**
   * flowgate.v02test.0025: the open chat's checkpoint: send from message `seq` ('include') or from the next one
   * ('exclude'); null clears it. Applied while the context setting is 체크포인트 사용.
   */
  setCheckpoint(seq: number | null, kind?: CheckpointKind): Promise<void>
  /** Whether the source keeps per-chat settings (AI pick, context checkpoint). */
  readonly supportsChatDocSettings: boolean
  invokeAi(): Promise<void>
  stopRun(): Promise<void>
  /** Resolves the chat mention; the screen writes it to the clipboard (it owns the click). */
  issueMention(): Promise<string | null>
  setManualCopyText(text: string | null): void
  saveSettings(patch: ChatSettingsPatch): Promise<boolean>
  setSourceMode(mode: SourceAccessMode): Promise<void>
  /** Adds an activity pushed by the container or the source (upsert by id). */
  pushActivity(activity: ConversationActivity): void
  /** Shows the inline notice line (`gui.conversation.notice.<code>`). */
  notify(tone: StatusTone, code: string, detail?: string | null): void
  dismissNotice(): void
  dispose(): void
}

export interface ConversationSessionOptions {
  source?: ConversationSource
  /** Wait between AI-run polls; tests replace it. */
  sleep?: (ms: number) => Promise<void>
  /** Called with `{ auto: true }` after a send whose send action is copy_mention. */
  onAutoCopyMention?: () => void
}

/**
 * flowgate.v02.0061 T#14 (WP answer 2): the server refused an edit / edit-once source access because the Flow's Graph
 * run is running, paused or stopping (409 `graph_run_locked`).
 */
function isSourceLocked(error: unknown): boolean {
  return error instanceof ConversationRequestError && error.code === 'graph_run_locked'
}

/** flowgate.v02.0084 T#2: the request got no answer (timeout / network); the server may still have acted. */
function isNoResponse(error: unknown): boolean {
  return error instanceof ConversationRequestError && error.status === null
}

/** flowgate.v02.0152: the server is still admitting the first request of this same press; ask again shortly. */
function isStartPending(error: unknown): boolean {
  return error instanceof ConversationRequestError && error.status === 409 && error.code === 'start_pending'
}

/** flowgate.v02.0152: one id per AI-call press (`client_request_id`, 8..200 of [A-Za-z0-9._:-]). */
function newStartRequest(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `run-${random}`
}

/** flowgate.v02.0135 T#3: a fresh create intent id (`client_request_id`, 8..200 of [A-Za-z0-9._:-]). */
function newCreateIntent(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
  return `chat-${random}`
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms))

export function createConversationSession(options: ConversationSessionOptions = {}): ConversationSession {
  const source = options.source ?? createHttpConversationSource()
  const sleep = options.sleep ?? defaultSleep

  const state = reactive<ConversationState>({
    scope: null,
    chats: [],
    chatsStatus: 'idle',
    chatsError: null,
    activeChatId: null,
    turnsStatus: 'idle',
    turnsError: null,
    turns: [],
    pending: [],
    activities: [],
    participants: [],
    headSeq: 0,
    readSeq: 0,
    hasMoreBefore: false,
    loadingOlder: false,
    providers: [],
    providersStatus: 'idle',
    selectedProviderId: '',
    checkpointSeq: null,
    checkpointKind: null,
    savingCheckpoint: false,
    settings: { ...DEFAULT_CHAT_SETTINGS },
    savingSettings: false,
    pendingSourceMode: null,
    sending: false,
    creatingChat: false,
    renaming: false,
    runId: null,
    runDisplay: null,
    cancelling: false,
    deciding: [],
    changeWindowEpoch: 0,
    copyingMention: false,
    manualCopyText: null,
    notice: null,
  })

  // Outbox and progress belong to the original user / Flow / CH, including while hidden.
  const outbox = new Map<string, PendingTurn[]>()
  const progress = reactive(new Map<string, SubmissionPhase>())
  const traces = new Map<string, SubmissionTrace>()
  // A receipt acknowledges only the unchanged draft that produced this failed turn.
  // Re-entering the same text after an edit is a new submission, with a new revision.
  const pendingDrafts = new Map<string, DraftSendSnapshot>()
  const retryReceipts = new Map<string, DraftSendSnapshot>()
  const ownerKey = (chatId: string) => JSON.stringify([draftUserId(), state.scope?.projectId, state.scope?.groupId, chatId])
  // flowgate.v02gui.0009: the same owner, named by the chat's own Flow (a first send whose screen moved on to another one).
  const scopedOwnerKey = (scope: ConversationScope, chatId: string) => JSON.stringify([draftUserId(), scope.projectId, scope.groupId, chatId])
  const submission = computed(() => state.activeChatId ? progress.get(ownerKey(state.activeChatId)) ?? null : null)
  const visibleOwner = (key: string) => state.activeChatId !== null && ownerKey(state.activeChatId) === key
  function publishOutbox(key: string, items: PendingTurn[]): void {
    if (items.length) outbox.set(key, items)
    else outbox.delete(key)
    if (visibleOwner(key)) state.pending = items
  }
  function publishProgress(key: string, value: SubmissionPhase | null): void {
    if (value) progress.set(key, value)
    else progress.delete(key)
    if (visibleOwner(key)) state.sending = value?.phase === 'sending'
  }
  let disposed = false
  // Bumped on every scope / chat switch; a response for an older generation is dropped.
  let scopeGen = 0
  let chatGen = 0
  // flowgate.v02.0135 T#3: the create intent of the open draft (one per Flow + draft): kept across its failed tries.
  let createIntent: { key: string; scopeGen: number; chatGen: number } | null = null
  let createOperation = 0
  let providersProject: string | null = null
  let providersDefaultId: string | null = null
  // flowgate.v02test.0025: each chat's AI pick as last read or picked on this screen (key: ownerKey); null = my default.
  const chatProviders = new Map<string, string | null>()
  let providersRequest = 0
  let providersLoad: Promise<void> = Promise.resolve()
  let noticeSeq = 0
  let chatsRequest = 0
  const unlistedChats = new Map<string, ChatSummary>()
  const titleGuard = reactive({ disposed: false, revision: 0, listRevision: 0, userId: draftUserId() })
  const titleRevisions = new Map<string, number>()
  const manualTitlesPending = new Set<string>()
  function titleRevision(chatId: string): number { return titleRevisions.get(chatId) ?? 0 }
  function touchTitle(chatId: string): void { titleRevisions.set(chatId, ++titleGuard.revision) }
  const titleTerminal = (chat: ChatSummary) => ['done', 'manual', 'failed'].includes(chat.autoTitle?.state ?? '')
  let unsubscribe: (() => void) | null = null
  // Highest read seq already reported for the active chat; it belongs to that chat and is
  // reset with it (clearChat), so another chat's position never suppresses a report.
  let reportedRead = 0
  // Activity reads are numbered. An answer older than one already applied is dropped, and so
  // is every read started before a decision answered: it may still show the request pending.
  let activityReads = 0
  let activityApplied = 0
  let activityStaleUpTo = 0
  // Rebuilt from the server on every entry; this cache only supports a later catch-up repair.
  const terminalRuns = new Map<string, AiRunState>()
  const runChecks = new Map<string, number>()
  const settingsRefreshedRuns = new Set<string>()
  let trackedRunId: string | null = null
  let historyRequest = 0
  // flowgate.v02test.0013 — the poll of the tracked run (pollRun). A signal sets `due` and calls `wake`, so the
  // poll's next tick reads now instead of after the rest of its RUN_POLL_MS wait (a signal during a tick makes the
  // next wait none). Exactly one per chat generation: clearChat wakes and drops it, and pollRun refuses a second
  // poll of the run it already polls.
  interface LivePoll { chatId: string; gen: number; runId: string; due: boolean; wake: (() => void) | null }
  let livePoll: LivePoll | null = null
  // flowgate.v02test.0013 — the recovery running for the open chat, with what was asked for while it runs.
  interface Recovery { chatId: string; gen: number; wanted: ChatRefresh | null; done: Promise<void> }
  let recovery: Recovery | null = null
  // flowgate.v02test.0013 — a recovery asked for while the chat was opening or one of its messages was being sent.
  let deferredRefresh: { gen: number; parts: ChatRefresh } | null = null


  const activeChat = computed(() => state.chats.find((c) => c.id === state.activeChatId) ?? null)
  // flowgate.v02test.0024 D: while a run goes on, ONE status line says which AI runs it and with which access. It is
  // untimed — the newest thing in the chat, after every turn — and goes the moment the run ends (runId null), when the
  // run's own result lines take over.
  const timeline = computed(() => {
    const display = state.runId !== null ? state.runDisplay : null
    const activities = display ? [...state.activities, runLine(display)] : state.activities
    return buildTimeline(state.turns, activities, state.pending, state.readSeq)
  })
  const running = computed(() => state.runId !== null)
  watch(() => state.runId, (runId) => { if (runId === null) state.runDisplay = null }, { flush: 'sync' })
  const hasProviders = computed(() => state.providersStatus === 'ready' && state.providers.length > 0)

  function notify(tone: StatusTone, code: string, detail: string | null = null): void {
    noticeSeq += 1
    state.notice = { id: noticeSeq, tone, code, detail }
  }

  // flowgate.v02.0135 T#2: the notice line belongs to the Flow and the chat (or draft) it was
  // raised for. Moving to another Flow, another chat or a new draft clears it, and an answer that
  // arrives after such a move (chat generation `gen` is gone) does not raise one on the new screen.
  function notifyIfCurrent(gen: number, tone: StatusTone, code: string, detail: string | null = null): void {
    if (!disposed && gen === chatGen) notify(tone, code, detail)
  }

  function runLine(display: DeepReadonly<RunDisplay> | RunDisplay): StatusActivity {
    return {
      kind: 'status', id: 'local-status-run-running', chatId: state.activeChatId ?? undefined, createdAt: null,
      tone: 'info', code: 'run_running', text: null, run: { providerName: display.providerName, access: display.access },
    }
  }

  function providerName(providerId: string | null | undefined): string | null {
    return providerId ? state.providers.find((p) => p.id === providerId)?.name ?? null : null
  }

  /** The access a call made now gets: the user's stored mode decides the run's token (an edit once is an edit). */
  function accessOfMode(mode: SourceAccessMode): ChatSourceAccess {
    return mode === 'read_only' ? 'read' : 'read_write'
  }

  /** The server's run detail is the applied value; what it does not say yet keeps what the line already shows. */
  function showRunDetail(run: AiRunState): void {
    const shown = state.runDisplay
    state.runDisplay = {
      providerName: run.providerName ?? providerName(run.providerId) ?? shown?.providerName ?? null,
      access: run.sourceAccess ?? shown?.access ?? null,
    }
  }

  /**
   * flowgate.v02test.0024 B: a refused send / AI call / command decision becomes a line of this chat's history (at its
   * time) instead of the notice above the composer, and is recorded so F5 and reopening the chat bring it back: the
   * server keeps what it answered (4xx / 5xx); what got no answer — or whose record could not be stored — stays in
   * this browser. Only the code, the server's words and the time; never a token or a header.
   */
  function recordChatError(chatId: string, gen: number, code: string, error: unknown, withDetail = true, offScreen = false): void {
    // flowgate.v02gui.0009: a failed send is recorded for the chat it went to even after the user left it (`offScreen`),
    // so coming back (or F5) shows it; it is drawn now only when that chat is the open one.
    const current = isCurrentChat(chatId, gen)
    if (!current && !offScreen) return
    const record: ChatErrorRecord = {
      id: newChatErrorId(), code, occurredAt: new Date().toISOString(),
      message: withDetail && error != null ? redactChatError(messageOf(error)) : '',
    }
    if (current || (!disposed && state.activeChatId === chatId)) state.activities = upsertActivity(state.activities, chatErrorActivity(chatId, record))
    void persistChatError(chatId, record, error)
  }

  async function persistChatError(chatId: string, record: ChatErrorRecord, error: unknown): Promise<void> {
    const userId = draftUserId()
    const answered = error instanceof ConversationRequestError && error.status !== null
    if (answered && source.recordChatError) {
      try {
        await source.recordChatError(chatId, record)
        return
      } catch {
        // The server could not take it: this browser keeps it instead.
      }
    }
    keepLocalChatError(userId, chatId, record)
  }

  /** The errors this browser kept for the chat (the server's come with its activity read). */
  function restoreLocalChatErrors(chatId: string): void {
    for (const record of readLocalChatErrors(draftUserId(), chatId)) {
      state.activities = upsertActivity(state.activities, chatErrorActivity(chatId, record))
    }
  }

  function runStatusPrefix(chatId: string, runId: string): string {
    return `local-status-run:${encodeURIComponent(chatId)}:${encodeURIComponent(runId)}:`
  }

  function renderTerminal(chatId: string, run: AiRunState, read: TurnReadResult): void {
    const prefix = runStatusPrefix(chatId, run.runId)
    const body = runBodyState(state.turns, run.runId, read.status)
    state.activities = state.activities.filter((a) => !a.id.startsWith(prefix))
    for (const line of runStatusLines(run, body, read.status)) {
      state.activities = upsertActivity(state.activities, {
        kind: 'status', id: prefix + line.slot, chatId, runId: run.runId, runResult: { ...run },
        // flowgate.v02test.0024 B: a result line sits at its run's time like any entry of the history. Without a
        // finish time the start places it; only a run with neither stays untimed (after every turn).
        createdAt: run.finishedAt ?? run.startedAt ?? null, tone: line.tone, code: line.code, text: line.text,
      })
    }
    if (body === 'present' && trackedRunId === run.runId && !settingsRefreshedRuns.has(run.runId)) {
      settingsRefreshedRuns.add(run.runId)
      const gen = chatGen
      void loadSettings(state.scope, () => isCurrentChat(chatId, gen) && trackedRunId === run.runId &&
        (state.runId == null || state.runId === run.runId))
    }
  }

  function showRunUnconfirmed(chatId: string, runId: string, startedAt: string | null = null): void {
    const id = runStatusPrefix(chatId, runId) + 'result'
    state.activities = upsertActivity(state.activities, {
      kind: 'status', id, chatId, runId, createdAt: startedAt, tone: 'warning', code: 'run_result_unconfirmed', text: null,
    })
  }

  function showHistoryUnconfirmed(chatId: string, unconfirmed: boolean): void {
    const id = `local-status-history:${encodeURIComponent(chatId)}`
    state.activities = state.activities.filter((a) => a.id !== id)
    if (unconfirmed) state.activities = upsertActivity(state.activities, {
      kind: 'status', id, chatId, createdAt: null, tone: 'warning', code: 'run_history_unconfirmed', text: null,
    })
  }

  function clearChat(): void {
    terminalRuns.clear()
    runChecks.clear()
    settingsRefreshedRuns.clear()
    trackedRunId = null
    historyRequest += 1
    // The old chat's poll ends on its next check; waking it lets that happen now, not after its wait.
    const poll = livePoll
    livePoll = null
    poll?.wake?.()
    recovery = null
    deferredRefresh = null
    unsubscribe?.()
    unsubscribe = null
    state.turns = []
    state.pending = []
    state.sending = false
    state.activities = []
    state.participants = []
    state.headSeq = 0
    state.readSeq = 0
    reportedRead = 0
    state.hasMoreBefore = false
    state.loadingOlder = false
    state.turnsStatus = 'idle'
    state.turnsError = null
    state.manualCopyText = null
    state.runId = null
    state.cancelling = false
    state.deciding = []
    state.checkpointSeq = null
    state.checkpointKind = null
    state.savingCheckpoint = false
    state.changeWindowEpoch += 1
  }

  function applyPage(page: Awaited<ReturnType<ConversationSource['fetchTurns']>>, participantsComplete = false): void {
    state.turns = mergeTurns(state.turns, page.turns)
    state.headSeq = Math.max(state.headSeq, page.headSeq)
    // Empty scroll pages omit metadata; an includeHead response is authoritative, even empty.
    if (participantsComplete || page.participants.length > 0) state.participants = page.participants
    if (page.myReadSeq != null) state.readSeq = Math.max(state.readSeq, page.myReadSeq)
  }

  function oldestSeq(): number {
    return state.turns.length > 0 ? state.turns[0].seq : state.headSeq + 1
  }

  // ── Scope / chat list ──────────────────────────────────────────────────────────────
  function loadProviders(projectId: string): Promise<void> {
    if (providersProject === projectId && state.providersStatus !== 'error') return providersLoad
    providersProject = projectId
    state.providersStatus = 'loading'
    providersLoad = fetchProviders(projectId)
    return providersLoad
  }

  function providersLoaded(): Promise<void> {
    return providersLoad
  }

  // ── Per-chat AI pick and context checkpoint (flowgate.v02test.0025) ──────────────────
  /** My default AI when this scope offers it, else the first offered AI. */
  function defaultProviderId(): string {
    const id = providersDefaultId
    return id && state.providers.some((p) => p.id === id) ? id : state.providers[0]?.id ?? ''
  }

  /** Shows `pick` when it is still offered; anything else (none, removed, not runnable) is my default AI. */
  function applyChatProvider(pick: string | null): void {
    if (state.providersStatus !== 'ready') return
    state.selectedProviderId = pick && state.providers.some((p) => p.id === pick) ? pick : defaultProviderId()
  }

  function persistChatProvider(chatId: string, providerId: string): void {
    if (!source.saveChatDocSettings) return
    source.saveChatDocSettings(chatId, { provider_id: providerId }).catch((error) => {
      if (!disposed && state.activeChatId === chatId) notify('warning', 'chat_ai_save_failed', messageOf(error))
    })
  }

  async function loadChatDocSettings(chatId: string, gen: number): Promise<void> {
    const key = ownerKey(chatId)
    const before = chatProviders.get(key)
    if (!source.fetchChatDocSettings) return
    try {
      const [settings] = await Promise.all([source.fetchChatDocSettings(chatId), providersLoad])
      if (!isCurrentChat(chatId, gen)) return
      state.checkpointSeq = settings.checkpointSeq
      state.checkpointKind = settings.checkpointKind
      // A pick made on this screen while the read was out wins: it is the newer one (and is being saved).
      if (chatProviders.get(key) !== before) return
      const pick = settings.providerId ?? before ?? null
      chatProviders.set(key, pick)
      applyChatProvider(pick)
    } catch {
      // Keep chatting on the shown selection (my default AI or this screen's last pick); never block.
    }
  }

  async function fetchProviders(projectId: string): Promise<void> {
    const request = ++providersRequest
    const userId = draftUserId()
    try {
      const list = await source.listProviders(projectId)
      if (disposed || providersProject !== projectId || request !== providersRequest || userId !== draftUserId()) return
      state.providers = list.providers
      providersDefaultId = list.defaultProviderId
      const keep = list.providers.some((p) => p.id === state.selectedProviderId)
      state.selectedProviderId = keep
        ? state.selectedProviderId
        : list.defaultProviderId && list.providers.some((p) => p.id === list.defaultProviderId)
          ? list.defaultProviderId
          : list.providers[0]?.id ?? ''
      state.providersStatus = 'ready'
    } catch {
      if (disposed || providersProject !== projectId || request !== providersRequest || userId !== draftUserId()) return
      state.providers = []
      state.selectedProviderId = ''
      state.providersStatus = 'error'
    }
  }

  let settingsRequest = 0
  function sameScope(left: ConversationScope | null, right: ConversationScope | null): boolean {
    return left === null ? right === null : !!right && left.projectId === right.projectId && left.groupId === right.groupId
  }

  function settingsCurrent(gen: number, scope: ConversationScope | null): boolean {
    return !disposed && gen === scopeGen && sameScope(scope, state.scope)
  }

  function applySettings(settings: ChatSettings, scope: ConversationScope | null): boolean {
    if (!sameScope(settings.sourceAccessScope, scope)) return false
    state.settings = { ...settings, sourceAccessMode: scope ? settings.sourceAccessMode : 'read_only' }
    return true
  }

  async function loadSettings(requestScope: ConversationScope | null, current: () => boolean = () => true): Promise<void> {
    const gen = scopeGen
    const scope = requestScope ? { ...requestScope } : null
    if (!settingsCurrent(gen, scope) || !current()) return
    const request = ++settingsRequest
    if (scope) state.pendingSourceMode = 'read_only'
    try {
      const settings = await source.fetchChatSettings(scope ?? undefined)
      if (settingsCurrent(gen, scope) && request === settingsRequest && current()) applySettings(settings, scope)
    } catch {
      // D0008 §3-4: keep chatting on defaults, never block.
    } finally {
      if (settingsCurrent(gen, scope) && request === settingsRequest) state.pendingSourceMode = null
    }
  }

  async function reloadChats(trace?: SubmissionTrace): Promise<void> {
    const scope = state.scope
    if (!scope) return
    const gen = scopeGen
    const request = ++chatsRequest
    const revisions = new Map(titleRevisions)
    const userId = draftUserId()
    state.chatsStatus = 'loading'
    state.chatsError = null
    try {
      trace?.mark('list_start')
      const chats = await source.listChats(scope)
      trace?.mark('list_end', { result: 'ok' })
      if (disposed || gen !== scopeGen || request !== chatsRequest || userId !== draftUserId()) return
      const merged = chats.map((chat) => {
        const current = state.chats.find((item) => item.id === chat.id)
        if (current && (manualTitlesPending.has(chat.id) || (revisions.get(chat.id) ?? 0) !== titleRevision(chat.id) ||
          (titleTerminal(current) && !titleTerminal(chat)))) return current
        // Creation/rename responses do not all carry meta. Keep only previously known data.
        const next = current && chat.autoTitle === undefined ? { ...chat, autoTitle: current.autoTitle } : chat
        if (current && (current.title !== next.title || (next.autoTitle?.state === 'manual' && current.autoTitle?.state !== 'manual'))) touchTitle(chat.id)
        return next
      })
      for (const chat of merged) unlistedChats.delete(chat.id)
      state.chats = [...unlistedChats.values(), ...merged]
      titleGuard.listRevision++
      state.chatsStatus = 'ready'
    } catch (error) {
      trace?.mark('list_end', { result: 'error' })
      if (disposed || gen !== scopeGen || request !== chatsRequest) return
      state.chatsStatus = 'error'
      state.chatsError = messageOf(error)
    }
  }

  async function setScope(scope: ConversationScope | null): Promise<void> {
    const same = scope && state.scope && scope.projectId === state.scope.projectId && scope.groupId === state.scope.groupId
    if (same || (!scope && !state.scope)) return
    scopeGen += 1
    chatGen += 1
    unlistedChats.clear()
    state.renaming = false
    state.scope = scope ? { ...scope } : null
    state.chats = []
    state.chatsStatus = 'idle'
    state.chatsError = null
    state.activeChatId = null
    state.notice = null
    clearChat()
    state.settings = { ...state.settings, sourceAccessMode: 'read_only', sourceAccessScope: null }
    state.savingSettings = false
    state.pendingSourceMode = scope ? 'read_only' : null
    void loadSettings(scope)
    if (!scope) return
    void loadProviders(scope.projectId)
    const gen = scopeGen
    await reloadChats()
    if (disposed || gen !== scopeGen) return
    // The newest conversation of the Flow opens first.
    const first = state.chats[0]
    if (first) await selectChat(first.id)
  }

  async function readTurns(chatId: string, query: Parameters<ConversationSource['fetchTurns']>[1], trace: SubmissionTrace | undefined, stage: 'history' | 'catchup' | 'older') {
    trace?.mark(`${stage}_start`, { docId: chatId })
    try {
      const page = await source.fetchTurns(chatId, query)
      trace?.mark(`${stage}_end`, { docId: chatId, result: 'ok' })
      return page
    } catch (error) {
      trace?.mark(`${stage}_end`, { docId: chatId, result: 'error' })
      throw error
    }
  }

  // ── Active chat ────────────────────────────────────────────────────────────────────
  async function selectChat(chatId: string, trace?: SubmissionTrace): Promise<void> {
    if (chatId === state.activeChatId && state.turnsStatus !== 'error') {
      if (state.turnsStatus === 'ready') await catchUp()
      return
    }
    // Another chat is not the one a shown notice was about (a reload of the same chat keeps it).
    if (chatId !== state.activeChatId) state.notice = null
    chatGen += 1
    const gen = chatGen
    clearChat()
    state.activeChatId = chatId
    restoreLocalChatErrors(chatId)
    state.pending = outbox.get(ownerKey(chatId)) ?? []
    state.sending = progress.get(ownerKey(chatId))?.phase === 'sending'
    state.turnsStatus = 'loading'
    // flowgate.v02test.0025: this chat's own AI, never the previous chat's; the stored pick follows from the server.
    if (source.fetchChatDocSettings) {
      applyChatProvider(chatProviders.get(ownerKey(chatId)) ?? null)
      void loadChatDocSettings(chatId, gen)
    }
    try {
      // No cursor: the server resumes from the position it remembers (D0002 §3-4).
      const first = await readTurns(chatId, { includeHead: true, limit: TURN_PAGE_SIZE }, trace, 'history')
      if (disposed || gen !== chatGen) return
      applyPage(first, true)
      let cursor = first.nextAfterSeq
      for (let round = 0; cursor != null && round < CATCHUP_MAX_ROUNDS; round++) {
        const page = await readTurns(chatId, { afterSeq: cursor, limit: TURN_PAGE_SIZE }, trace, 'catchup')
        if (disposed || gen !== chatGen) return
        applyPage(page)
        cursor = page.nextAfterSeq
      }
      state.hasMoreBefore = oldestSeq() > 1
      if (state.hasMoreBefore && state.turns.length < PREPEND_PAGE_SIZE) await loadOlder(trace)
      if (disposed || gen !== chatGen) return
      state.turnsStatus = 'ready'
    } catch (error) {
      if (disposed || gen !== chatGen) return
      state.turnsStatus = 'error'
      state.turnsError = messageOf(error)
      return
    }
    void openActivities(chatId, gen).then(() => restoreRuns(chatId, gen))
    void adoptActiveRun(chatId, gen, ACTIVE_RUN_ADOPT_ATTEMPTS)
    // A signal that came while the chat was opening (after its pages were read, possibly) is served now.
    flushDeferredRefresh()
  }

  async function openActivities(chatId: string, gen: number): Promise<void> {
    await loadActivities(chatId, gen)
    if (disposed || gen !== chatGen || !source.supportsActivities || !source.subscribeActivities) return
    unsubscribe = source.subscribeActivities(chatId, (activity) => {
      if (!disposed && gen === chatGen) state.activities = upsertActivity(state.activities, activity)
    })
  }

  /**
   * One full read of the chat's command requests and run changes (D0006 §3.9.2).
   * flowgate.v02.0151 T-A2 (NR §2.6-3 ④⑤): true once the server answered — applied, or dropped because a newer read
   * or a decision already answered after it started, or because the chat / generation is gone — and false when the
   * read failed. A failed read changes nothing on screen.
   */
  async function loadActivities(chatId: string, gen: number): Promise<boolean> {
    if (!source.supportsActivities) return true
    activityReads += 1
    const read = activityReads
    try {
      const list = await source.listActivities(chatId)
      if (!isCurrentChat(chatId, gen) || read <= activityApplied || read <= activityStaleUpTo) return true
      activityApplied = read
      state.activities = replaceServerActivities(state.activities, list, state.deciding)
      return true
    } catch {
      // Activities are an addition to the conversation of record: what is shown stays and the
      // next polling tick (or reopening the chat) reads again (D0006 §6.2).
      return false
    }
  }

  /**
   * flowgate.v02.0151 T-A2 (NR §2.6-3 ⑤): the read once a run ended (or was given up) is tried up to
   * FINAL_ACTIVITY_READ_ATTEMPTS times, RUN_POLL_MS apart, for the chat and generation it was for. When every try
   * fails nothing is removed; the next catch-up (coming back to the panel, the same chat opened again) reads again.
   */
  async function loadFinalActivities(chatId: string, gen: number): Promise<void> {
    for (let attempt = 1; ; attempt++) {
      if (!isCurrentChat(chatId, gen)) return
      if (await loadActivities(chatId, gen) || attempt >= FINAL_ACTIVITY_READ_ATTEMPTS) return
      await sleep(RUN_POLL_MS)
    }
  }

  /** The page read in flight, so a caller that needs it finished (revealCheckpoint) can wait for it. */
  let olderLoad: Promise<void> | null = null

  function loadOlder(trace?: SubmissionTrace): Promise<void> {
    if (state.loadingOlder && olderLoad) return olderLoad
    const load = loadOlderPage(trace)
    olderLoad = load
    void load.finally(() => {
      if (olderLoad === load) olderLoad = null
    })
    return load
  }

  async function loadOlderPage(trace?: SubmissionTrace): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || state.loadingOlder) return
    const before = oldestSeq()
    if (before <= 1) {
      state.hasMoreBefore = false
      return
    }
    const gen = chatGen
    state.loadingOlder = true
    try {
      const page = await readTurns(chatId, { beforeSeq: before, limit: PREPEND_PAGE_SIZE }, trace, 'older')
      if (disposed || gen !== chatGen) return
      applyPage(page)
      state.hasMoreBefore = page.prevBeforeSeq != null || oldestSeq() > 1
    } catch {
      // Keep what is on screen; the [이전 대화] button stays to try again.
    } finally {
      if (gen === chatGen) state.loadingOlder = false
    }
  }

  /**
   * flowgate.v02test.0025: under 체크포인트 사용 the checkpoint message is always on screen, so the
   * "▼ 여기부터 전송됩니다" line is drawn right next to it. When the chat opens on its recent messages only (or the
   * mode is switched on later), older pages are read until the checkpoint message is loaded. Nothing is drawn for a
   * message that is not on screen. A failed read stops here; the [이전 대화] button stays to try again.
   */
  let revealingCheckpoint = false

  function checkpointOffScreen(): boolean {
    const seq = state.checkpointSeq
    return state.settings.contextMode === 'checkpoint' && seq != null && state.turnsStatus === 'ready' && state.hasMoreBefore && oldestSeq() > seq
  }

  async function revealCheckpoint(): Promise<void> {
    if (revealingCheckpoint || !checkpointOffScreen()) return
    const gen = chatGen
    revealingCheckpoint = true
    try {
      while (!disposed && gen === chatGen && checkpointOffScreen()) {
        const before = oldestSeq()
        await loadOlder()
        if (disposed || gen !== chatGen) return
        // The read that was already out may have been someone else's; read again until one adds nothing.
        if (oldestSeq() >= before && !state.loadingOlder) return
      }
    } finally {
      revealingCheckpoint = false
    }
  }

  const stopRevealWatch = watch(
    () => [state.activeChatId, state.turnsStatus, state.settings.contextMode, state.checkpointSeq] as const,
    () => void revealCheckpoint(),
  )

  /** Explicit completion contract: errors / a remaining cursor are never a completed read. */
  async function readTurnPages(chatId: string, afterSeq: number, current: () => boolean): Promise<TurnReadResult> {
    let cursor: number | null = afterSeq
    for (let round = 0; round < CATCHUP_MAX_ROUNDS; round++) {
      if (!current()) return { status: 'discarded', nextAfterSeq: cursor }
      let page
      try {
        // Reuse the existing head contract once per refresh to keep the participant strip current.
        page = await source.fetchTurns(chatId, { afterSeq: cursor, limit: TURN_PAGE_SIZE, ...(round === 0 ? { includeHead: true } : {}) })
      } catch {
        return { status: current() ? 'failed' : 'discarded', nextAfterSeq: cursor }
      }
      if (!current()) return { status: 'discarded', nextAfterSeq: cursor }
      applyPage(page, round === 0)
      const next = page.nextAfterSeq
      if (next == null) return { status: 'complete', nextAfterSeq: null }
      if (next <= cursor) return { status: 'limited', nextAfterSeq: next }
      cursor = next
    }
    return { status: 'limited', nextAfterSeq: cursor }
  }

  async function finalTurnRead(chatId: string, current: () => boolean, afterSeq = 0): Promise<TurnReadResult> {
    // A known attributed body only needs a final tail read. Otherwise start at zero to
    // cover replies before the saved read position. Only that full range certifies absence.
    for (let attempt = 0; attempt < FINAL_TURN_READ_ATTEMPTS; attempt++) {
      if (!current()) return { status: 'discarded', nextAfterSeq: null }
      const result = await readTurnPages(chatId, afterSeq, current)
      if (!current()) return { status: 'discarded', nextAfterSeq: result.nextAfterSeq }
      if (result.status !== 'failed' || attempt === FINAL_TURN_READ_ATTEMPTS - 1) return result
      await sleep(RUN_POLL_MS)
      if (!current()) return { status: 'discarded', nextAfterSeq: result.nextAfterSeq }
    }
    return { status: 'failed', nextAfterSeq: null }
  }

  /**
   * Fill a tail gap, re-read the activities, then repair terminal evidence / a failed history lookup on reconnect.
   * flowgate.v02.0151 T-A2 (NR §2.6-3 ①③④): the activities are read between the turns and the run history — the
   * order selectChat uses, since restoreRuns takes candidate runs from the activities — so every caller (the same chat
   * selected again, a POST that finished after a switch, the panel coming back into view) also brings late run
   * changes and command requests. Every step is for the chat and generation the catch-up started on; an older read
   * answering late is dropped by the activity read numbers and by restoreRuns' own request number.
   */
  async function catchUp(): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || disposed) return
    const gen = chatGen
    const current = () => isCurrentChat(chatId, gen)
    const result = await readTurnPages(chatId, state.turns.at(-1)?.seq ?? 0, current)
    if (!current() || result.status === 'discarded') return
    await loadActivities(chatId, gen)
    if (!current()) return
    await restoreRuns(chatId, gen)
  }

  // ── Recovery after a signal (flowgate.v02test.0013) ────────────────────────────────
  /** The poll of the open chat's tracked run, when one is running. */
  function currentPoll(chatId: string, gen: number): LivePoll | null {
    const poll = livePoll
    return poll && poll.chatId === chatId && poll.gen === gen && state.runId === poll.runId ? poll : null
  }

  /** Cuts the poll's wait short: its next tick reads the run, the turns and the activities now. */
  function wakePoll(poll: LivePoll): void {
    poll.due = true
    poll.wake?.()
  }

  /** A signal is held while the chat is opening or one of its messages is on its way to the server. */
  function mustDefer(): boolean {
    return state.turnsStatus === 'loading' || state.pending.some((p) => !p.failed)
  }

  function flushDeferredRefresh(): void {
    const held = deferredRefresh
    if (!held || held.gen !== chatGen || disposed || mustDefer()) return
    deferredRefresh = null
    void recover(held.parts)
  }

  function recover(parts: ChatRefresh = FULL_CHAT_REFRESH): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || disposed) return Promise.resolve()
    const gen = chatGen
    if (mustDefer()) {
      deferredRefresh = { gen, parts: mergeChatRefresh(deferredRefresh?.gen === gen ? deferredRefresh.parts : null, parts) }
      return Promise.resolve()
    }
    // A chat whose first read failed shows its own [다시 시도]; nothing to bring up to date.
    if (state.turnsStatus !== 'ready') return Promise.resolve()
    const ongoing = recovery
    if (ongoing && ongoing.chatId === chatId && ongoing.gen === gen) {
      ongoing.wanted = mergeChatRefresh(ongoing.wanted, parts)
      return ongoing.done
    }
    const entry: Recovery = { chatId, gen, wanted: mergeChatRefresh(null, parts), done: Promise.resolve() }
    recovery = entry
    entry.done = (async () => {
      try {
        while (entry.wanted && isCurrentChat(chatId, gen)) {
          const wanted = entry.wanted
          entry.wanted = null
          await recoverOnce(chatId, gen, wanted)
        }
      } finally {
        if (recovery === entry) recovery = null
      }
    })()
    return entry.done
  }

  async function recoverOnce(chatId: string, gen: number, wanted: ChatRefresh): Promise<void> {
    const current = () => isCurrentChat(chatId, gen)
    // A run started elsewhere (another window, a resend that was answered late) or missed when the chat opened.
    if (wanted.run && !running.value) await adoptActiveRun(chatId, gen, 1)
    if (!current()) return
    // The poll reads the turns and activities on every tick and ends the run with its own final reads.
    const poll = currentPoll(chatId, gen)
    if (poll) {
      wakePoll(poll)
      return
    }
    if (wanted.turns) {
      const result = await readTurnPages(chatId, state.turns.at(-1)?.seq ?? 0, current)
      if (!current() || result.status === 'discarded') return
    }
    if (wanted.activities) {
      await loadActivities(chatId, gen)
      if (!current()) return
    }
    // A run being started or polled owns its own end; the history is read once nothing is.
    if (wanted.history && !running.value) await restoreRuns(chatId, gen)
  }

  /** Existing group history has no cursor. At most two windows and fifty detail reads. */
  async function restoreRuns(chatId: string, gen: number): Promise<void> {
    if (!isCurrentChat(chatId, gen) || !state.scope) return
    const request = ++historyRequest
    const scope = { ...state.scope }
    const current = () => isCurrentChat(chatId, gen) && request === historyRequest
    let uncertain = false
    const candidates = new Set<string>()
    const listedLiveChats = new Map<string, string>()
    try {
      let list = await source.listRuns(scope, RUN_HISTORY_INITIAL_LIMIT)
      if (!current()) return
      if (list.hasMore) {
        list = await source.listRuns(scope, RUN_HISTORY_MAX_LIMIT)
        if (!current()) return
      }
      uncertain = list.hasMore
      for (const item of list.items) if (item.docRef === chatId) {
        candidates.add(item.runId)
        listedLiveChats.set(item.runId, item.docRef)
      }
    } catch {
      if (!current()) return
      uncertain = true
    }
    // Include referenced runs outside the list window, without guessing an id from a badge / time.
    for (const turn of state.turns) if (turn.sourceRunId) candidates.add(turn.sourceRunId)
    for (const activity of state.activities) if (activity.runId) candidates.add(activity.runId)
    for (const runId of terminalRuns.keys()) candidates.add(runId)
    if (candidates.size > RUN_HISTORY_DETAIL_LIMIT) uncertain = true
    const confirmed: AiRunState[] = []
    for (const runId of [...candidates].slice(0, RUN_HISTORY_DETAIL_LIMIT)) {
      if (!current()) return
      if (runId === state.runId) continue // history never owns the active-run lock
      const check = (runChecks.get(runId) ?? 0) + 1
      runChecks.set(runId, check)
      try {
        const run = await source.fetchRun(runId)
        if (!current()) return
        if (runId === state.runId || check !== runChecks.get(runId)) continue
        if (!runBelongsToChat(run, runId, chatId, listedLiveChats.get(runId) ?? null)) { uncertain = true; continue }
        if (run.status === 'running') continue
        terminalRuns.set(runId, run)
        confirmed.push(run)
      } catch {
        if (!current()) return
        uncertain = true
      }
    }
    // Do not make an already-proven reply depend on covering unrelated older history.
    // Each run belongs to exactly one read group, so its failed reads still total at most 3.
    const withBody = confirmed.filter((run) => runBodyState(state.turns, run.runId, 'failed') === 'present')
    const withoutBody = confirmed.filter((run) => !withBody.includes(run))
    for (const group of [withBody, withoutBody]) {
      if (!group.length) continue
      const afterSeq = group === withBody ? state.turns.at(-1)?.seq ?? 0 : 0
      const result = await finalTurnRead(chatId, current, afterSeq)
      if (!current() || result.status === 'discarded') return
      for (const run of group) {
        if (run.runId !== state.runId && terminalRuns.get(run.runId) === run) renderTerminal(chatId, run, result)
      }
    }
    if (current()) showHistoryUnconfirmed(chatId, uncertain)
  }

  async function renameActiveChat(title: string): Promise<boolean> {
    const chat = activeChat.value
    const next = title.trim()
    if (!chat || !next || (next === chat.title && chat.autoTitle?.state !== 'owed') || state.renaming) return false
    state.renaming = true
    const chatId = chat.id
    const gen = chatGen
    const scope = scopeGen
    const userId = draftUserId()
    touchTitle(chatId) // A manual intent invalidates an already pending automatic response.
    manualTitlesPending.add(chatId)
    try {
      const saved = await source.renameChat(chatId, next)
      if (disposed || scope !== scopeGen || userId !== draftUserId()) return false
      setChatTitle(chatId, saved.title, { state: 'manual', provisionalTitle: chat.autoTitle?.provisionalTitle ?? chat.title })
      return true
    } catch (error) {
      notifyIfCurrent(gen, 'danger', 'rename_failed', messageOf(error))
      return false
    } finally {
      manualTitlesPending.delete(chatId)
      if (!disposed && scope === scopeGen && userId === draftUserId()) state.renaming = false
    }
  }

  /**
   * D0006 §6.3: a standalone CH. Nothing is added before the server answers; on success the
   * list is read again and the new chat opens. A failure keeps the open chat, says why, and
   * never retries on its own — the button stays for another try.
   */
  async function createChat(title: string, options?: CreateChatOptions): Promise<boolean>
  async function createChat(title: string, options: CreateChatOptions | undefined, context: CreateChatContext): Promise<CreateChatResult>
  async function createChat(title: string, options?: CreateChatOptions, context?: CreateChatContext): Promise<boolean | CreateChatResult> {
    const scope = state.scope ? { ...state.scope } : null
    let createdId: string | null = null
    let intent: string | null = null
    const finish = (status: 'cancelled' | 'failed'): false | CreateChatResult => context
      ? { status, chatId: createdId, scope, draftIntentId: context.draftIntentId, createIntentId: intent }
      : false
    const next = title.trim()
    if (!scope || !next || state.creatingChat) return finish('failed')
    state.creatingChat = true
    const gen = scopeGen
    const draftGen = chatGen
    // flowgate.v02.0135 T#3: a try of the same draft re-sends its intent; the server answers the chat an earlier try
    // made (its answer lost, the chat committed late) instead of making another one.
    if (!createIntent || createIntent.scopeGen !== gen || createIntent.chatGen !== draftGen) {
      createIntent = { key: context?.createIntentId ?? newCreateIntent(), scopeGen: gen, chatGen: draftGen }
    }
    intent = createIntent.key
    const currentScope = () => !disposed && gen === scopeGen && state.scope?.projectId === scope.projectId && state.scope?.groupId === scope.groupId
    const currentDraft = () => currentScope() && draftGen === chatGen && (!context || context.isCurrent())
    const operation = ++createOperation
    // Release only the departing owner's progress flag at the switch itself. An old
    // continuation must not clear a new owner's in-flight creation when it completes.
    const stopCreatingWatch = watch(
      () => currentScope() && (context ? context.isCurrent() : (draftGen === chatGen || state.activeChatId === createdId)),
      (current) => { if (!current && operation === createOperation) state.creatingChat = false },
      { flush: 'sync' },
    )
    try {
      let created: ChatSummary
      try {
        context?.trace?.mark('create_start')
        created = await source.createChat(scope, next, { ...(options?.autoTitle ? { autoTitle: true } : {}), clientRequestId: intent })
        context?.trace?.mark('create_end', { docId: created.id, result: 'ok' })
      } catch (error) {
        context?.trace?.mark('create_end', { result: 'error' })
        // Only the Flow and the draft the create was for show its failure (0135 T#2).
        if (!disposed && gen === scopeGen && draftGen === chatGen) notifyCreateFailed(error)
        // flowgate.v02.0135 T#3: without an answer the server may still have made the chat (on stg it did, minutes
        // later): the Flow's list is read again so that chat shows instead of the next try making another one.
        if (!disposed && gen === scopeGen && isNoResponse(error)) void reloadChats()
        return finish('failed')
      }
      if (options?.autoTitle && created.autoTitle === undefined) {
        created = { ...created, autoTitle: { state: 'owed', provisionalTitle: next } }
      }
      createdId = created.id
      if (createIntent?.key === intent) createIntent = null
      if (!currentDraft()) return finish('cancelled')
      // flowgate.v02test.0025: the AI picked in the draft becomes the new chat's own pick.
      const draftPick = state.selectedProviderId
      if (draftPick && source.saveChatDocSettings) {
        chatProviders.set(ownerKey(created.id), draftPick)
        persistChatProvider(created.id, draftPick)
      }
      // A list read that failed (or does not show it yet) still gets the chat the server made.
      if (!state.chats.some((c) => c.id === created.id)) {
        unlistedChats.set(created.id, created)
        state.chats = [created, ...state.chats]
      }
      const result: CreatedChat = { status: 'created', chatId: created.id, scope, draftIntentId: context?.draftIntentId ?? null, createIntentId: intent }
      context?.onCreated(result)
      context?.onLoading?.()
      const selection = selectChat(created.id, context?.trace)
      const selectedGen = chatGen
      await selection
      if (!currentScope() || selectedGen !== chatGen || state.activeChatId !== created.id || (context && !context.isCurrent())) return finish('cancelled')
      if (state.turnsStatus === 'error') return finish('failed')
      // Standalone creation still refreshes; a first send refreshes only after POST starts.
      if (!context) void reloadChats()
      return context ? result : true
    } finally {
      stopCreatingWatch()
      if (operation === createOperation && currentScope() && (!context || context.isCurrent())) state.creatingChat = false
    }
  }

  // flowgate.v02.0047: a new chat is a draft until its first send (chatStart.ts creates it then).
  function startDraft(): void {
    chatGen += 1
    state.notice = null
    clearChat()
    state.activeChatId = null
    // flowgate.v02test.0025: a new chat starts on my default AI, not on the AI of the chat left behind.
    if (source.fetchChatDocSettings) applyChatProvider(null)
  }

  function setChatTitle(chatId: string, title: string, marker?: ChatAutoTitle): void {
    if (disposed) return
    touchTitle(chatId)
    const update = (chat: ChatSummary): ChatSummary => ({ ...chat, title,
      autoTitle: marker ?? { state: 'done', provisionalTitle: chat.autoTitle?.provisionalTitle ?? chat.title } })
    const unlisted = unlistedChats.get(chatId)
    if (unlisted) unlistedChats.set(chatId, update(unlisted))
    state.chats = state.chats.map((chat) => chat.id === chatId ? update(chat) : chat)
  }

  function notifyCreateFailed(error: unknown): void {
    const status = error instanceof ConversationRequestError ? error.status : null
    const code = error instanceof ConversationRequestError ? error.code : null
    // 403 and a disposed group are the readOnly meaning: nothing to try again.
    if (status === 403) notify('warning', 'create_forbidden')
    else if (code === 'conversation_group_disposed') notify('warning', 'create_group_disposed')
    else notify('danger', 'create_failed', messageOf(error))
  }

  // ── Send ───────────────────────────────────────────────────────────────────────────
  /** True while the chat opened as generation `gen` is still the one on screen. */
  function isCurrentChat(chatId: string, gen: number): boolean {
    return !disposed && gen === chatGen && chatId === state.activeChatId
  }

  /**
   * POST one turn; every replay keeps its original CH, body, head and key. True when it was accepted, else why the
   * last attempt failed (flowgate.v02gui.0007 A: shown on the failed bubble).
   */
  async function postPending(chatId: string, gen: number, key: string, item: PendingTurn, trace: SubmissionTrace, refresh = false): Promise<true | SendFailure> {
    let failure = sendFailureOf(null)
    const userId = draftUserId()
    // flowgate.v02gui.0009: a send belongs to its chat, not to the screen. Moving to another chat or Flow neither stops
    // it nor its replays (same CH, body and idempotency key: the server stores the turn once); only a disposed session
    // or another signed-in user does.
    const ended = () => disposed || userId !== draftUserId()
    for (let attempt = 0; attempt <= SEND_RETRY_MAX; attempt++) {
      if (attempt > 0 && ended()) return failure
      publishProgress(key, { phase: 'sending', stage: 'send', attempt: attempt + 1 })
      try {
        trace.mark('turn_start', { docId: chatId, attempt: attempt + 1 })
        const posting = source.postTurn(chatId, { body: item.body, idempotencyKey: item.idempotencyKey, basedOnSeq: item.basedOnSeq, traceId: trace.id })
        if (refresh && attempt === 0) void reloadChats(trace)
        const res = await posting
        trace.mark('turn_end', { docId: chatId, attempt: attempt + 1, result: 'ok' })
        publishOutbox(key, (outbox.get(key) ?? []).filter((p) => p.localId !== item.localId))
        traces.delete(item.localId)
        if (!isCurrentChat(chatId, gen)) {
          if (visibleOwner(key)) void catchUp()
          return true
        }
        if (res.turn) state.turns = mergeTurns(state.turns, [res.turn])
        state.headSeq = Math.max(state.headSeq, res.headSeq)
        trace.frame('accepted_frame', chatId)
        return true
      } catch (error) {
        const status = error instanceof ConversationRequestError ? error.status : null
        trace.mark('turn_end', { docId: chatId, attempt: attempt + 1, result: 'error', ...(status === null ? {} : { status }) })
        failure = sendFailureOf(status)
        if ((status != null && [400, 404, 409, 422].includes(status)) || attempt === SEND_RETRY_MAX || ended()) {
          recordChatError(chatId, gen, 'send_failed', error, true, !ended())
          return failure
        }
      }
    }
    return failure
  }

  /**
   * D0005 §3-2: the send-time action runs once, only after the turn was accepted, and only
   * for the chat the turn went to. After a switch to another chat it is dropped: a mention
   * or an AI run must never land in a chat where no turn was just sent.
   *
   * flowgate.v02.0142 T#1: the AI run uses `providerId`, the AI pinned when the message was sent — never the picker's
   * current value. Without a usable one startAiRun ends with the `no_provider` notice (the turn itself stays stored).
   */
  function runSendAction(chatId: string, gen: number, trace: SubmissionTrace, providerId: string): void {
    if (!isCurrentChat(chatId, gen)) return
    if (state.settings.sendAction === 'copy_mention') options.onAutoCopyMention?.()
    else if (state.settings.sendAction === 'invoke_ai') {
      trace.mark('ai_start', { docId: chatId })
      void startAiRun(providerId)
    }
  }

  async function send(text: string, target?: SendTarget, trace = createSubmissionTrace(), snapshot?: DraftSendSnapshot): Promise<boolean> {
    const created = target && 'status' in target ? target : undefined
    if (created && disposed) return false
    // A first-send continuation only ever addresses the CH it made, in that CH's own Flow. flowgate.v02gui.0009: when
    // the screen moved on (another Flow, chat or draft) while the chat was being made or opened — or came back while it
    // opens again — the message still goes to that CH: off screen, never into the chat open now, shown once it is open.
    const inScope = !!created && state.scope?.projectId === created.scope.projectId && state.scope?.groupId === created.scope.groupId
    const offScreen = !!created && !(inScope && state.activeChatId === created.chatId && state.turnsStatus === 'ready')
    const chatId = created?.chatId ?? state.activeChatId
    const body = text.trim()
    if (!chatId || !body) return false
    if (!offScreen && (state.sending || running.value || state.turnsStatus !== 'ready')) return false
    const owner = created && offScreen ? scopedOwnerKey(created.scope, chatId) : ownerKey(chatId)
    if (progress.get(owner)?.phase === 'sending') return false
    const receipt = retryReceipts.get(owner)
    retryReceipts.delete(owner)
    if (receipt && snapshot && receipt.body.trim() === body && receipt.body === snapshot.body && receipt.revision === snapshot.revision &&
      receipt.owner.userId === snapshot.owner.userId && receipt.owner.projectId === snapshot.owner.projectId &&
      receipt.owner.groupId === snapshot.owner.groupId && receipt.owner.intentId === snapshot.owner.intentId) return true
    // Composer resubmission of a failed body reuses its bubble, never a new key.
    const failed = (outbox.get(owner) ?? []).find((p) => p.body === body)
    if (failed) {
      if (snapshot) pendingDrafts.set(failed.localId, { ...snapshot, owner: { ...snapshot.owner } })
      if (!offScreen) return retry(failed.localId, true)
      publishOutbox(owner, (outbox.get(owner) ?? []).map((p) => p.localId === failed.localId ? { ...p, failed: false, failure: null } : p))
      publishProgress(owner, { phase: 'sending', stage: 'send', attempt: 1 })
      return finishSend(chatId, chatGen, owner, failed, traces.get(failed.localId) ?? trace)
    }
    const gen = chatGen
    const key = newIdempotencyKey()
    // flowgate.v02.0142 T#1: pinned in the same synchronous step as chatId / gen / key, before the first await.
    const providerId = target?.providerId ?? state.selectedProviderId
    // flowgate.v02gui.0009: off screen nothing of the new CH was read; 0 is its head (always <= the server's).
    const basedOnSeq = offScreen ? 0 : state.headSeq
    const item: PendingTurn = { localId: key, idempotencyKey: key, body, basedOnSeq, createdAt: new Date().toISOString(), failed: false, providerId }
    traces.set(key, trace)
    if (snapshot) pendingDrafts.set(key, { ...snapshot, owner: { ...snapshot.owner } })
    publishOutbox(owner, [...(outbox.get(owner) ?? []), item])
    publishProgress(owner, { phase: 'sending', stage: 'send', attempt: 1 })
    trace.frame('pending_frame', chatId)
    // The Flow shown now reads its list again only when it is the CH's own Flow.
    return finishSend(chatId, gen, owner, item, trace, inScope)
  }

  async function finishSend(chatId: string, gen: number, owner: string, item: PendingTurn, trace: SubmissionTrace, refresh = false): Promise<boolean> {
    const result = await postPending(chatId, gen, owner, item, trace, refresh)
    if (result !== true) {
      publishOutbox(owner, (outbox.get(owner) ?? []).map((p) => p.localId === item.localId ? { ...p, failed: true, failure: result } : p))
      publishProgress(owner, { phase: 'failed', stage: 'send', attempt: progress.get(owner)?.attempt ?? 1 })
      if (visibleOwner(owner)) flushDeferredRefresh()
      return false
    }
    pendingDrafts.delete(item.localId)
    publishProgress(owner, null)
    runSendAction(chatId, gen, trace, item.providerId)
    // flowgate.v02test.0013: a signal held while this message was on its way is served once it is stored.
    if (visibleOwner(owner)) flushDeferredRefresh()
    return true
  }

  async function retry(localId: string, fromComposer = false): Promise<boolean> {
    const chatId = state.activeChatId
    const item = state.pending.find((p) => p.localId === localId)
    if (!chatId || !item || state.sending || running.value || state.turnsStatus !== 'ready') return false
    const owner = ownerKey(chatId)
    if (progress.get(owner)?.phase === 'sending') return false
    publishOutbox(owner, (outbox.get(owner) ?? []).map((p) => p.localId === localId ? { ...p, failed: false, failure: null } : p))
    publishProgress(owner, { phase: 'sending', stage: 'send', attempt: 1 })
    const gen = chatGen
    const snapshot = pendingDrafts.get(localId)
    // The first attempt failed before its send action, so the accepted retry runs it once — on the AI pinned at the
    // first send (flowgate.v02.0142 T#1, `item.providerId` in finishSend), not on the selection of this moment.
    const ok = await finishSend(chatId, gen, owner, item, traces.get(localId) ?? createSubmissionTrace())
    if (ok && !fromComposer && snapshot) retryReceipts.set(owner, snapshot)
    return ok
  }

  async function reportRead(seq: number): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || seq <= 0 || seq <= state.readSeq || seq <= reportedRead) return
    const gen = chatGen
    reportedRead = seq
    try {
      const confirmed = await source.markRead(chatId, seq)
      // A late answer for a chat that is no longer open is dropped.
      if (!isCurrentChat(chatId, gen)) return
      // The boundary only moves forward and the server's value wins.
      if (confirmed != null) state.readSeq = Math.max(state.readSeq, confirmed)
    } catch {
      // Only this chat's own report is released for another try.
      if (isCurrentChat(chatId, gen) && reportedRead === seq) reportedRead = 0
    }
  }

  // ── Command requests ───────────────────────────────────────────────────────────────
  /**
   * D0006 §6.2. Only a pending request the user may still decide is sent. The optimistic
   * approving / rejecting state lives only while the call is in flight: a success replaces the
   * card with the server's request, any failure puts the card back first, and either way the
   * activity is read once more so the card ends on the server's state.
   */
  async function decideCommand(commandId: string, decision: CommandDecision): Promise<void> {
    const chatId = state.activeChatId
    const current = state.activities.find((a): a is CommandRequestActivity => a.kind === 'command' && a.id === commandId)
    if (!chatId || !current || current.state !== 'pending_approval' || current.decisionForbidden || state.deciding.includes(commandId)) return
    const gen = chatGen
    state.deciding = [...state.deciding, commandId]
    state.activities = upsertActivity(state.activities, { ...current, state: decision === 'approve' ? 'approving' : 'rejecting' })
    try {
      const decided = await source.decideCommand(chatId, commandId, decision)
      if (!isCurrentChat(chatId, gen)) return
      state.activities = upsertActivity(state.activities, decided)
    } catch (error) {
      if (!isCurrentChat(chatId, gen)) return
      const status = error instanceof ConversationRequestError ? error.status : null
      if (status === 403) {
        // Not this user's request to decide: the server state stays, the buttons go.
        state.activities = upsertActivity(state.activities, { ...current, decisionForbidden: true })
        notify('warning', 'command_decision_forbidden')
      } else {
        state.activities = upsertActivity(state.activities, current)
        // 409: someone or something already settled it — the re-read shows how; no retry.
        // 404: the re-read drops the card if the server no longer has it.
        // Network / 5xx: the card is pending again and can be pressed again.
        if (status === 409) notify('info', 'command_already_handled')
        else if (status !== 404) recordChatError(chatId, gen, 'command_decision_failed', error)
      }
    } finally {
      state.deciding = state.deciding.filter((id) => id !== commandId)
      activityStaleUpTo = activityReads
    }
    if (isCurrentChat(chatId, gen)) await loadActivities(chatId, gen)
  }

  // ── Chat AI run (D0005 §3-1, L0008, 0264 STOP) ─────────────────────────────────────
  function selectProvider(providerId: string): void {
    if (!state.providers.some((p) => p.id === providerId)) return
    const changed = state.selectedProviderId !== providerId
    state.selectedProviderId = providerId
    const chatId = state.activeChatId
    // flowgate.v02test.0025: stored as this chat's pick; a draft keeps it until its first send makes the chat.
    if (!chatId || !source.saveChatDocSettings) return
    const key = ownerKey(chatId)
    if (!changed && chatProviders.get(key) === providerId) return
    chatProviders.set(key, providerId)
    persistChatProvider(chatId, providerId)
  }

  async function setCheckpoint(seq: number | null, kind: CheckpointKind = 'include'): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || !source.saveChatDocSettings || state.savingCheckpoint) return
    if (seq === state.checkpointSeq && (seq == null || kind === state.checkpointKind)) return
    const gen = chatGen
    state.savingCheckpoint = true
    try {
      const saved = await source.saveChatDocSettings(
        chatId,
        seq == null ? { context_checkpoint_seq: null } : { context_checkpoint_seq: seq, context_checkpoint_kind: kind },
      )
      if (isCurrentChat(chatId, gen)) {
        state.checkpointSeq = saved.checkpointSeq
        state.checkpointKind = saved.checkpointKind
      }
    } catch (error) {
      if (isCurrentChat(chatId, gen)) notify('danger', 'checkpoint_failed', messageOf(error))
    } finally {
      if (isCurrentChat(chatId, gen)) state.savingCheckpoint = false
    }
  }

  /**
   * flowgate.v02.0151 T-A2 (NR §2.6-2): whether the server still lists `runId` as the chat's active run (`GET
   * /ai-invoke/active`) — true / false, or null when that query did not answer.
   */
  async function serverStillRuns(chatId: string, runId: string): Promise<boolean | null> {
    try {
      return (await source.fetchActiveChatRun(chatId)) === runId
    } catch {
      return null
    }
  }

  async function pollRun(chatId: string, runId: string, gen: number): Promise<void> {
    if (!isCurrentChat(chatId, gen)) return
    // flowgate.v02test.0013: the run is polled already (an adoption racing a start's answer): one poll per run.
    if (currentPoll(chatId, gen)?.runId === runId) return
    const poll: LivePoll = { chatId, gen, runId, due: false, wake: null }
    livePoll = poll
    try {
      await trackRun(chatId, runId, gen, poll)
    } finally {
      if (livePoll === poll) livePoll = null
    }
  }

  async function trackRun(chatId: string, runId: string, gen: number, poll: LivePoll): Promise<void> {
    state.runId = runId
    // flowgate.v02test.0024 D: an adopted run (another tab, F5, a 409 run_in_progress) shows its line at once; the
    // AI name and the access come with its first detail read.
    if (!state.runDisplay) state.runDisplay = { providerName: null, access: null }
    trackedRunId = runId
    runChecks.set(runId, (runChecks.get(runId) ?? 0) + 1)
    const current = () => isCurrentChat(chatId, gen) && state.runId === runId
    // RUN_POLL_MS between ticks, cut short by a signal (wakePoll); a signal during a tick skips the next wait.
    const wait = async (): Promise<void> => {
      if (!poll.due) {
        await new Promise<void>((resolve) => {
          poll.wake = resolve
          void sleep(RUN_POLL_MS).then(resolve)
        })
      }
      poll.wake = null
      poll.due = false
    }
    // 0084 registration grace remains independent of response / completion evidence.
    let seen = false
    // The server's running detail has group/action scope but no doc_ref. Only its
    // active endpoint can prove the exact CH/run binding until finished detail arrives.
    let liveMembershipConfirmed = false
    let unseenGone = 0
    const unseenGoneMax = Math.ceil(RUN_ADOPT_GRACE_MS / RUN_POLL_MS)
    // flowgate.v02.0151 T-A2 (NR §2.6-2): running out of polls (RUN_POLL_MAX), a 404/410 for a run already read (or
    // never registered within the grace) and a detail that does not prove this chat no longer end the run by
    // themselves. The server's active run of the chat is asked first: while it is this run the lock stays and the
    // polling goes on, the budget growing by RUN_POLL_MAX; an unanswered active query is asked again on the next
    // poll, FINAL_TURN_READ_ATTEMPTS times at most. When the budget ran out and the server no longer lists the run,
    // one more detail read decides — normally the finish, judged like any other — and only a run still not terminal
    // then is given up (run_result_unconfirmed).
    let startedAt: string | null = null
    let polls = 0
    let budget = RUN_POLL_MAX
    let unanswered = 0
    let lastRead = false
    const stillActive = async (): Promise<boolean> => {
      const active = await serverStillRuns(chatId, runId)
      if (active === true) {
        unanswered = 0
        lastRead = false
        budget = Math.max(budget, polls + RUN_POLL_MAX)
        return true
      }
      if (active === null && ++unanswered < FINAL_TURN_READ_ATTEMPTS) {
        budget = Math.max(budget, polls + 1)
        return true
      }
      return false
    }
    for (;;) {
      if (!current()) return
      if (polls >= budget) {
        if (lastRead) break
        const active = await stillActive()
        if (!current()) return
        if (!active) {
          lastRead = true
          budget = polls + 1
        }
      }
      polls += 1
      await wait()
      if (!current()) return
      let run
      try {
        run = await source.fetchRun(runId)
      } catch (error) {
        if (!current()) return
        const status = error instanceof ConversationRequestError ? error.status : null
        if (status === 404 || status === 410) {
          unseenGone += 1
          if (seen || unseenGone >= unseenGoneMax) {
            const active = await stillActive()
            if (!current()) return
            if (!active) break
          }
          continue
        }
        await loadActivities(chatId, gen)
        if (!current()) return
        continue
      }
      if (!current()) return
      seen = true
      startedAt = run.startedAt ?? startedAt
      if (!runBelongsToChat(run, runId, chatId)) {
        const liveWithoutDoc = run.status === 'running' && run.runId === runId && run.docRef == null &&
          run.actionScope === 'chat' && run.groupId === chatId.split('.').slice(0, 3).join('.')
        if (!liveWithoutDoc) {
          // The detail is not applied; the run ends here only when the server no longer lists it as active either.
          const active = await stillActive()
          if (!current()) return
          if (!active) break
          continue
        }
        if (!liveMembershipConfirmed) {
          let active: string | null
          try {
            active = await source.fetchActiveChatRun(chatId)
          } catch {
            if (!current()) return
            continue
          }
          if (!current()) return
          // A null answer can mean it finished between the two reads. Do not apply an
          // unowned detail; the next poll verifies the finished payload's own doc_ref.
          if (active !== runId) continue
          liveMembershipConfirmed = true
        }
      }
      if (run.status === 'running') {
        showRunDetail(run)
        await readTurnPages(chatId, state.turns.at(-1)?.seq ?? 0, current)
        if (!current()) return
        await loadActivities(chatId, gen)
        if (!current()) return
        continue
      }
      const afterSeq = runBodyState(state.turns, runId, 'failed') === 'present' ? state.turns.at(-1)?.seq ?? 0 : 0
      const result = await finalTurnRead(chatId, current, afterSeq)
      if (!current() || result.status === 'discarded') return
      terminalRuns.set(runId, run)
      renderTerminal(chatId, run, result)
      state.runId = null
      state.cancelling = false
      await loadFinalActivities(chatId, gen)
      return
    }
    if (current()) {
      showRunUnconfirmed(chatId, runId, startedAt)
      state.runId = null
      state.cancelling = false
      await loadFinalActivities(chatId, gen)
    }
  }

  /** The [AI 호출] button: the AI selected at the moment of the press. */
  async function invokeAi(): Promise<void> {
    await startAiRun(state.selectedProviderId)
  }

  /**
   * flowgate.v02.0142 T#1: starts the chat AI run on `providerId` and on nothing else — it is not read from the
   * selection again here, and an empty / unknown / unlisted id never falls back to the server default or to another
   * connection: the run ends with the `no_provider` notice and no /start request.
   */
  async function startAiRun(providerId: string): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || running.value) return
    if (!providerId || !hasProviders.value || !state.providers.some((p) => p.id === providerId)) {
      notify('warning', 'no_provider')
      return
    }
    const gen = chatGen
    const stale = () => disposed || gen !== chatGen
    state.runId = 'starting'
    // flowgate.v02test.0024 D: from the press on — the AI this call is pinned to and the access the user's mode gives
    // it; the run detail later replaces both with what the server applied.
    state.runDisplay = { providerName: providerName(providerId), access: accessOfMode(state.settings.sourceAccessMode) }
    // flowgate.v02.0152: this press, named once. Every /start it sends carries the same id, so the server can answer
    // a resend with the run the first request admitted — running or already finished — instead of a second run.
    const requestId = newStartRequest()
    const recoveryStarted = Date.now()
    let resends = 0
    for (;;) {
      try {
        const runId = await source.startChatRun(chatId, providerId, requestId)
        if (stale()) return
        if (!runId) {
          state.runId = null
          return
        }
        void pollRun(chatId, runId, gen)
        return
      } catch (error) {
        if (stale()) return
        // 409 run_in_progress: adopt the run that already owns the group (L0008 §5).
        if (error instanceof ConversationRequestError && error.code === 'run_in_progress' && error.runId) {
          void pollRun(chatId, error.runId, gen)
          return
        }
        // flowgate.v02.0152: no answer (the first call of a chat used to run past the 30s wait while the server went
        // on to run the AI), or the server still admitting this press: send the same press again. state.runId stays
        // 'starting' meanwhile, so another click cannot start a second press.
        if ((isNoResponse(error) || isStartPending(error))
          && resends < START_RESEND_MAX && Date.now() - recoveryStarted < START_RECOVERY_MS) {
          resends += 1
          await sleep(RUN_POLL_MS)
          if (stale()) return
          continue
        }
        // flowgate.v02.0084 T#2: recovery ran out without an answer naming a run — adopt the chat's active run if
        // there is one.
        if (isNoResponse(error) || isStartPending(error)) {
          const adopted = await findActiveRun(chatId)
          if (stale()) return
          if (adopted) {
            void pollRun(chatId, adopted, gen)
            return
          }
        }
        state.runId = null
        // flowgate.v02.0061 T#14: an edit / edit-once call while the Flow's Graph run is locked (WP answer 2).
        if (isSourceLocked(error)) recordChatError(chatId, gen, 'source_locked', error, false)
        else recordChatError(chatId, gen, 'invoke_failed', error)
        return
      }
    }
  }

  async function findActiveRun(chatId: string): Promise<string | null> {
    try {
      return await source.fetchActiveChatRun(chatId)
    } catch {
      return null
    }
  }

  /**
   * Polls the chat's active run, if the server has one. flowgate.v02test.0013: a failed query is asked again up to
   * `attempts` times, RUN_POLL_MS apart (the chat opening: a run missed there showed no progress until its end). A
   * recovery asks once — the next signal asks again. A manual [AI 호출] still adopts the run through 409.
   */
  async function adoptActiveRun(chatId: string, gen: number, attempts = 1): Promise<void> {
    for (let attempt = 1; ; attempt++) {
      if (!isCurrentChat(chatId, gen) || running.value) return
      try {
        const runId = await source.fetchActiveChatRun(chatId)
        if (!runId || !isCurrentChat(chatId, gen) || running.value) return
        void pollRun(chatId, runId, gen)
        return
      } catch {
        if (attempt >= attempts) return
      }
      await sleep(RUN_POLL_MS)
    }
  }

  async function stopRun(): Promise<void> {
    const runId = state.runId
    const chatId = state.activeChatId
    if (!chatId || !runId || runId === 'starting' || state.cancelling) return
    state.cancelling = true
    const gen = chatGen
    try {
      await source.cancelRun(runId)
      // pollRun observes the finish and releases the button exactly once.
    } catch (error) {
      if (!isCurrentChat(chatId, gen) || state.runId !== runId) return
      const status = error instanceof ConversationRequestError ? error.status : null
      if (status === 404 || status === 410) return
      state.cancelling = false
      notifyIfCurrent(gen, 'danger', 'cancel_failed', messageOf(error))
    }
  }

  // ── Mention / settings ─────────────────────────────────────────────────────────────
  async function issueMention(): Promise<string | null> {
    const chatId = state.activeChatId
    if (!chatId || state.copyingMention) return null
    state.copyingMention = true
    const settingsScope = state.scope ? { ...state.scope } : null
    const gen = chatGen
    try {
      const mention = await source.issueChatMention(chatId)
      // An edit_once one-shot may have just been consumed server-side (T0011 §8).
      if (gen === chatGen) void loadSettings(settingsScope)
      return mention
    } catch (error) {
      if (isSourceLocked(error)) recordChatError(chatId, gen, 'source_locked', error, false)
      else notifyIfCurrent(gen, 'danger', 'mention_failed', messageOf(error))
      return null
    } finally {
      state.copyingMention = false
    }
  }

  function setManualCopyText(text: string | null): void {
    state.manualCopyText = text || null
  }

  async function saveSettings(patch: ChatSettingsPatch): Promise<boolean> {
    if (state.savingSettings || state.pendingSourceMode) return false
    const gen = scopeGen
    const scope = state.scope ? { ...state.scope } : null
    if ('source_access_mode' in patch && !scope) return false
    const request = ++settingsRequest
    state.savingSettings = true
    try {
      const settings = await source.saveChatSettings(patch, scope ?? undefined)
      return settingsCurrent(gen, scope) && request === settingsRequest && applySettings(settings, scope)
    } catch (error) {
      if (settingsCurrent(gen, scope)) notify('danger', 'settings_failed', messageOf(error))
      return false
    } finally {
      if (settingsCurrent(gen, scope)) state.savingSettings = false
    }
  }

  /** Server-confirmed only: the selection moves once the PATCH answers (0568 T0004 §2). */
  async function setSourceMode(mode: SourceAccessMode): Promise<void> {
    if (state.pendingSourceMode || state.savingSettings || !state.scope || mode === state.settings.sourceAccessMode) return
    const gen = scopeGen
    const scope = { ...state.scope }
    const request = ++settingsRequest
    state.pendingSourceMode = mode
    try {
      const settings = await source.saveChatSettings({ source_access_mode: mode }, scope)
      if (settingsCurrent(gen, scope) && request === settingsRequest) applySettings(settings, scope)
    } catch (error) {
      if (settingsCurrent(gen, scope)) notify('danger', 'source_mode_failed', messageOf(error))
    } finally {
      if (settingsCurrent(gen, scope) && request === settingsRequest) state.pendingSourceMode = null
    }
  }

  function pushActivity(activity: ConversationActivity): void {
    state.activities = upsertActivity(state.activities, activity)
  }

  function dismissNotice(): void {
    state.notice = null
  }

  function dispose(): void {
    state.changeWindowEpoch += 1
    disposed = true
    titleGuard.disposed = true
    stopRevealWatch()
    const poll = livePoll
    livePoll = null
    poll?.wake?.()
    recovery = null
    deferredRefresh = null
    unsubscribe?.()
    unsubscribe = null
  }

  void loadSettings(state.scope)

  return {
    state: readonly(state) as DeepReadonly<ConversationState>,
    activeChat,
    timeline,
    running,
    hasProviders,
    submission,
    supportsActivities: source.supportsActivities,
    titleGuard: readonly(titleGuard),
    titleRevision,
    setScope,
    providersLoaded,
    reloadChats,
    selectChat,
    renameActiveChat,
    createChat,
    startDraft,
    setChatTitle,
    loadOlder,
    catchUp,
    recover,
    send,
    retry,
    reportRead,
    decideCommand,
    selectProvider,
    setCheckpoint,
    supportsChatDocSettings: !!(source.fetchChatDocSettings && source.saveChatDocSettings),
    invokeAi,
    stopRun,
    issueMention,
    setManualCopyText,
    saveSettings,
    setSourceMode,
    pushActivity,
    notify,
    dismissNotice,
    dispose,
  }
}
