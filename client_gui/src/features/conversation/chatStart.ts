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
import { computed, readonly, ref, watch, type ComputedRef, type Ref } from 'vue'
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
  const owed = new Map<string, { title: string; projectId: string }>()

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
      if (typed === null) owed.set(result.chatId, { title, projectId: result.scope.projectId })
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

  /** `chatId` is the chat the message was sent to, which may no longer be the one on screen. */
  function settle(chatId: string | null, ok: boolean): boolean {
    const due = chatId ? owed.get(chatId) : undefined
    if (ok && chatId && due) {
      owed.delete(chatId)
      void requestTitle(chatId, due.title, due.projectId)
    }
    return ok
  }

  const titleOf = (chatId: string) => state.chats.find((c) => c.id === chatId)?.title

  // A title typed before the ask stops it. A chat no longer listed (another Flow is open) is
  // still asked: the server keeps a renamed chat from the AI title anyway.
  const renamed = (chatId: string, expected: string) => {
    const shown = titleOf(chatId)
    return shown !== undefined && shown !== expected
  }

  async function requestTitle(chatId: string, expected: string, projectId: string): Promise<void> {
    if (!source.autoTitleChat) return
    await session.providersLoaded()
    // The selected AI is the one of the project the chat was made in.
    if (state.scope?.projectId !== projectId) return
    const providerId = state.selectedProviderId
    if (!session.hasProviders.value || !providerId || renamed(chatId, expected)) return
    try {
      const saved = await source.autoTitleChat(chatId, providerId)
      // A title typed while the AI was answering stays on screen as well.
      if (titleOf(chatId) === expected) session.setChatTitle(chatId, saved.title)
    } catch (error) {
      // R0001 §2.2-4: no AI title — the chat and its messages stay as they are. Without an
      // answer at all (the request timed out or the connection dropped) the server may still
      // have written it: the list is read again, as its [☰] would.
      if (error instanceof ConversationRequestError && error.status === null && state.scope?.projectId === projectId) {
        void session.reloadChats()
      }
    }
  }

  return { isDraft, phase, draftIntentId: readonly(draftIntentId), draftTitle: readonly(draftTitle), starting: readonly(starting), startNew, rename, send, retry }
}
