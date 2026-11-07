// flowgate.v02.0047 — starting a chat from the composer (R0001 §2.1, §2.2). With no chat open
// in a Flow the composer writes the first message of a new one: that chat exists only once the
// message is sent — created through the same createChat contract, then sent through the same
// send — so an untouched draft never reaches the chat list. A failed create keeps the text in
// the composer; a failed send keeps the bubble with its retry, as for any other chat.
//
// A chat made this way is created owing one AI title (`autoTitle`; the server records the debt),
// asked right after its first message was accepted and never awaited by the send. The ask goes
// to the chat the message went to, so leaving it (for [+] or another chat of the list) while the
// message posts does not lose the title; and it waits for the provider list when the first
// message beat it, so a list still loading does not lose it either. A title the user typed (on the draft or afterwards) wins: the draft's typed title
// is used as is and owes nothing, and the server pays the debt once and drops it on any manual
// rename.
import { computed, readonly, ref, watch, getCurrentScope, onScopeDispose, type ComputedRef, type Ref } from 'vue'
import { draftIntent, draftCreateIntent, draftUserId, type DraftSendSnapshot } from './drafts'
import { ConversationRequestError } from './api'
import { createSubmissionTrace, type SubmissionTrace, type SubmissionPhase } from './submissionTrace'
import type { ConversationSession } from './conversationSession'
import type { ConversationSource } from './conversationSource'

export interface ChatStart {
  /** A Flow is open and no chat is: the composer starts a new one. */
  readonly isDraft: ComputedRef<boolean>
  /** The title the user typed for the draft; null keeps the default one. */
  readonly draftTitle: Readonly<Ref<string | null>>
  /** The draft's chat is being created and its first message sent. */
  readonly starting: Readonly<Ref<boolean>>
  readonly phase: ComputedRef<SubmissionPhase | null>
  /** [+]: leave the open chat for a new, unsaved one. */
  readonly draftIntentId: Readonly<Ref<string | null>>
  startNew(): void
  rename(title: string): Promise<boolean>
  send(text: string, snapshot?: DraftSendSnapshot, trace?: SubmissionTrace): Promise<boolean>
  retry(localId: string): Promise<boolean>
}

export interface ChatStartOptions {
  session: ConversationSession
  source: ConversationSource
  /** The provisional title a new chat is created with. */
  defaultTitle: () => string
}

