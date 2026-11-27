<template>
  <!-- the Conversation part for the Designer's right tool area:
       header (chat list / title / new chat) · participants · one chronological timeline ·
       composer (the v9 order). It owns no tab button, panel header,
       collapse or width: a Tool Panel Container puts this element in its body and
       gives it the height to fill.
       (WP answer 2): `sourceLocked` — the Flow's Graph run is running / paused / stopping: the
       AI source access mode cannot change (the reason on the mode icons); messages, AI calls and read use go on. A
       refused edit / edit-once call (409 graph_run_locked) says so in the notice.
       `subscribeEvents` (the container's project event stream) keeps the open chat in line with
       the server while it is open — shown or not — and `active` (the container shows this part) makes coming back to
       the Dock tab a return like the browser tab's. -->
  <section class="conv-panel" data-test="conversation-panel" :aria-label="t('gui.conversation.label')">
    <ConversationHeader
      :chats="state.chats"
      :chats-status="state.chatsStatus"
      :reconnecting="listReconnecting || offline"
      :active-chat="activeChat"
      :has-scope="state.scope !== null"
      :creating="state.creatingChat"
      :renaming="state.renaming"
      :read-only="readOnly"
      :draft="start.isDraft.value"
      :title="headerTitle"
      :flow-running="flowRunning || session.flowRunning.value"
      :chat-running="session.running.value"
      @select="session.selectChat"
      @rename="start.rename"
      @create="onCreate"
      @reload="session.reloadChats"
    />
    <ConversationParticipants :participants="state.participants" />
    <Alert
      v-if="state.notice"
      class="conv-notice"
      data-test="conv-notice"
      :tone="state.notice.tone"
      dismissible
      :dismiss-label="t('gui.conversation.close')"
      @dismiss="session.dismissNotice"
    >
      {{ noticeText }}
    </Alert>
    <ConversationTimeline
      :items="session.timeline.value"
      :status="state.turnsStatus === 'error' && offline ? 'loading' : state.turnsStatus"
      :has-more-before="state.hasMoreBefore"
      :loading-older="state.loadingOlder"
      :deciding="state.deciding"
      :empty-text="emptyText"
      :chat-id="state.activeChatId"
      :read-only="inputReadOnly"
      :checkpoint-seq="checkpointMode ? state.checkpointSeq : null"
      :checkpoint-kind="checkpointMode ? state.checkpointKind : null"
      :can-checkpoint="checkpointMode && session.supportsChatDocSettings && !inputReadOnly && !!state.activeChatId"
      :checkpoint-busy="state.savingCheckpoint"
      :my-participant-key="state.myParticipantKey"
      :reply-now="replyNowView"
      @mutate="session.mutateTurn"
      @reply-now="session.replyNow"
      @cancel-wait="session.cancelFlowWait"
      @retry-history="session.recover({ history: true })"
      @dismiss-history="session.dismissHistoryWarning"
      @load-older="session.loadOlder"
      @reload="reloadChat"
      @retry="start.retry"
      @decide="session.decideCommand"
      @open-change="openChange"
      @open-file="openChangeFile"
      @read="session.reportRead"
      @checkpoint="session.setCheckpoint"
    />
    <RunChangeWindow
      v-for="(view, index) in changeWindows"
      :key="view.key"
      :chat-id="view.chatId"
      :activity="view.activity"
      :requested-path="view.path"
      :request-version="view.requestVersion"
      :index="index"
      :load-diff="source.fetchRunFileDiff"
      @close="closeChange(view.key)"
    />
    <!-- Sending, editing and deleting a message are the server's project.document.update: when it says no, the input and the
         message menus are off and one line says why (flowgate.v02test.0090). -->
    <PermissionNote v-if="editRefused" :project-id="projectId" :actions="['project.document.update']" data-test="conv-permission-note" />
    <ConversationComposer
      ref="composerEl"
      :chat-id="state.activeChatId"
      :project-id="state.scope?.projectId ?? null"
      :group-id="state.scope?.groupId ?? null"
      :draft-intent-id="start.draftIntentId.value"
      :disabled="composerDisabled"
      :read-only="inputReadOnly"
      :settings="state.settings"
      :saving-settings="state.savingSettings"
      :pending-source-mode="state.pendingSourceMode"
      :source-locked="sourceLocked"
      :providers="state.providers"
      :selected-provider-id="state.selectedProviderId"
      :default-provider-id="state.defaultProviderId"
      :providers-status="state.providersStatus"
      :ai-pick-status="state.activeChatId !== null ? state.chatProviderStatus : 'ready'"
      :ai-pick-error="state.chatProviderError"
      :has-providers="session.hasProviders.value"
      :sending="state.sending || start.starting.value"
      :submission="start.phase.value"
      :running="session.running.value"
      :run-starting="state.runId === 'starting'"
      :cancelling="state.cancelling"
      :stop-state="stopState"
      :copying-mention="state.copyingMention"
      :manual-copy-text="state.manualCopyText"
      :on-send="start.send"
      :attachments-open="attachmentsOpen"
      :attachments-pending="attachmentsPending"
      @toggle-attachments="emit('toggle-attachments')"
      @select-provider="session.selectProvider"
      @retry-ai-pick="session.retryChatProvider"
      @save-settings="onSaveSettings"
      @set-source-mode="session.setSourceMode"
      @copy-mention="copyMention(false)"
      @invoke-ai="session.invokeAi"
      @stop="onStop"
      @manual-copy-again="copyManualAgain"
      @manual-copy-dismiss="session.setManualCopyText(null)"
    />
  </section>
</template>

<script setup lang="ts">
import { useRequestRecovery } from '@shared/requestRecovery'
import { useSelfHeal } from '@shared/selfHeal'
import { connectionLost } from '@shared/connectionStatus'
// The panel is handed its scope (the current Project and the active Flow's v0.1 group id)
// and creates one Conversation session for its own lifetime. A container that already has a
// session-wide data source (or a test) passes `source`; otherwise the HTTP source of the
// existing contracts is used. The panel owns modeless run-change windows (0151 T-B); the
// optional `open-change` event remains a notification. File contents stay outside the log.
// with no chat open the composer starts one — [+] only leaves
// the open chat, and the chat is created by its first send (chatStart.ts).
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../components/ui/Alert.vue'
import PermissionNote from '../../components/ui/PermissionNote.vue'
import { useCapabilities } from '@shared/permissions/capabilities'
import { copyToClipboard } from '../../core/interaction/clipboard'
import { createChatStart } from './chatStart'
import ConversationComposer from './ConversationComposer.vue'
import ConversationHeader from './ConversationHeader.vue'
import ConversationParticipants from './ConversationParticipants.vue'
import ConversationTimeline from './ConversationTimeline.vue'
import RunChangeWindow from './RunChangeWindow.vue'
import { REVISIT_CATCHUP_MIN_MS, createConversationSession } from './conversationSession'
import { useChatEvents, type SubscribeChatEvents } from './conversationEvents'
import { createHttpConversationSource, type ConversationSource } from './conversationSource'
import { ATTACHMENT_LINK_CONTEXT, ATTACHMENT_REQUEST_BULK, ATTACHMENT_REQUEST_REFRESH, ATTACHMENT_REQUEST_ROW, type AttachmentLinkContext, type ChatSettingsPatch, type ChatSummary, type WorkChangeActivity } from './types'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    /** The active Flow's v0.1 group id (FlowRef.groupId), or null when no Flow is open. */
    groupId: string | null
    source?: ConversationSource
    readOnly?: boolean
    /** the Flow's Graph run locks the AI source access mode. */
    sourceLocked?: boolean
    /** Confirmed Flow run activity, separate from archive / unknown edit locks. */
    flowRunning?: boolean
    /** the project event stream; without it the chat hears no server event. */
    subscribeEvents?: SubscribeChatEvents
    /** the AI attachment request row the container draws requests with. */
    attachmentRequestRow?: Component
    /** the approve all / reject all bar of a request block. */
    attachmentRequestBulk?: Component
    /** the container shows this part (false while another Dock tab is shown). */
    active?: boolean
    /** the Flow's attachment window is open (the 📎 button of the input shows it pressed). */
    attachmentsOpen?: boolean
    /** Waiting AI attachment requests of the whole Flow (every chat and card), for the badge of the 📎 button; the container reads it. */
    attachmentsPending?: number
    /**
     * v02test.0074: shows one file of a Flow in that Flow's attachment window (the container's `focusAttachment`); the
     * name of a message's file chip calls it. Resolves false when the file or the window is not there.
     */
    focusAttachment?: (flowId: string, key: string) => Promise<boolean>
  }>(),
  { source: undefined, readOnly: false, sourceLocked: false, flowRunning: false, subscribeEvents: undefined, active: true, attachmentRequestRow: undefined, attachmentRequestBulk: undefined, attachmentsOpen: false, attachmentsPending: 0, focusAttachment: undefined },
)

