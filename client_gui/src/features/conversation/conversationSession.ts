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
import { computed, reactive, readonly, type ComputedRef, type DeepReadonly } from 'vue'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS, newIdempotencyKey, type CreateChatOptions } from './api'
import { createHttpConversationSource, type ConversationSource } from './conversationSource'
import { buildTimeline, mergeTurns, replaceServerActivities, upsertActivity, type PendingTurn, type TimelineItem } from './timeline'
import type {
  ChatProvider,
  ChatSettings,
  ChatSettingsPatch,
  ChatSummary,
  CommandDecision,
  CommandRequestActivity,
  ConversationActivity,
  ConversationParticipant,
  ConversationScope,
  ConversationTurn,
  RunFileDiff,
  SourceAccessMode,
  StatusTone,
} from './types'

// L0004 §1-5 screen parameters (same values as the v0.1 ConversationView).
export const TURN_PAGE_SIZE = 50
export const PREPEND_PAGE_SIZE = 30
export const CATCHUP_MAX_ROUNDS = 20
export const SEND_RETRY_MAX = 2
export const RUN_POLL_MS = 2500
export const RUN_POLL_MAX = 480

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** Inline feedback line (the part opens no dialog — 0235 R0001: chat stays dialog-free). */
export interface ConversationNotice {
  id: number
  tone: StatusTone
  /** `gui.conversation.notice.<code>` */
  code: string
  detail: string | null
}

/** The run change file whose stored diff is open under its card. */
export interface OpenFileDiff {
  runId: string
  path: string
  status: 'loading' | 'ready' | 'error'
  diff: RunFileDiff | null
  error: string | null
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
  selectedProviderId: string
  settings: ChatSettings
  savingSettings: boolean
  pendingSourceMode: SourceAccessMode | null
  sending: boolean
  creatingChat: boolean
  renaming: boolean
  runId: string | null
  cancelling: boolean
  /** Command ids whose decision is in flight. */
  deciding: string[]
  fileDiff: OpenFileDiff | null
  copyingMention: boolean
  /** Text of a mention copy that did not reach the clipboard (inline manual copy, 0240). */
  manualCopyText: string | null
  notice: ConversationNotice | null
}

export interface ConversationSession {
  readonly state: DeepReadonly<ConversationState>
  readonly activeChat: ComputedRef<DeepReadonly<ChatSummary> | null>
  readonly timeline: ComputedRef<TimelineItem[]>
  readonly running: ComputedRef<boolean>
  readonly hasProviders: ComputedRef<boolean>
  readonly supportsActivities: boolean

