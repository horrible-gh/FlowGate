<template>
  <!-- flowgate.v02.0031 — the Conversation part for the Designer's right tool area:
       header (chat list / title / new chat) · one chronological timeline · participants ·
       composer. It owns no tab button, panel header, collapse or width (R0001 §10): a Tool
       Panel Container puts this element in its body and gives it the height to fill. -->
  <section class="conv-panel" data-test="conversation-panel" :aria-label="t('gui.conversation.label')">
    <ConversationHeader
      :chats="state.chats"
      :chats-status="state.chatsStatus"
      :active-chat="activeChat"
      :has-scope="state.scope !== null"
      :creating="state.creatingChat"
      :renaming="state.renaming"
      :read-only="readOnly"
      @select="session.selectChat"
      @rename="session.renameActiveChat"
      @create="onCreate"
      @reload="session.reloadChats"
    />
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
      :file-diff="state.fileDiff"
      @load-older="session.loadOlder"
      @reload="reloadChat"
      @retry="session.retry"
      @decide="session.decideCommand"
      @open-change="(activity) => emit('open-change', activity)"
      @open-file="session.openChangeFile"
      @close-file="session.closeChangeFile"
      @read="session.reportRead"
    />
    <ConversationParticipants :participants="state.participants" />
    <ConversationComposer
      :chat-id="state.activeChatId"
      :disabled="state.activeChatId === null || state.turnsStatus !== 'ready'"
      :read-only="readOnly"
      :settings="state.settings"
      :saving-settings="state.savingSettings"
      :pending-source-mode="state.pendingSourceMode"
      :providers="state.providers"
      :selected-provider-id="state.selectedProviderId"
      :providers-status="state.providersStatus"
      :has-providers="session.hasProviders.value"
      :sending="state.sending"
      :running="session.running.value"
      :run-starting="state.runId === 'starting'"
      :cancelling="state.cancelling"
      :copying-mention="state.copyingMention"
      :manual-copy-text="state.manualCopyText"
      :on-send="session.send"
      @select-provider="session.selectProvider"
      @save-settings="onSaveSettings"
      @set-source-mode="session.setSourceMode"
      @copy-mention="copyMention(false)"
      @invoke-ai="session.invokeAi"
      @stop="session.stopRun"
      @manual-copy-again="copyManualAgain"
      @manual-copy-dismiss="session.setManualCopyText(null)"
    />
  </section>
</template>

<script setup lang="ts">
// The panel is handed its scope (the current Project and the active Flow's v0.1 group id)
// and creates one Conversation session for its own lifetime. A container that already has a
// session-wide data source (or a test) passes `source`; otherwise the HTTP source of the
// existing contracts is used. Opening a work change as a whole is a request to the container
// (`open-change`); one file of it opens its stored diff right under the card (flowgate.v02.0032,
// D0006 §6.1).
import { computed, onBeforeUnmount, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Alert from '../../components/ui/Alert.vue'
import { copyToClipboard } from '../../core/interaction/clipboard'
import ConversationComposer from './ConversationComposer.vue'
import ConversationHeader from './ConversationHeader.vue'
import ConversationParticipants from './ConversationParticipants.vue'
import ConversationTimeline from './ConversationTimeline.vue'
import { createConversationSession } from './conversationSession'
import type { ConversationSource } from './conversationSource'
import type { ChatSettingsPatch, ChatSummary, WorkChangeActivity } from './types'

const props = defineProps<{
  projectId: string | null
  /** The active Flow's v0.1 group id (FlowRef.groupId), or null when no Flow is open. */
  groupId: string | null
  source?: ConversationSource
  readOnly?: boolean
}>()

const emit = defineEmits<{
  'open-change': [activity: WorkChangeActivity]
}>()

const { t, te } = useI18n()

const session = createConversationSession({
  source: props.source,
  onAutoCopyMention: () => void copyMention(true),
})
const state = session.state

const activeChat = computed(() => (session.activeChat.value as ChatSummary | null) ?? null)

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
  if (!state.activeChatId) return t('gui.conversation.timeline.no_chat')
  return t('gui.conversation.timeline.empty')
})

const noticeText = computed(() => {
  const notice = state.notice
  if (!notice) return ''
  const key = `gui.conversation.notice.${notice.code}`
  const label = te(key) ? t(key) : notice.code
  return notice.detail ? `${label} (${notice.detail})` : label
})

function onCreate(): void {
  void session.createChat(t('gui.conversation.header.new_title'))
}

function reloadChat(): void {
  if (state.activeChatId) void session.selectChat(state.activeChatId)
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