// The server's "no" for changing this project's documents (a chat is one): the input, send and the message menus go off
// with it. No answer yet changes nothing — the server still checks every send and its 403 shows the common box.
const projectCaps = useCapabilities(() => props.projectId)
const editRefused = computed(() => projectCaps.refusal('project.document.update') !== null)
const inputReadOnly = computed(() => props.readOnly || editRefused.value)

const emit = defineEmits<{
  'open-change': [activity: WorkChangeActivity]
  /** the 📎 button of the input: the container opens / closes the Flow's attachment window. */
  'toggle-attachments': []
}>()

// the attachment request blocks of the timeline draw their rows with the container's component.
provide(ATTACHMENT_REQUEST_ROW, computed(() => props.attachmentRequestRow ?? null))
provide(ATTACHMENT_REQUEST_BULK, computed(() => props.attachmentRequestBulk ?? null))
// v02test.0074: a message's file chips download from this chat's Flow and go to the attachment window through the container.
provide<AttachmentLinkContext>(ATTACHMENT_LINK_CONTEXT, {
  flowId: () => props.groupId,
  focus: (flowId, key) => props.focusAttachment?.(flowId, key) ?? Promise.resolve(false),
})

const { t, te } = useI18n()

const source = props.source ?? createHttpConversationSource()
const session = createConversationSession({
  source,
  onAutoCopyMention: () => void copyMention(true),
})
const state = session.state
// a request block reads the activities again after a bulk call (a failed one too).
provide(ATTACHMENT_REQUEST_REFRESH, () => session.recover({ activities: true }))
const start = createChatStart({ session, source, defaultTitle: () => t('gui.conversation.header.new_title') })
const composerEl = ref<InstanceType<typeof ConversationComposer> | null>(null)