  setScope(scope: ConversationScope | null): Promise<void>
  /** Settles once the provider list of the current scope has loaded (or failed to). */
  providersLoaded(): Promise<void>
  reloadChats(): Promise<void>
  selectChat(chatId: string): Promise<void>
  renameActiveChat(title: string): Promise<boolean>
  createChat(title: string, options?: CreateChatOptions): Promise<boolean>
  /** Leaves the open chat for an unsaved new one: nothing exists until its first send. */
  startDraft(): void
  /** Shows a title the server already stored (the AI title of a new chat). */
  setChatTitle(chatId: string, title: string): void
  loadOlder(): Promise<void>
  catchUp(): Promise<void>
  send(text: string): Promise<boolean>
  retry(localId: string): Promise<boolean>
  reportRead(seq: number): Promise<void>
  decideCommand(commandId: string, decision: CommandDecision): Promise<void>
  /** Opens (or, when it is open, closes) the stored diff of one file of a run change. */
  openChangeFile(runId: string, path: string): Promise<void>
  closeChangeFile(): void
  selectProvider(providerId: string): void
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
    settings: { ...DEFAULT_CHAT_SETTINGS },
    savingSettings: false,
    pendingSourceMode: null,
    sending: false,
    creatingChat: false,
    renaming: false,
    runId: null,
    cancelling: false,
    deciding: [],
    fileDiff: null,
    copyingMention: false,
    manualCopyText: null,
    notice: null,
  })

  let disposed = false
  // Bumped on every scope / chat switch; a response for an older generation is dropped.
  let scopeGen = 0
  let chatGen = 0
  let providersProject: string | null = null
  let providersLoad: Promise<void> = Promise.resolve()
  let noticeSeq = 0
  let unsubscribe: (() => void) | null = null
  // Highest read seq already reported for the active chat; it belongs to that chat and is
  // reset with it (clearChat), so another chat's position never suppresses a report.
  let reportedRead = 0
  // Activity reads are numbered. An answer older than one already applied is dropped, and so
  // is every read started before a decision answered: it may still show the request pending.
  let activityReads = 0
  let activityApplied = 0
  let activityStaleUpTo = 0

  const activeChat = computed(() => state.chats.find((c) => c.id === state.activeChatId) ?? null)
  const timeline = computed(() => buildTimeline(state.turns, state.activities, state.pending, state.readSeq))
  const running = computed(() => state.runId !== null)
  const hasProviders = computed(() => state.providersStatus === 'ready' && state.providers.length > 0)

  function notify(tone: StatusTone, code: string, detail: string | null = null): void {
    noticeSeq += 1
    state.notice = { id: noticeSeq, tone, code, detail }
  }

  function addStatus(tone: StatusTone, code: string, text: string | null = null): void {
    noticeSeq += 1
    state.activities = upsertActivity(state.activities, {
      kind: 'status',
      id: `local-status-${noticeSeq}`,
      createdAt: new Date().toISOString(),
      tone,
      code,
      text,
    })
  }

  function clearChat(): void {
    unsubscribe?.()
    unsubscribe = null
    state.turns = []
    state.pending = []
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
    state.fileDiff = null
  }

  function applyPage(page: Awaited<ReturnType<ConversationSource['fetchTurns']>>): void {
    state.turns = mergeTurns(state.turns, page.turns)
    state.headSeq = Math.max(state.headSeq, page.headSeq)
    if (page.participants.length > 0) state.participants = page.participants
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

  async function fetchProviders(projectId: string): Promise<void> {
    try {
      const list = await source.listProviders(projectId)
      if (disposed || providersProject !== projectId) return
      state.providers = list.providers
      const keep = list.providers.some((p) => p.id === state.selectedProviderId)
      state.selectedProviderId = keep
        ? state.selectedProviderId
        : list.defaultProviderId && list.providers.some((p) => p.id === list.defaultProviderId)
          ? list.defaultProviderId
          : list.providers[0]?.id ?? ''
      state.providersStatus = 'ready'
    } catch {
      if (disposed || providersProject !== projectId) return
      state.providers = []
      state.selectedProviderId = ''
      state.providersStatus = 'error'
    }
  }

  async function loadSettings(): Promise<void> {
    try {
      const settings = await source.fetchChatSettings()
      if (!disposed) state.settings = settings
    } catch {
      // D0008 §3-4: keep chatting on defaults, never block.
    }
  }

  async function reloadChats(): Promise<void> {
    const scope = state.scope
    if (!scope) return
    const gen = scopeGen
    state.chatsStatus = 'loading'
    state.chatsError = null
    try {
      const chats = await source.listChats(scope)
      if (disposed || gen !== scopeGen) return
      state.chats = chats
      state.chatsStatus = 'ready'
    } catch (error) {
      if (disposed || gen !== scopeGen) return
      state.chatsStatus = 'error'
      state.chatsError = messageOf(error)
    }
  }

  async function setScope(scope: ConversationScope | null): Promise<void> {
    const same = scope && state.scope && scope.projectId === state.scope.projectId && scope.groupId === state.scope.groupId
    if (same || (!scope && !state.scope)) return
    scopeGen += 1
    chatGen += 1
    state.scope = scope ? { ...scope } : null
    state.chats = []
    state.chatsStatus = 'idle'
    state.chatsError = null
    state.activeChatId = null
    clearChat()
    if (!scope) return
    void loadProviders(scope.projectId)
    const gen = scopeGen
    await reloadChats()
    if (disposed || gen !== scopeGen) return
    // The newest conversation of the Flow opens first.
    const first = state.chats[0]
    if (first) await selectChat(first.id)
  }

  // ── Active chat ────────────────────────────────────────────────────────────────────
  async function selectChat(chatId: string): Promise<void> {
    if (chatId === state.activeChatId && state.turnsStatus !== 'error') return
    chatGen += 1
    const gen = chatGen
    clearChat()
    state.activeChatId = chatId
    state.turnsStatus = 'loading'
    try {
      // No cursor: the server resumes from the position it remembers (D0002 §3-4).
      const first = await source.fetchTurns(chatId, { includeHead: true, limit: TURN_PAGE_SIZE })
      if (disposed || gen !== chatGen) return
      applyPage(first)
      let cursor = first.nextAfterSeq
      for (let round = 0; cursor != null && round < CATCHUP_MAX_ROUNDS; round++) {
        const page = await source.fetchTurns(chatId, { afterSeq: cursor, limit: TURN_PAGE_SIZE })
        if (disposed || gen !== chatGen) return
        applyPage(page)
        cursor = page.nextAfterSeq
      }
      state.hasMoreBefore = oldestSeq() > 1
      if (state.hasMoreBefore && state.turns.length < PREPEND_PAGE_SIZE) await loadOlder()
      if (disposed || gen !== chatGen) return
      state.turnsStatus = 'ready'
    } catch (error) {
      if (disposed || gen !== chatGen) return
      state.turnsStatus = 'error'
      state.turnsError = messageOf(error)
      return
    }
    void openActivities(chatId, gen)
    void adoptActiveRun(chatId, gen)
  }

  async function openActivities(chatId: string, gen: number): Promise<void> {
    await loadActivities(chatId, gen)
    if (disposed || gen !== chatGen || !source.supportsActivities || !source.subscribeActivities) return
    unsubscribe = source.subscribeActivities(chatId, (activity) => {
      if (!disposed && gen === chatGen) state.activities = upsertActivity(state.activities, activity)
    })
  }

  /** One full read of the chat's command requests and run changes (D0006 §3.9.2). */
  async function loadActivities(chatId: string, gen: number): Promise<void> {
    if (!source.supportsActivities) return
    activityReads += 1
    const read = activityReads
    try {
      const list = await source.listActivities(chatId)
      if (!isCurrentChat(chatId, gen) || read <= activityApplied || read <= activityStaleUpTo) return
      activityApplied = read
      state.activities = replaceServerActivities(state.activities, list, state.deciding)
    } catch {
      // Activities are an addition to the conversation of record: what is shown stays and the
      // next polling tick (or reopening the chat) reads again (D0006 §6.2).
    }
  }

  async function loadOlder(): Promise<void> {
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
      const page = await source.fetchTurns(chatId, { beforeSeq: before, limit: PREPEND_PAGE_SIZE })
      if (disposed || gen !== chatGen) return
      applyPage(page)
      state.hasMoreBefore = page.prevBeforeSeq != null || oldestSeq() > 1
    } catch {
      // Keep what is on screen; the [이전 대화] button stays to try again.
    } finally {
      if (gen === chatGen) state.loadingOlder = false
    }
  }

  /** Fill the gap after the last confirmed seq (P0003 scenario 7). */
  async function catchUp(): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || disposed) return
    const gen = chatGen
    let cursor: number | null = state.headSeq
    for (let round = 0; cursor != null && round < CATCHUP_MAX_ROUNDS; round++) {
      let page
      try {
        page = await source.fetchTurns(chatId, { afterSeq: cursor, limit: TURN_PAGE_SIZE })
      } catch {
        return
      }
      if (disposed || gen !== chatGen) return
      applyPage(page)
      cursor = page.nextAfterSeq
    }
  }

  async function renameActiveChat(title: string): Promise<boolean> {
    const chat = activeChat.value
    const next = title.trim()
    if (!chat || !next || next === chat.title || state.renaming) return false
    state.renaming = true
    const chatId = chat.id
    try {
      const saved = await source.renameChat(chatId, next)
      state.chats = state.chats.map((c) => (c.id === chatId ? { ...c, title: saved.title } : c))
      return true
    } catch (error) {
      notify('danger', 'rename_failed', messageOf(error))
      return false
    } finally {
      state.renaming = false
    }
  }

  /**
   * D0006 §6.3: a standalone CH. Nothing is added before the server answers; on success the
   * list is read again and the new chat opens. A failure keeps the open chat, says why, and
   * never retries on its own — the button stays for another try.
   */
  async function createChat(title: string, options?: CreateChatOptions): Promise<boolean> {
    const scope = state.scope
    const next = title.trim()
    if (!scope || !next || state.creatingChat) return false
    state.creatingChat = true
    const gen = scopeGen
    try {
      let created: ChatSummary
      try {
        created = options?.autoTitle ? await source.createChat(scope, next, { autoTitle: true }) : await source.createChat(scope, next)
      } catch (error) {
        if (!disposed && gen === scopeGen) notifyCreateFailed(error)
        return false
      }
      if (disposed || gen !== scopeGen) return false
      await reloadChats()
      if (disposed || gen !== scopeGen) return false
      // A list read that failed (or does not show it yet) still gets the chat the server made.
      if (!state.chats.some((c) => c.id === created.id)) state.chats = [created, ...state.chats]
      await selectChat(created.id)
      return true
    } finally {
      state.creatingChat = false
    }
  }

  // flowgate.v02.0047: a new chat is a draft until its first send (chatStart.ts creates it then).
  function startDraft(): void {
    chatGen += 1
    clearChat()
    state.activeChatId = null
  }

  function setChatTitle(chatId: string, title: string): void {
    state.chats = state.chats.map((c) => (c.id === chatId ? { ...c, title } : c))
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

  /** POST one turn; the same idempotency key on every retry, so a stored turn replays. */
  async function postPending(chatId: string, gen: number, item: PendingTurn): Promise<boolean> {
    for (let attempt = 0; attempt <= SEND_RETRY_MAX; attempt++) {
      try {
        const res = await source.postTurn(chatId, { body: item.body, idempotencyKey: item.idempotencyKey, basedOnSeq: item.basedOnSeq })
        // Stored either way; the screen of another chat is not touched.
        if (!isCurrentChat(chatId, gen)) return true
        state.pending = state.pending.filter((p) => p.localId !== item.localId)
        if (res.turn) state.turns = mergeTurns(state.turns, [res.turn])
        state.headSeq = Math.max(state.headSeq, res.headSeq)
        return true
      } catch (error) {
        const status = error instanceof ConversationRequestError ? error.status : null
        // 400/404/409/422 are decisions, not blips — retrying repeats the same answer.
        if ((status != null && [400, 404, 409, 422].includes(status)) || attempt === SEND_RETRY_MAX) {
          if (isCurrentChat(chatId, gen)) notify('danger', 'send_failed', messageOf(error))
          return false
        }
      }
    }
    return false
  }

  function markPending(localId: string, failed: boolean): void {
    state.pending = state.pending.map((p) => (p.localId === localId ? { ...p, failed } : p))
  }

  /**
   * D0005 §3-2: the send-time action runs once, only after the turn was accepted, and only
   * for the chat the turn went to. After a switch to another chat it is dropped: a mention
   * or an AI run must never land in a chat where no turn was just sent.
   */
  function runSendAction(chatId: string, gen: number): void {
    if (!isCurrentChat(chatId, gen)) return
    if (state.settings.sendAction === 'copy_mention') options.onAutoCopyMention?.()
    else if (state.settings.sendAction === 'invoke_ai' && hasProviders.value) void invokeAi()
  }

  async function send(text: string): Promise<boolean> {
    const chatId = state.activeChatId
    const body = text.trim()
    // One turn at a time: no new send while a turn posts or a chat AI run is in flight.
    if (!chatId || !body || state.sending || running.value) return false
    const gen = chatGen
    const key = newIdempotencyKey()
    const item: PendingTurn = { localId: key, idempotencyKey: key, body, basedOnSeq: state.headSeq, createdAt: new Date().toISOString(), failed: false }
    state.pending = [...state.pending, item]
    state.sending = true
    try {
      const ok = await postPending(chatId, gen, item)
      if (!ok) {
        // The bubble stays with a retry that reuses the same key (P0003 scenario 17).
        if (isCurrentChat(chatId, gen)) markPending(item.localId, true)
        return false
      }
      runSendAction(chatId, gen)
      return true
    } finally {
      state.sending = false
    }
  }

  async function retry(localId: string): Promise<boolean> {
    const chatId = state.activeChatId
    const item = state.pending.find((p) => p.localId === localId)
    if (!chatId || !item || state.sending) return false
    const gen = chatGen
    markPending(localId, false)
    state.sending = true
    try {
      const ok = await postPending(chatId, gen, item)
      if (!ok) {
        if (isCurrentChat(chatId, gen)) markPending(localId, true)
        return false
      }
      // The first attempt failed before its send action, so the accepted retry runs it once.
      runSendAction(chatId, gen)
      return true
    } finally {
      state.sending = false
    }
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
        else if (status !== 404) notify('danger', 'command_decision_failed', messageOf(error))
      }
    } finally {
      state.deciding = state.deciding.filter((id) => id !== commandId)
      activityStaleUpTo = activityReads
    }
    if (isCurrentChat(chatId, gen)) await loadActivities(chatId, gen)
  }

  // ── Run change file diff (D0006 §3.8.3, §6.1) ──────────────────────────────────────
  async function openChangeFile(runId: string, path: string): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId) return
    const open = state.fileDiff
    if (open && open.runId === runId && open.path === path && open.status !== 'error') {
      state.fileDiff = null
      return
    }
    const gen = chatGen
    state.fileDiff = { runId, path, status: 'loading', diff: null, error: null }
    const stillOpen = () => isCurrentChat(chatId, gen) && state.fileDiff?.runId === runId && state.fileDiff.path === path
    try {
      const diff = await source.fetchRunFileDiff(chatId, runId, path)
      if (stillOpen()) state.fileDiff = { runId, path, status: 'ready', diff, error: null }
    } catch (error) {
      if (stillOpen()) state.fileDiff = { runId, path, status: 'error', diff: null, error: messageOf(error) }
    }
  }

  function closeChangeFile(): void {
    state.fileDiff = null
  }

  // ── Chat AI run (D0005 §3-1, L0008, 0264 STOP) ─────────────────────────────────────
  function selectProvider(providerId: string): void {
    if (state.providers.some((p) => p.id === providerId)) state.selectedProviderId = providerId
  }

  async function pollRun(chatId: string, runId: string, gen: number): Promise<void> {
    state.runId = runId
    const baseline = state.turns.filter((t) => t.speaker === 'ai').length
    for (let i = 0; i < RUN_POLL_MAX; i++) {
      await sleep(RUN_POLL_MS)
      if (disposed || gen !== chatGen) return
      let run
      try {
        run = await source.fetchRun(runId)
      } catch (error) {
        const status = error instanceof ConversationRequestError ? error.status : null
        // Only a gone run is terminal; a blip keeps polling (L0008 §3).
        if (status === 404 || status === 410) break
        await loadActivities(chatId, gen)
        continue
      }
      // No server push in v0.2 yet: the reply lands through the same catch-up as a gap, and
      // the run's command requests through an activity read on the same tick (D0006 §3.9.2).
      await catchUp()
      if (disposed || gen !== chatGen) return
      if (run.status !== 'finished') {
        await loadActivities(chatId, gen)
        continue
      }
      state.runId = null
      state.cancelling = false
      const delivered = state.turns.filter((t) => t.speaker === 'ai').length > baseline
      // A one-shot source mode is consumed by the delivered answer (0568 rev3).
      if (delivered) void loadSettings()
      // A delivered answer shows itself; only a run that ended without one says so.
      if (run.endReason === 'cancelled') addStatus('info', 'run_cancelled')
      else if (!delivered) addStatus('danger', 'run_no_reply', run.endReason)
      // Right after the end: the run change and the closed requests (D0006 §3.9.2-3).
      await loadActivities(chatId, gen)
      return
    }
    if (isCurrentChat(chatId, gen)) {
      state.runId = null
      state.cancelling = false
      await loadActivities(chatId, gen)
    }
  }

  async function invokeAi(): Promise<void> {
    const chatId = state.activeChatId
    if (!chatId || running.value) return
    if (!hasProviders.value) {
      notify('warning', 'no_provider')
      return
    }
    const gen = chatGen
    state.runId = 'starting'
    try {
      const runId = await source.startChatRun(chatId, state.selectedProviderId || null)
      if (disposed || gen !== chatGen) return
      if (!runId) {
        state.runId = null
        return
      }
      void pollRun(chatId, runId, gen)
    } catch (error) {
      if (disposed || gen !== chatGen) return
      // 409 run_in_progress: adopt the run that already owns the group (L0008 §5).
      if (error instanceof ConversationRequestError && error.code === 'run_in_progress' && error.runId) {
        void pollRun(chatId, error.runId, gen)
        return
      }
      state.runId = null
      // flowgate.v02.0061 T#14: an edit / edit-once call while the Flow's Graph run is locked (WP answer 2).
      if (isSourceLocked(error)) notify('warning', 'source_locked')
      else notify('danger', 'invoke_failed', messageOf(error))
    }
  }

  async function adoptActiveRun(chatId: string, gen: number): Promise<void> {
    try {
      const runId = await source.fetchActiveChatRun(chatId)
      if (!runId || disposed || gen !== chatGen || running.value) return
      void pollRun(chatId, runId, gen)
    } catch {
      // Best effort — a manual [AI 호출] still adopts the run through 409.
    }
  }

  async function stopRun(): Promise<void> {
    const runId = state.runId
    if (!runId || runId === 'starting' || state.cancelling) return
    state.cancelling = true
    try {
      await source.cancelRun(runId)
      // pollRun observes the finish and releases the button exactly once.
    } catch (error) {
      const status = error instanceof ConversationRequestError ? error.status : null
      if (status === 404 || status === 410) return
      state.cancelling = false
      notify('danger', 'cancel_failed', messageOf(error))
    }
  }

  // ── Mention / settings ─────────────────────────────────────────────────────────────
  async function issueMention(): Promise<string | null> {
    const chatId = state.activeChatId
    if (!chatId || state.copyingMention) return null
    state.copyingMention = true
    try {
      const mention = await source.issueChatMention(chatId)
      // An edit_once one-shot may have just been consumed server-side (T0011 §8).
      void loadSettings()
      return mention
    } catch (error) {
      if (isSourceLocked(error)) notify('warning', 'source_locked')
      else notify('danger', 'mention_failed', messageOf(error))
      return null
    } finally {
      state.copyingMention = false
    }
  }

  function setManualCopyText(text: string | null): void {
    state.manualCopyText = text || null
  }

  async function saveSettings(patch: ChatSettingsPatch): Promise<boolean> {
    if (state.savingSettings) return false
    state.savingSettings = true
    try {
      state.settings = await source.saveChatSettings(patch)
      return true
    } catch (error) {
      notify('danger', 'settings_failed', messageOf(error))
      return false
    } finally {
      state.savingSettings = false
    }
  }

  /** Server-confirmed only: the selection moves once the PATCH answers (0568 T0004 §2). */
  async function setSourceMode(mode: SourceAccessMode): Promise<void> {
    if (state.pendingSourceMode || mode === state.settings.sourceAccessMode) return
    state.pendingSourceMode = mode
    try {
      state.settings = await source.saveChatSettings({ source_access_mode: mode })
    } catch (error) {
      notify('danger', 'source_mode_failed', messageOf(error))
    } finally {
      state.pendingSourceMode = null
    }
  }

  function pushActivity(activity: ConversationActivity): void {
    state.activities = upsertActivity(state.activities, activity)
  }

  function dismissNotice(): void {
    state.notice = null
  }

  function dispose(): void {
    disposed = true
    unsubscribe?.()
    unsubscribe = null
  }

  void loadSettings()

  return {
    state: readonly(state) as DeepReadonly<ConversationState>,
    activeChat,
    timeline,
    running,
    hasProviders,
    supportsActivities: source.supportsActivities,
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
    send,
    retry,
    reportRead,
    decideCommand,
    openChangeFile,
    closeChangeFile,
    selectProvider,
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
