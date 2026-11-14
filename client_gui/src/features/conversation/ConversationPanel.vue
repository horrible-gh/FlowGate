<template>
  <!-- flowgate.v02.0031 — the Conversation part for the Designer's right tool area:
       header (chat list / title / new chat) · participants · one chronological timeline ·
       composer (flowgate.v02.0047: the v9 order). It owns no tab button, panel header,
       collapse or width (R0001 §10): a Tool Panel Container puts this element in its body and
       gives it the height to fill.
       flowgate.v02.0061 T#14 (WP answer 2): `sourceLocked` — the Flow's Graph run is running / paused / stopping: the
       AI source access mode cannot change (the reason on the mode icons); messages, AI calls and read use go on. A
       refused edit / edit-once call (409 graph_run_locked) says so in the notice.
       flowgate.v02test.0013: `subscribeEvents` (the container's project event stream) keeps the open chat in line with
       the server while it is open — shown or not — and `active` (the container shows this part) makes coming back to
       the Dock tab a return like the browser tab's. -->
  <section class="conv-panel" data-test="conversation-panel" :aria-label="t('gui.conversation.label')">
    <ConversationHeader
      :chats="state.chats"
      :chats-status="state.chatsStatus"
      :active-chat="activeChat"
      :has-scope="state.scope !== null"
      :creating="state.creatingChat"
      :renaming="state.renaming"
      :read-only="readOnly"
      :draft="start.isDraft.value"
      :title="headerTitle"
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
      :status="state.turnsStatus"
      :has-more-before="state.hasMoreBefore"
      :loading-older="state.loadingOlder"
      :deciding="state.deciding"
      :empty-text="emptyText"
      :chat-id="state.activeChatId"
      :read-only="readOnly"
      :checkpoint-seq="checkpointMode ? state.checkpointSeq : null"
      :checkpoint-kind="checkpointMode ? state.checkpointKind : null"
      :can-checkpoint="checkpointMode && session.supportsChatDocSettings && !readOnly && !!state.activeChatId"
      :checkpoint-busy="state.savingCheckpoint"
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
    <ConversationComposer
      ref="composerEl"
      :chat-id="state.activeChatId"
      :project-id="state.scope?.projectId ?? null"
      :group-id="state.scope?.groupId ?? null"
      :draft-intent-id="start.draftIntentId.value"
      :disabled="composerDisabled"
      :read-only="readOnly"
      :settings="state.settings"
      :saving-settings="state.savingSettings"
      :pending-source-mode="state.pendingSourceMode"
      :source-locked="sourceLocked"
      :providers="state.providers"
      :selected-provider-id="state.selectedProviderId"
      :providers-status="state.providersStatus"
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
      @select-provider="session.selectProvider"
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
// The panel is handed its scope (the current Project and the active Flow's v0.1 group id)
// and creates one Conversation session for its own lifetime. A container that already has a
// session-wide data source (or a test) passes `source`; otherwise the HTTP source of the
// existing contracts is used. The panel owns modeless run-change windows (0151 T-B); the
// optional `open-change` event remains a notification. File contents stay outside the log.
// flowgate.v02.0047: with no chat open the composer starts one — [+] only leaves
// the open chat, and the chat is created by its first send (chatStart.ts).
import { computed, nextTick, onBeforeUnmount, onMounted, provide, ref, watch, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../components/ui/Alert.vue'
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
import { ATTACHMENT_REQUEST_BULK, ATTACHMENT_REQUEST_REFRESH, ATTACHMENT_REQUEST_ROW, type ChatSettingsPatch, type ChatSummary, type WorkChangeActivity } from './types'

const props = withDefaults(
  defineProps<{
    projectId: string | null
    /** The active Flow's v0.1 group id (FlowRef.groupId), or null when no Flow is open. */
    groupId: string | null
    source?: ConversationSource
    readOnly?: boolean
    /** flowgate.v02.0061 T#14: the Flow's Graph run locks the AI source access mode. */
    sourceLocked?: boolean
    /** flowgate.v02test.0013: the project event stream; without it the chat hears no server event. */
    subscribeEvents?: SubscribeChatEvents
    /** flowgate.chat.0017 CH0002: the AI attachment request row the container draws requests with. */
    attachmentRequestRow?: Component
    /** flowgate.chat.0017 CH0002: the approve all / reject all bar of a request block. */
    attachmentRequestBulk?: Component
    /** flowgate.v02test.0013: the container shows this part (false while another Dock tab is shown). */
    active?: boolean
  }>(),
  { source: undefined, readOnly: false, sourceLocked: false, subscribeEvents: undefined, active: true, attachmentRequestRow: undefined, attachmentRequestBulk: undefined },
)

const emit = defineEmits<{
  'open-change': [activity: WorkChangeActivity]
}>()

// flowgate.chat.0017 CH0002: the attachment request blocks of the timeline draw their rows with the container's component.
provide(ATTACHMENT_REQUEST_ROW, computed(() => props.attachmentRequestRow ?? null))
provide(ATTACHMENT_REQUEST_BULK, computed(() => props.attachmentRequestBulk ?? null))

const { t, te } = useI18n()

const source = props.source ?? createHttpConversationSource()
const session = createConversationSession({
  source,
  onAutoCopyMention: () => void copyMention(true),
})
const state = session.state
// flowgate.chat.0017 CH0002: a request block reads the activities again after a bulk call (a failed one too).
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

// flowgate.v02test.0025: the chat's checkpoint (⋯ menu, divider) exists only under 체크포인트 사용; another context
// setting hides it but keeps it stored, so choosing 체크포인트 사용 again applies it again.
const checkpointMode = computed(() => state.settings.contextMode === 'checkpoint')

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

const emptyText = computed(() => {
  if (!state.scope) return t('gui.conversation.timeline.no_flow')
  if (state.chatsStatus === 'loading') return t('gui.conversation.header.list_loading')
  if (state.chatsStatus === 'error') return t('gui.conversation.header.list_failed')
  return t('gui.conversation.timeline.empty')
})

const noticeText = computed(() => {
  const notice = state.notice
  if (!notice) return ''
  const key = `gui.conversation.notice.${notice.code}`
  const label = te(key) ? t(key) : notice.code
  // flowgate.v02.0088 F12: only a separate server diagnostic is added, never the notice again.
  return notice.detail && notice.detail !== label ? `${label} (${notice.detail})` : label
})

function onCreate(): void {
  start.startNew()
  void nextTick(() => composerEl.value?.focus())
}

function reloadChat(): void {
  if (state.activeChatId) void session.selectChat(state.activeChatId)
}

// flowgate.v02gui.0011 B: the stop button from the press of send on. The session owns it once the message is being
// stored (and while the AI call starts or runs); before that, while a new chat is being created, chatStart does.
const stopState = computed(() => {
  const own = session.startStop.value
  return own !== 'none' ? own : start.stopState.value
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

// flowgate.v02test.0013: every server event about the open chat (a message, its AI run starting or ending, a command
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
)

// flowgate.v02.0151 T-A2 (NR §2.6-3 ②): coming back to the panel — the tab visible again, the window focused again —
// catches the open chat up. At most once per REVISIT_CATCHUP_MIN_MS (both events fire on one return), never while
// hidden. flowgate.v02test.0013: events missed meanwhile (a dropped stream, a throttled background tab) are why the
// return recovers everything — the chat's active run included, which the open chat may have missed — and a run
// being polled is not skipped any more: its poll reads now instead of at the end of its wait. Showing the Dock's
// chat tab again is a return too; a window focus while another Dock tab is shown is not.
let lastRevisitCatchUp = Number.NEGATIVE_INFINITY

function onRevisit(): void {
  if (document.visibilityState === 'hidden' || !props.active) return
  if (!state.activeChatId || state.turnsStatus !== 'ready') return
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
})

onBeforeUnmount(() => {
  document.removeEventListener('visibilitychange', onVisibilityChange)
  window.removeEventListener('focus', onRevisit)
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