const activeChat = computed(() => (session.activeChat.value as ChatSummary | null) ?? null)

// One window per run; separate runs can stay open together. The epoch is advanced by
// clearChat, including a scope change or A -> B -> A within one render tick.
interface ChangeWindow {
  key: number
  chatId: string
  activity: WorkChangeActivity
  path?: string
  requestVersion: number
}
const changeWindows = ref<ChangeWindow[]>([])
let nextChangeWindow = 0
watch(() => state.changeWindowEpoch, () => { changeWindows.value = [] }, { flush: 'sync' })

function showChange(activity: WorkChangeActivity, path?: string): void {
  const chatId = state.activeChatId
  if (!chatId) return
  const existing = changeWindows.value.find((view) => view.chatId === chatId && view.activity.runId === activity.runId)
  if (existing) {
    existing.activity = activity
    existing.path = path
    existing.requestVersion += 1
  } else {
    changeWindows.value.push({ key: ++nextChangeWindow, chatId, activity, path, requestVersion: 0 })
  }
}

function openChange(activity: WorkChangeActivity): void {
  showChange(activity)
  emit('open-change', activity)
}

function openChangeFile(runId: string, path: string): void {
  const activity = session.timeline.value.find((item) => item.kind === 'change' && item.activity.runId === runId)
  if (activity?.kind === 'change') showChange(activity.activity, path)
}

function closeChange(key: number): void {
  changeWindows.value = changeWindows.value.filter((view) => view.key !== key)
}