export function createChatStart({ session, source, defaultTitle }: ChatStartOptions): ChatStart {
  const state = session.state
  const draftTitle = ref<string | null>(null)
  const draftIntentId = ref<string | null>(null)
  const operations = ref<{ origin: DraftSendSnapshot; status: SubmissionPhase; active: boolean }[]>([])
  const ownsScreen = (snapshot: DraftSendSnapshot) => snapshot.owner.userId === draftUserId() &&
    snapshot.owner.projectId === state.scope?.projectId && snapshot.owner.groupId === state.scope?.groupId &&
    snapshot.owner.intentId === draftIntentId.value &&
    (state.activeChatId === snapshot.owner.chatId || state.activeChatId === snapshot.createdChatId)
  const starting = computed(() => operations.value.some((op) => op.active && ownsScreen(op.origin)))
  const phase = computed(() => session.submission.value ?? [...operations.value].reverse().find((op) => ownsScreen(op.origin))?.status ?? null)
  function clearSettledFailures(): void {
    operations.value = operations.value.filter((op) => op.active || (ownsScreen(op.origin) &&
      !(op.status.phase === 'failed' && op.status.stage === 'load' && state.turnsStatus === 'ready')))
  }
  watch(() => [state.scope?.projectId, state.scope?.groupId, draftUserId(), draftIntentId.value, state.activeChatId, state.turnsStatus] as const,
    clearSettledFailures, { flush: 'sync' })
  watch(() => [state.scope?.projectId, state.scope?.groupId, draftUserId()] as const, () => {
    draftIntentId.value = state.scope ? draftIntent({ ...state.scope, userId: draftUserId() }) : null
    draftTitle.value = null
  }, { immediate: true, flush: 'sync' })
  // Chats made on their first send whose AI title is still owed, with their provisional title
  // and the project whose AI picks it. Kept per chat: a first send that failed in one chat stays
  // owed while another chat is started.
  type Debt = { title: string; projectId: string; groupId: string; userId: string; accepted: boolean;
    status: 'owed' | 'unknown' | 'claimed'; lastTrigger?: string; lastWatch?: string; checkRevision?: number }
  const owed = new Map<string, Debt>()
  const inFlight = new Map<string, { waiting: boolean }>()
  const ended = new Set<string>()
  let acceptance = 0
  let lifetime = 0
  let stopped = false
  const keyOf = (chatId: string) => JSON.stringify([draftUserId(), state.scope?.projectId, state.scope?.groupId, chatId])
  const listed = (chatId: string) => state.chats.find((chat) => chat.id === chatId)
  const sameOwner = (due: Debt) => !stopped && !session.titleGuard.disposed && due.userId === draftUserId() &&
    due.userId === session.titleGuard.userId && due.projectId === state.scope?.projectId && due.groupId === state.scope?.groupId
  function observe(chatId: string): Debt | undefined {
    const key = keyOf(chatId)
    const chat = listed(chatId)
    const marker = chat?.autoTitle
    if (marker && ['done', 'manual', 'failed'].includes(marker.state)) { owed.delete(key); ended.add(key); return }
    if (ended.has(key)) return
    let due = owed.get(key)
    if (!due && marker?.state === 'owed' && state.scope) {
      due = { title: marker.provisionalTitle, ...state.scope, userId: draftUserId(), accepted: false, status: 'owed' }
      owed.set(key, due)
    }
    if (due && marker?.state === 'claimed') { due.status = 'claimed'; due.checkRevision = session.titleGuard.listRevision }
    if (due && due.status !== 'owed' && marker?.state === 'owed' && state.chatsStatus === 'ready' &&
      session.titleGuard.listRevision > (due.checkRevision ?? -1)) { due.status = 'owed'; due.lastTrigger = undefined; due.lastWatch = undefined }
    return due
  }

  const isDraft = computed(() => state.scope !== null && state.activeChatId === null)

  function startNew(): void {
    if (state.scope) draftIntentId.value = draftIntent({ ...state.scope, userId: draftUserId() }, true)
    session.startDraft()
    draftTitle.value = null
  }

  async function rename(title: string): Promise<boolean> {
    if (!isDraft.value) return session.renameActiveChat(title)
    const next = title.trim()
    draftTitle.value = next && next !== defaultTitle() ? next : null
    return true
  }

  async function send(text: string, snapshot?: DraftSendSnapshot, trace = createSubmissionTrace()): Promise<boolean> {
    trace.mark('handler')
    if (starting.value || (snapshot && !ownsScreen(snapshot))) return false
    if (!isDraft.value) {
      const chatId = state.activeChatId
      operations.value = operations.value.filter((op) => !ownsScreen(op.origin))
      return settle(chatId, await session.send(text, undefined, trace, snapshot))
    }
    if (starting.value || !text.trim() || !state.scope || !draftIntentId.value) return false
    const origin: DraftSendSnapshot = snapshot ?? { owner: { ...state.scope, userId: draftUserId(), intentId: draftIntentId.value, chatId: null }, body: text, revision: 0 }
    if (!ownsScreen(origin)) return false
    origin.createIntentId = draftCreateIntent(origin.owner)
    const typed = draftTitle.value
    const title = typed ?? defaultTitle()
    operations.value = operations.value.filter((op) => !ownsScreen(op.origin))
    const operation = { origin, status: { phase: 'creating', stage: 'create', attempt: 1 } as SubmissionPhase, active: true }
    operations.value.push(operation)
    const currentOperation = () => operations.value.find((op) => op.origin.createIntentId === origin.createIntentId)
    const update = (status: SubmissionPhase) => { const op = currentOperation(); if (op) op.status = status }
    // flowgate.v02.0142 T#1: the AI chosen now is the one of this first message, whatever the picker shows once the
    // chat has been created.
    const providerId = state.selectedProviderId
    try {
      const result = await session.createChat(title, typed === null ? { autoTitle: true } : undefined, {
        draftIntentId: origin.owner.intentId,
        createIntentId: origin.createIntentId,
        isCurrent: () => ownsScreen(origin),
        onCreated: (created) => { origin.createdChatId = created.chatId },
        onLoading: () => update({ phase: 'loading', stage: 'load', attempt: 1 }),
        trace,
      })
      if (result.status !== 'created' || !ownsScreen(origin)) {
        if (result.status === 'failed') {
          const stage = currentOperation()?.status.stage ?? 'create'
          update({ phase: 'failed', stage, attempt: 1 })
        } else operations.value = operations.value.filter((op) => op.origin.createIntentId !== origin.createIntentId)
        return false
      }
      draftTitle.value = null
      if (typed === null && !ended.has(keyOf(result.chatId))) owed.set(keyOf(result.chatId), { title, ...result.scope, userId: origin.owner.userId, accepted: false, status: 'owed' })
      // No await between this ownership check and the explicitly addressed first send.
      if (!ownsScreen(origin)) return false
      update({ phase: 'sending', stage: 'send', attempt: 1 })
      const ok = await session.send(text, { ...result, providerId }, trace, origin)
      // Session owns send progress (and its retained failure bubble).
      operations.value = operations.value.filter((op) => op.origin.createIntentId !== origin.createIntentId)
      return settle(result.chatId, ok)
    } finally {
      const op = currentOperation()
      if (op) op.active = false
      clearSettledFailures()
    }
  }

  async function retry(localId: string): Promise<boolean> {
    const chatId = state.activeChatId
    return settle(chatId, await session.retry(localId))
  }

  /** A successful send is never held for title generation. */
  function settle(chatId: string | null, ok: boolean): boolean {
    if (ok && chatId) {
      const due = observe(chatId)
      if (due) { due.accepted = true; void requestTitle(chatId, due, `send:${++acceptance}`) }
    }
    return ok
  }

  // One reconciliation read per ambiguous request, never a timer/finally retry loop.
  async function reconcile(chatId: string, due: Debt, current: () => boolean): Promise<void> {
    if (!current()) return
    due.status = 'unknown'
    due.checkRevision = session.titleGuard.listRevision
    await session.reloadChats()
    if (!current() || state.chatsStatus !== 'ready') return
    const marker = listed(chatId)?.autoTitle
    if (marker?.state === 'owed') due.status = 'owed'
    else if (marker?.state === 'claimed') due.status = 'claimed'
    observe(chatId)
  }

  async function requestTitle(chatId: string, due: Debt, trigger: string): Promise<void> {
    const key = keyOf(chatId)
    if (trigger.startsWith('watch:')) {
      if (due.lastWatch === trigger) return
      due.lastWatch = trigger // Also consume identical observations made during an in-flight request.
    }
    if (!source.autoTitleChat || !sameOwner(due) || !due.accepted || due.status !== 'owed' || ended.has(key) ||
      inFlight.has(key) || due.lastTrigger === trigger) return
    due.lastTrigger = trigger
    const flight = { waiting: true }
    inFlight.set(key, flight) // Includes provider wait, not just the HTTP request.
    const generation = lifetime
    const revision = session.titleRevision(chatId)
    const current = () => sameOwner(due) && generation === lifetime
    try {
      await session.providersLoaded()
      if (!current() || session.titleRevision(chatId) !== revision) return
      const chat = listed(chatId)
      if (!chat || state.chatsStatus === 'error' || (chat.autoTitle && chat.autoTitle.state !== 'owed') || chat.title !== due.title) return
      const providerId = state.selectedProviderId
      if (state.providersStatus !== 'ready' || !providerId || !state.providers.some((p) => p.id === providerId)) return
      flight.waiting = false
      const saved = await source.autoTitleChat(chatId, providerId)
      if (!current()) return
      if (session.titleRevision(chatId) !== revision) { await reconcile(chatId, due, current); return }
      if (saved.id !== chatId || typeof saved.title !== 'string' || !saved.title.trim()) {
        await reconcile(chatId, due, current)
        return
      }
      if (listed(chatId)?.autoTitle?.state === 'manual') return
      session.setChatTitle(saved.id, saved.title)
      owed.delete(key)
      ended.add(key)
    } catch (error) {
      if (!current()) return
      const code = error instanceof ConversationRequestError ? error.code : null
      if (code === 'conversation_auto_title_unavailable') return // Server returned the unexecuted debt.
      if (code === 'conversation_auto_title_no_message') { due.accepted = false; return }
      if (code === 'conversation_title_changed' || code === 'conversation_auto_title_failed' ||
        (error instanceof ConversationRequestError && (error.status === 403 || error.status === 404 || code === 'conversation_group_disposed'))) {
        owed.delete(key); ended.add(key); return
      }
      await reconcile(chatId, due, current)
    } finally {
      if (inFlight.get(key) === flight) inFlight.delete(key)
      // A distinct scope reentry that happened during HTTP waits for that request,
      // then reads once before recovering. This is not a failure/finally retry loop.
      if (generation !== lifetime && sameOwner(due) && state.activeChatId === chatId &&
        owed.get(key) === due && !ended.has(key) && !inFlight.has(key)) {
        const reentry = lifetime
        const returned = () => sameOwner(due) && reentry === lifetime
        await reconcile(chatId, due, returned)
        if (returned() && listed(chatId)?.autoTitle?.state === 'owed' && due.status === 'owed') {
          void requestTitle(chatId, due, `reentry:${reentry}`)
        }
      }
    }
  }

  const stopLifetime = watch(() => [state.scope?.projectId, state.scope?.groupId, draftUserId(), session.titleGuard.disposed] as const,
    (now, before) => {
      lifetime++
      for (const [key, flight] of inFlight) if (flight.waiting) inFlight.delete(key)
      if (!state.scope || now[2] !== before?.[2] || session.titleGuard.disposed) { owed.clear(); ended.clear() }
      if (session.titleGuard.disposed) cleanup()
    }, { flush: 'sync' })
  // Fingerprint values, not array identity/loading/finally. Identical reloads cannot retry.
  const stopRecovery = watch(() => JSON.stringify([
    state.scope, draftUserId(), state.activeChatId, state.chatsStatus === 'ready', state.providersStatus, state.selectedProviderId,
    state.providers.map((p) => p.id), state.turnsStatus === 'ready' && state.turns.some((t) => t.speaker === 'user' && t.body.trim()),
    state.chats.map((chat) => [chat.id, chat.title, chat.autoTitle]),
  ]), (trigger) => {
    if (stopped || session.titleGuard.disposed || !state.scope || state.chatsStatus !== 'ready') return
    const active = state.activeChatId
    if (active) {
      const due = observe(active)
      if (due && state.turnsStatus === 'ready' && state.turns.some((t) => t.speaker === 'user' && t.body.trim())) due.accepted = true
    }
    for (const [key, due] of owed) {
      if (sameOwner(due)) {
        const chatId = JSON.parse(key)[3] as string
        if (chatId === active && (state.sending || starting.value)) continue
        observe(chatId)
        if (owed.has(key)) void requestTitle(chatId, due, `watch:${trigger}`)
      }
    }
  }, { immediate: true, flush: 'post' })
  function cleanup(): void {
    stopped = true; lifetime++; owed.clear(); ended.clear()
    stopRecovery(); stopLifetime()
  }
  if (getCurrentScope()) onScopeDispose(cleanup)

  return { isDraft, phase, draftIntentId: readonly(draftIntentId), draftTitle: readonly(draftTitle), starting: readonly(starting), startNew, rename, send, retry }
}