const headerTitle = computed(() => {
  if (activeChat.value) return activeChat.value.title
  return start.isDraft.value ? (start.draftTitle.value ?? t('gui.conversation.header.new_title')) : ''
})

// flowgate.v02test.0025: the chat's checkpoint (⋯ menu, divider) exists only under "체크포인트 사용"; another context
// setting hides it but keeps it stored, so choosing "체크포인트 사용" again applies it again.
const checkpointMode = computed(() => (state.settings.contextMode === 'checkpoint' || state.settings.contextMode === 'checkpoint_recent'))

// A draft can be written once the Flow's chat list has been read (a send before that could
// race the newest chat opening); an open chat once its turns are on screen.
const composerDisabled = computed(() => {
  if (!state.scope) return true
  if (state.activeChatId === null) return state.chatsStatus === 'loading' || state.chatsStatus === 'idle'
  return state.turnsStatus !== 'ready'
})

watch(
  () => [props.projectId, props.groupId] as const,
  ([projectId, groupId]) => {
    void session.setScope(projectId && groupId ? { projectId, groupId } : null)
  },
  { immediate: true },
)

const listReconnecting = useRequestRecovery(
  () => `${props.projectId}:${props.groupId}`,
  () => state.chatsStatus === 'loading',
  notice => notice.path.endsWith('/api/v1/documents') && notice.projectId === props.projectId &&
    notice.groupId === props.groupId && notice.typeCode === 'CH',
)

// 0060: while the server is unreachable the App Shell shows one banner; this part says it is reconnecting instead of
// showing its own failures, and reads again by itself.
const offline = computed(() => connectionLost.value)

// 0060: a failed chat list and a chat whose read failed read again by themselves (5 s, 10 s, 20 s, then every 30 s)
// instead of waiting for the event stream's resync, which may never come.
useSelfHeal({
  failed: () => state.scope !== null && state.chatsStatus === 'error',
  busy: () => state.chatsStatus === 'loading',
  reload: () => void session.recoverChatList(),
})
useSelfHeal({
  failed: () => state.activeChatId !== null && state.turnsStatus === 'error',
  busy: () => state.turnsStatus === 'loading',
  reload: () => void session.recover(),
})

// 0060: a message whose send was cut off (no answer, 5xx) goes out again by itself once the server answers: when the
// connection is back and whenever the open chat has been read again. Same idempotency key — never stored twice.
function resendInterrupted(): void {
  void session.resendInterrupted()
}
watch(() => state.turnsStatus, (status, before) => {
  if (status === 'ready' && before !== 'ready') resendInterrupted()
})

const emptyText = computed(() => {
  if (!state.scope) return t('gui.conversation.timeline.no_flow')
  if (state.chatsStatus === 'loading' || (state.chatsStatus === 'error' && offline.value)) return t(listReconnecting.value || offline.value ? 'gui.conversation.header.reconnecting' : 'gui.conversation.header.list_loading')
  if (state.chatsStatus === 'error') return t('gui.conversation.header.list_failed')
  return t('gui.conversation.timeline.empty')
})

const noticeText = computed(() => {
  const notice = state.notice
  if (!notice) return ''
  const key = `gui.conversation.notice.${notice.code}`
  const label = te(key) ? t(key) : notice.code
  // only a separate server diagnostic is added, never the notice again.
  return notice.detail && notice.detail !== label ? `${label} (${notice.detail})` : label
})

function onCreate(): void {
  start.startNew()
  void nextTick(() => composerEl.value?.focus())
}

function reloadChat(): void {
  if (state.activeChatId) void session.selectChat(state.activeChatId)
}

// B: the stop button from the press of send on. The session owns it once the message is being
// stored (and while the AI call starts or runs); before that, while a new chat is being created, chatStart does.
const stopState = computed(() => {
  const own = session.startStop.value
  return own !== 'none' ? own : start.stopState.value
})
// flowgate.v02test.0067 card 3: [지금 멈추고 다시 답하기] sits on the follow-up line, only while a message waits.
const replyNowView = computed(() => {
  const view = session.followUp.value
  return view && view.reason === 'run' && !inputReadOnly.value ? { stopping: view.stopping, editsFiles: view.editsFiles } : undefined
})
// flowgate.v02test.0067 #2: the Designer saw the Flow's run lock go off — a call waiting for the Flow looks again now.
watch(() => props.sourceLocked, (locked, was) => {
  if (was && !locked) session.wakeFlowWait()
})
function onStop(): void {
  if (session.startStop.value !== 'none') void session.stopRun()
  else start.stopCreate()
}

async function onSaveSettings(patch: ChatSettingsPatch, done: (ok: boolean) => void): Promise<void> {
  done(await session.saveSettings(patch))
}

// The mention exists only server-side (0293); a copy the browser refuses falls back to the
// inline manual-copy panel, never to a full-screen dialog (0240). An automatic copy after a
// send stays silent when it works.
async function copyMention(auto: boolean): Promise<void> {
  const mention = await session.issueMention()
  if (!mention) return
  const ok = await copyToClipboard(mention)
  session.setManualCopyText(ok ? null : mention)
  if (ok && !auto) session.notify('success', 'mention_copied')
}

async function copyManualAgain(): Promise<void> {
  const text = state.manualCopyText
  if (!text) return
  if (await copyToClipboard(text)) session.setManualCopyText(null)
}

// every server event about the open chat (a message, its AI run starting or ending, a command
// request, a run's changes) and a reconnect of the stream recover what it may have changed — also while the part is
// hidden behind another Dock tab, so it is current when shown again.
useChatEvents(
  props.subscribeEvents,
  () => {
    const scope = state.scope
    const chatId = state.activeChatId
    return scope && chatId ? { projectId: scope.projectId, groupId: scope.groupId, chatId } : null
  },
  (parts) => void session.recover(parts),
  // B3: a chat list whose read failed (dropped connection, server restart) used to stay failed
  // until [다시 시도] was pressed. When the stream reconnects or the browser is online again it reads again by itself.
  { projectId: () => state.scope?.projectId ?? null, restored: () => void session.recoverChatList() },
)

// T-A2 (NR ②): coming back to the panel — the tab visible again, the window focused again —
// catches the open chat up. At most once per REVISIT_CATCHUP_MIN_MS (both events fire on one return), never while
// hidden. the earlier change: events missed meanwhile (a dropped stream, a throttled background tab) are why the
// return recovers everything — the chat's active run included, which the open chat may have missed — and a run
// being polled is not skipped any more: its poll reads now instead of at the end of its wait. Showing the Dock's
// chat tab again is a return too; a window focus while another Dock tab is shown is not.
let lastRevisitCatchUp = Number.NEGATIVE_INFINITY

function onRevisit(): void {
  if (document.visibilityState === 'hidden' || !props.active) return
  // B3: a failed chat list reads again on return as well (no-op unless it failed).
  void session.recoverChatList()
  // ③: a chat whose first read failed is read again too (session.recover opens it again).
  if (!state.activeChatId || (state.turnsStatus !== 'ready' && state.turnsStatus !== 'error')) return
  const now = Date.now()
  if (now - lastRevisitCatchUp < REVISIT_CATCHUP_MIN_MS) return
  lastRevisitCatchUp = now
  void session.recover()
}

watch(
  () => props.active,
  (active, before) => {
    if (active && !before) onRevisit()
  },
)

function onVisibilityChange(): void {
  if (document.visibilityState === 'visible') onRevisit()
}

onMounted(() => {
  document.addEventListener('visibilitychange', onVisibilityChange)
  window.addEventListener('focus', onRevisit)
  window.addEventListener('fg:connection_recovered', resendInterrupted)
  window.addEventListener('online', resendInterrupted)
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('focus', onRevisit)
  window.removeEventListener('fg:connection_recovered', resendInterrupted)
  window.removeEventListener('online', resendInterrupted)
})

onBeforeUnmount(() => session.dispose())

defineExpose({ session })
</script>

<style scoped>
/* Fills whatever body the container gives it and never scrolls itself; the log scrolls. */
.conv-panel {
  height: 100%;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
}

.conv-notice {
  flex: 0 0 auto;
  margin: 6px 8px 0;
  font-size: 11px;
}
</style>
