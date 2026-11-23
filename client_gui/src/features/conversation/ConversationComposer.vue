<template>
  <!-- bottom of the Conversation. Every control drives an existing
       contract through the session; nothing here opens a dialog — settings, the
       menus and the manual-copy fallback are inline. the earlier change (v9): input and send on
       the first line; AI picker, source mode icons, settings (⚙) and the ⋯ menu (mention copy,
       AI call) on the second. the earlier change the earlier implementation: `sourceLocked` turns the source mode icons off with the
       reason (the Flow's Graph run); nothing else changes. -->
  <form
    class="conv-compose"
    :class="{ 'is-dragging': dragging }"
    data-test="conv-composer"
    @submit.prevent="submit"
    @dragover="onDragOver"
    @dragleave="onDragLeave"
    @drop="onDrop"
  >
    <div v-if="manualCopyText" class="conv-manual" data-test="conv-manual-copy">
      <div class="conv-manual-head">
        <span>{{ t('gui.conversation.composer.manual_copy_title') }}</span>
        <button type="button" class="conv-link" data-test="conv-manual-copy-again" @click="emit('manual-copy-again')">
          {{ t('gui.conversation.composer.manual_copy_again') }}
        </button>
        <button type="button" class="conv-link" @click="emit('manual-copy-dismiss')">{{ t('gui.conversation.close') }}</button>
      </div>
      <textarea class="conv-manual-text" readonly spellcheck="false" :value="manualCopyText" @focus="($event.target as HTMLTextAreaElement).select()"></textarea>
    </div>

    <!-- pasted / dropped images wait here (thumbnail, upload state, ✕) until the message is sent. -->
    <ComposerImageStrip
      :images="attach.images.value"
      :notice="attach.notice.value"
      :hint="imageHint"
      @remove="attach.remove"
      @retry="attach.retry"
      @dismiss-notice="attach.dismissNotice"
    />

    <!-- Voice input: live partial text, "transcribing…" with 취소, and the last voice notice. -->
    <ComposerVoiceNotice :voice="voice" />

    <!-- flowgate.v02test.0072: quick message buttons right above the input; ＋ 추가 / ✎ 편집 / right-click edit are
         the row's own (only with project.settings.edit). -->
    <ComposerQuickMessages
      :items="quick.items.value"
      :visible="quick.visible.value"
      :is-on="quick.isOn"
      :disabled="!!readOnly"
      :can-edit="quick.canEdit.value"
      :editing="quick.editing.value"
      :save="(input, id) => (id === null ? quick.create(input) : quick.update(id, input))"
      :remove="quick.remove"
      :move="quick.move"
      @toggle="quick.toggle"
      @update:editing="quick.setEditing"
    />

    <div class="conv-pill" :class="{ 'has-side-stop': sendWhileRunning && stopMode !== null }">
      <textarea
        ref="inputEl"
        v-model="draft"
        class="conv-input"
        data-test="conv-input"
        rows="1"
        :placeholder="t('gui.conversation.composer.placeholder')"
        :aria-label="t('gui.conversation.composer.placeholder')"
        :disabled="disabled || readOnly || sending"
        @input="onInput"
        @paste="onPaste"
        @keydown="onQuickKey"
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <!-- Voice input: recording pill and mic (always rendered so the row keeps its grid columns). -->
      <ComposerVoiceBar :voice="voice" :disabled="voiceDisabled" />
      <!-- while the AI answers and something is typed, the message can be sent (its AI call
           follows the answer); the stop stays next to it. -->
      <button
        v-if="sendWhileRunning && stopMode !== null"
        type="button"
        class="conv-send is-stop conv-stop-side"
        :class="{ 'is-stopping': stopMode === 'requested' }"
        data-test="conv-stop"
        :title="stopTitle"
        :aria-label="stopTitle"
        :disabled="stopMode === 'requested'"
        @click="emit('stop')"
      >
        <span aria-hidden="true">{{ stopMode === 'requested' ? '…' : '■' }}</span>
      </button>
      <!-- while a chat AI run is in flight the send button is a STOP button. the earlier change B: with
           `stopState` it is one from the press of send on (chat created, message stored, AI call starting). -->
      <button
        :type="isStop ? 'button' : 'submit'"
        class="conv-send"
        :class="{ 'is-stop': isStop, 'is-stopping': stopMode === 'requested' }"
        data-test="conv-send"
        :title="sendTitle"
        :aria-label="sendTitle"
        :disabled="isStop ? stopMode === 'requested' : disabled || readOnly || sending || aiPending || (draft.trim() === '' && !hasQuick) || attach.blocked.value"
        @click="isStop && emit('stop')"
      >
        <!-- T: a stop under way is a muted spinning ring instead of ■ (… with reduced motion), so a
             pressed stop button no longer looks like the one that can still be pressed. -->
        <span v-if="stopMode === 'requested'" class="conv-send-stopping" data-test="conv-send-stopping" aria-hidden="true">
          <span class="conv-send-ring"></span>
          <span class="conv-send-dots">…</span>
        </span>
        <span v-else aria-hidden="true">{{ isStop ? '■' : sending ? '…' : '➤' }}</span>
      </button>
    </div>

    <!-- flowgate.v02gui.0007 A: a failed send says why on its own bubble (with "다시 시도") and in the timeline's error
         line; the composer no longer repeats it. -->
    <!-- while the chat's AI is still being read, send waits without any line under the input
         (the passing "checking" line was removed); if it could not be read, say why and offer a
         retry: nothing is sent on an AI the user did not see chosen. -->
    <div v-if="aiPickStatus === 'error'" class="conv-ai-pick is-error" role="alert" data-test="conv-ai-pick-error">
      <span>{{ aiPickError ? `${t('gui.conversation.composer.ai_pick_failed')} (${aiPickError})` : t('gui.conversation.composer.ai_pick_failed') }}</span>
      <button type="button" class="conv-link" data-test="conv-ai-pick-retry" @click="emit('retry-ai-pick')">
        {{ t('gui.conversation.composer.ai_pick_retry') }}
      </button>
    </div>
    <div v-if="submission?.phase === 'failed' && submission.stage !== 'send'" role="status" aria-live="polite" data-test="conv-submission">
      {{ t(`gui.conversation.composer.failed_${submission.stage}`, { attempt: submission.attempt }) }}
    </div>

    <div class="conv-assist">
      <ComposerModelPicker
        :providers="providers"
        :selected-id="selectedProviderId"
        :status="providersStatus"
        :disabled="readOnly"
        @select="(id) => emit('select-provider', id)"
      />
      <ComposerSourceModes
        :modes="settings.sourceAccessModes"
        :current="settings.sourceAccessMode"
        :pending="pendingSourceMode"
        :disabled="readOnly"
        :locked="sourceLocked"
        @set="(mode) => emit('set-source-mode', mode)"
      />
      <!-- 📎: opens / closes this Flow's attachment window (the container does it). The yellow badge counts the AI's
           upload requests still waiting for a decision. -->
      <Button
        variant="ghost"
        size="sm"
        class="conv-attach"
        :class="{ 'is-open': attachmentsOpen }"
        data-test="conv-attachments"
        :aria-pressed="attachmentsOpen"
        :title="attachLabel"
        :aria-label="attachLabel"
        @click="emit('toggle-attachments')"
      >
        <span aria-hidden="true">📎</span>
        <span v-if="attachmentsPending > 0" class="conv-attach-badge" data-test="conv-attachments-badge">{{ attachmentsPending }}</span>
      </Button>
      <span class="conv-assist-spacer"></span>
      <ComposerSettings
        :timeout-enabled="chatId !== null"
        :settings="settings"
        :saving="savingSettings"
        :has-providers="hasProviders"
        :disabled="readOnly"
        @save="(patch, done) => emit('save-settings', patch, done)"
      />
      <ComposerToolsMenu
        :has-providers="hasProviders"
        :running="running"
        :mention-disabled="disabled || chatId === null || copyingMention"
        :invoke-disabled="disabled || chatId === null || readOnly || running"
        @copy-mention="emit('copy-mention')"
        @invoke-ai="emit('invoke-ai')"
      />
    </div>
  </form>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ComposerImageStrip from './ComposerImageStrip.vue'
import ComposerModelPicker from './ComposerModelPicker.vue'
import ComposerQuickMessages from './ComposerQuickMessages.vue'
import ComposerSettings from './ComposerSettings.vue'
import ComposerSourceModes from './ComposerSourceModes.vue'
import ComposerToolsMenu from './ComposerToolsMenu.vue'
import ComposerVoiceBar from './ComposerVoiceBar.vue'
import ComposerVoiceNotice from './ComposerVoiceNotice.vue'
import { createComposerImages, isImageFile } from './composerImages'
import { draftKey, draftUserId, loadDraft, saveDraft, type DraftOwner, type DraftSendSnapshot } from './drafts'
import { buildOutgoingText, useQuickMessages } from './quickMessages/useQuickMessages'
import { createSubmissionTrace, type SubmissionTrace, type SubmissionPhase } from './submissionTrace'
import type { ChatProvider, ChatSettings, ChatSettingsPatch, SourceAccessMode } from './types'
import { deleteMessageAudio, type TurnAudio } from './voice/api'
import { installVoiceMessages } from './voice/messages'
import { useVoiceInput } from './voice/useVoiceInput'
import { loadVoice } from './voice/voiceStore'

const props = withDefaults(defineProps<{
  /** The open chat; null while a new chat is a draft (its first send creates it). */
  chatId: string | null
  /** The Flow's attachment window is open (the 📎 button shows pressed). */
  attachmentsOpen?: boolean
  /** AI upload requests of this chat still waiting for a decision (the 📎 badge; none shown at 0). */
  attachmentsPending?: number
  projectId?: string | null
  groupId?: string | null
  draftIntentId?: string | null
  /** Nothing can be sent now (no Flow, the chat is loading). */
  disabled: boolean
  readOnly?: boolean
  settings: DeepReadonly<ChatSettings>
  savingSettings: boolean
  pendingSourceMode: SourceAccessMode | null
  /** the Flow's Graph run locks the source access mode. */
  sourceLocked?: boolean
  providers: readonly ChatProvider[]
  selectedProviderId: string
  providersStatus: string
  /**
   * whether the open chat's AI is settled ('loading' / 'error': send waits). Absent = 'ready'.
   */
  aiPickStatus?: 'ready' | 'loading' | 'error'
  /** why the chat's AI could not be read (with 'error'). */
  aiPickError?: string | null
  hasProviders: boolean
  sending: boolean
  submission?: SubmissionPhase | null
  running: boolean
  runStarting: boolean
  cancelling: boolean
  /**
   * B: the stop button's state from the press of send on ('available' = can be pressed,
   * 'requested' = a stop is under way). Absent: the stop button follows `running` / `runStarting` / `cancelling`.
   */
  stopState?: 'none' | 'available' | 'requested'
  copyingMention: boolean
  manualCopyText: string | null
  /** Sends one message; resolves true when the server accepted it (the draft clears then). */
  onSend: (text: string, snapshot?: DraftSendSnapshot, trace?: SubmissionTrace) => Promise<boolean>
}>(), { attachmentsOpen: false, attachmentsPending: 0 })

const emit = defineEmits<{
  'toggle-attachments': []
  'select-provider': [providerId: string]
  'retry-ai-pick': []
  'save-settings': [patch: ChatSettingsPatch, done: (ok: boolean) => void]
  'set-source-mode': [mode: SourceAccessMode]
  'copy-mention': []
  'invoke-ai': []
  stop: []
  'manual-copy-again': []
  'manual-copy-dismiss': []
}>()

const { t } = useI18n()
installVoiceMessages(useI18n({ useScope: 'global' }))
const attachLabel = computed(() =>
  props.attachmentsPending > 0
    ? t('gui.conversation.composer.attachments_pending', { n: props.attachmentsPending })
    : t('gui.conversation.composer.attachments'),
)

const inputEl = ref<HTMLTextAreaElement | null>(null)
function currentOwner(): DraftOwner | null {
  if (!props.chatId && (!props.projectId || !props.groupId || !props.draftIntentId)) return null
  return { userId: draftUserId(), projectId: props.projectId ?? '', groupId: props.groupId ?? '',
    intentId: props.draftIntentId ?? '', chatId: props.chatId }
}

let visibleOwner = currentOwner()
// the attached images follow the same owner as the text (chat or new-chat draft).
const ownerRef = shallowRef<DraftOwner | null>(visibleOwner)
const attach = createComposerImages(() => ownerRef.value)
attach.restore(visibleOwner)
const draft = ref(loadDraft(visibleOwner))
const revisions = new Map<string, number>()
// `draftBody` = what the input held at the press (the snapshot body also carries the quick messages sent with it).
const pending = new Set<{ snapshot: DraftSendSnapshot; owners: DraftOwner[]; draftBody?: string }>()
const sameScope = (a: DraftOwner, b: DraftOwner) => a.userId === b.userId && a.projectId === b.projectId && a.groupId === b.groupId && a.intentId === b.intentId

watch(
  () => [props.projectId, props.groupId, props.draftIntentId, props.chatId, draftUserId()] as const,
  () => {
    const owner = currentOwner()
    // Only this send's explicitly bound null -> CH transition may carry its input.
    const transfer = owner && [...pending].find(({ snapshot }) => snapshot.owner.chatId === null &&
      snapshot.createdChatId === owner.chatId && sameScope(snapshot.owner, owner) &&
      visibleOwner && draftKey(visibleOwner) === draftKey(snapshot.owner))
    if (transfer && owner && visibleOwner && !loadDraft(owner)) {
      revisions.set(draftKey(owner), revisions.get(draftKey(visibleOwner)) ?? 0)
      saveDraft(owner, draft.value)
      attach.transfer(visibleOwner, owner)
      transfer.owners.push(owner)
    } else {
      draft.value = loadDraft(owner)
    }
    visibleOwner = owner
    ownerRef.value = owner
    attach.restore(owner)
    void nextTick(autoGrow)
  },
)

// ── flowgate.v02test.0072: quick messages — the row above the input and the text a send carries ──────────────
const quick = useQuickMessages({ projectId: () => props.projectId, userId: draftUserId })
watch(() => [props.projectId, draftUserId()] as const, () => { void quick.load() }, { immediate: true })
/** An on quick message lets an empty input be sent (the quick messages alone go out). */
const hasQuick = computed(() => quick.active.value.length > 0)

/** Alt+1..9 in the input turns the row's 1st..9th button on / off. */
function onQuickKey(event: KeyboardEvent): void {
  if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || event.isComposing) return
  const digit = /^Digit([1-9])$/.exec(event.code ?? '')?.[1] ?? /^[1-9]$/.exec(event.key ?? '')?.[0]
  if (!digit || props.readOnly) return
  if (quick.toggleAt(Number(digit) - 1)) event.preventDefault()
}

// null = no stop button (the send button shows).
const stopMode = computed<'available' | 'requested' | null>(() => {
  if (props.stopState !== undefined) return props.stopState === 'none' ? null : props.stopState
  if (!props.running) return null
  return props.cancelling || props.runStarting ? 'requested' : 'available'
})
const aiPending = computed(() => props.aiPickStatus !== undefined && props.aiPickStatus !== 'ready')
// an AI answering does not hold a typed message: the button sends it, the stop moves beside it.
const sendWhileRunning = computed(() => props.running && !props.sending && draft.value.trim() !== '')
const isStop = computed(() => stopMode.value !== null && !sendWhileRunning.value)

const stopTitle = computed(() =>
  props.cancelling || stopMode.value === 'requested' ? t('gui.conversation.composer.stopping') : t('gui.conversation.composer.stop'))

const sendTitle = computed(() => {
  if (isStop.value && (props.cancelling || (props.stopState !== undefined && stopMode.value === 'requested'))) return t('gui.conversation.composer.stopping')
  if (isStop.value) return t('gui.conversation.composer.stop')
  return t('gui.conversation.composer.send')
})

function autoGrow(): void {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 110)}px`
}

function onInput(): void {
  const owner = visibleOwner
  if (owner) {
    const key = draftKey(owner)
    revisions.set(key, (revisions.get(key) ?? 0) + 1)
    saveDraft(owner, draft.value)
  }
  autoGrow()
}

// ── the earlier change: message images — paste, drop, state of the attached ones ──────────────────────
const canAttach = computed(() => !props.disabled && !props.readOnly && !props.sending)
const dragging = ref(false)

/** Why the send is held now, for the note under the thumbnails. */
const imageHint = computed<'uploading' | 'checking' | 'unusable' | null>(() => {
  if (attach.images.value.length === 0) return null
  if (attach.uploading.value) return 'uploading'
  if (attach.checking.value) return 'checking'
  if (attach.blocked.value) return 'unusable'
  return null
})

function clipboardImages(data: DataTransfer | null): File[] {
  if (!data) return []
  const files = Array.from(data.files ?? []).filter(isImageFile)
  if (files.length) return files
  return Array.from(data.items ?? []).flatMap((item) => {
    const file = item.kind === 'file' && item.type.startsWith('image/') ? item.getAsFile() : null
    return file ? [file] : []
  })
}

function onPaste(event: ClipboardEvent): void {
  if (!canAttach.value) return
  const files = clipboardImages(event.clipboardData)
  // Text that comes with an image (a copied spreadsheet range, a web selection) is pasted as text, as before.
  if (!files.length || event.clipboardData?.getData('text/plain')) return
  event.preventDefault()
  attach.add(files)
}

const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')

// 0074 TR0014: a file dragged over the input never goes to the browser — also while sending or when the input is
// locked (the browser would open the image and leave the app). Then nothing is taken (dropEffect none). Drags
// without `Files` (text, quick message order) keep their default.
function onDragOver(event: DragEvent): void {
  if (!carriesFiles(event)) return
  event.preventDefault()
  if (!canAttach.value) {
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'none'
    return
  }
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  dragging.value = true
}

function onDragLeave(event: DragEvent): void {
  const next = event.relatedTarget as Node | null
  if (!next || !(event.currentTarget as HTMLElement | null)?.contains(next)) dragging.value = false
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  if (!carriesFiles(event)) return
  event.preventDefault()
  if (!canAttach.value) return
  attach.add(Array.from(event.dataTransfer?.files ?? []))
}

onBeforeUnmount(() => attach.dispose())

// ── Voice input: the mic records; the text goes into the input, or the recording is sent as a voice message ─────
const voiceDisabled = computed(() => props.disabled || !!props.readOnly || props.sending)
const voice = useVoiceInput({
  owner: currentOwner,
  onText: insertText,
  onAudio: sendAudio,
})
onMounted(() => { void loadVoice() })

/** Appends transcribed text to the input (a space apart from what is typed) and keeps it as the draft. */
function insertText(text: string): void {
  const current = draft.value
  const separator = current && !/\s$/.test(current) ? ' ' : ''
  draft.value = `${current}${separator}${text}`
  onInput()
  void nextTick(() => {
    autoGrow()
    const el = inputEl.value
    if (!el) return
    el.focus()
    el.setSelectionRange?.(draft.value.length, draft.value.length)
  })
}

/**
 * Sends an uploaded recording as its own message (body = the server's transcript, plus the audio id). What is typed
 * in the input stays there. When the message cannot go out now, the transcript is put into the input instead and the
 * unsent upload is removed, so nothing said is lost.
 */
async function sendAudio(audio: TurnAudio, owner: DraftOwner): Promise<boolean> {
  const current = currentOwner()
  if (!current || !sameScope(owner, current) || owner.chatId !== current.chatId) {
    void deleteMessageAudio(owner.groupId, audio.audioId).catch(() => undefined)
    return false
  }
  const body = audio.text.trim()
  const blocked = !owner || !body || props.disabled || props.readOnly || props.sending || aiPending.value ||
    [...pending].some((p) => draftKey(p.snapshot.owner) === draftKey(owner))
  if (blocked) {
    const flowId = owner.groupId
    if (flowId) void deleteMessageAudio(flowId, audio.audioId).catch(() => undefined)
    if (body) insertText(body)
    return false
  }
  const snapshot: DraftSendSnapshot = { owner: { ...owner }, body, revision: -1, audio: audio.audioId }
  const send = { snapshot, owners: [snapshot.owner] }
  pending.add(send)
  try {
    return await props.onSend(body, snapshot, createSubmissionTrace())
  } finally {
    pending.delete(send)
  }
}

async function submit(): Promise<void> {
  const trace = createSubmissionTrace()
  trace.mark('submit')
  const text = draft.value.trim()
  const owner = visibleOwner
  // the on quick messages (row order) go after the typed text; a hidden row (load failed) adds nothing.
  const quickSent = quick.active.value
  if (!owner || (!text && !quickSent.length) || props.disabled || props.readOnly || props.sending || aiPending.value) return
  // An image still uploading, failed or gone holds the send: it would otherwise be left out without a word.
  if (attach.blocked.value) return
  if ([...pending].some((p) => draftKey(p.snapshot.owner) === draftKey(owner))) return
  const imageIds = [...attach.readyIds.value]
  const draftBody = draft.value
  const outgoing = quickSent.length ? buildOutgoingText(text, quickSent) : text
  // With quick messages the snapshot body is the text that went out, so a retried bubble still matches this send.
  const snapshot: DraftSendSnapshot = { owner: { ...owner }, body: quickSent.length ? outgoing : draftBody,
    revision: revisions.get(draftKey(owner)) ?? 0, ...(imageIds.length ? { images: imageIds } : {}) }
  const send = { snapshot, owners: [snapshot.owner], draftBody }
  // Persist before the first await, even if this owner is hidden by the time it fails.
  saveDraft(owner, draftBody)
  pending.add(send)
  let ok = false
  try {
    ok = await props.onSend(outgoing, snapshot, trace)
  } finally {
    pending.delete(send)
  }
  if (!ok) return
  // accepted: the once (1회) buttons that went with it turn off; a failed send keeps them on.
  quick.consume(quickSent)
  // The server linked the images to the message: they leave the composer (they are not deleted).
  if (imageIds.length) attach.sent(send.owners, imageIds)
  for (const target of send.owners) {
    const key = draftKey(target)
    if ((revisions.get(key) ?? 0) !== snapshot.revision || loadDraft(target) !== draftBody) continue
    saveDraft(target, '')
    if (!visibleOwner || draftKey(visibleOwner) !== key || draft.value !== draftBody) continue
    draft.value = ''
    await nextTick()
    if (visibleOwner && draftKey(visibleOwner) === key) {
      autoGrow()
      inputEl.value?.focus()
    }
  }
}

defineExpose({ focus: () => inputEl.value?.focus() })
</script>

<style scoped>
.conv-compose {
  position: relative;
  flex: 0 0 auto;
  /* the AI picker's popover caps its width to this box (100cqw). */
  container-type: inline-size;
  padding: 7px 8px;
  border-top: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
}

.conv-ai-pick {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 4px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.conv-ai-pick.is-error {
  color: var(--fg2-status-danger-fg);
}

.conv-compose.is-dragging {
  outline: 2px dashed var(--fg2-blue);
  outline-offset: -3px;
}

.conv-manual {
  margin-bottom: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  padding: 7px 8px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.conv-manual-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--fg2-text);
  font-weight: 700;
}

.conv-manual-head > span {
  flex: 1;
}

.conv-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-weight: 400;
  cursor: pointer;
}

.conv-manual-text {
  width: 100%;
  min-height: 56px;
  box-sizing: border-box;
  resize: vertical;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: 10.5px/1.4 ui-monospace, SFMono-Regular, Consolas, monospace;
  padding: 5px;
}

.conv-pill {
  min-height: 43px;
  display: grid;
  /* input · voice (pill + mic, empty when voice input is not offered) · send */
  grid-template-columns: minmax(0, 1fr) auto 39px;
  align-items: end;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  overflow: hidden;
}

.conv-input {
  min-width: 0;
  min-height: 43px;
  max-height: 110px;
  height: 43px;
  resize: none;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--fg2-text);
  padding: 12px 9px 8px;
  font: 12px/1.35 var(--fg2-font);
}

.conv-input::placeholder {
  color: var(--fg2-muted-2);
}

/* send while the AI answers — the stop sits left of the send button. */
.conv-pill.has-side-stop {
  grid-template-columns: minmax(0, 1fr) auto 37px 39px;
}

.conv-send {
  width: 32px;
  height: 32px;
  margin: 5px 5px 5px 0;
  padding: 0;
  border: 1px solid var(--fg2-blue);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  display: grid;
  place-items: center;
  font-size: 14px;
  cursor: pointer;
}

.conv-send.is-stop {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.conv-send:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

/* T: a stop under way — muted, full opacity (a faded ring would be harder to see), spinning ring. */
.conv-send.is-stopping {
  border-color: var(--fg2-line-strong);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
}

.conv-send.is-stopping:disabled {
  cursor: progress;
  opacity: 1;
}

.conv-send-stopping {
  display: grid;
  place-items: center;
}

.conv-send-ring {
  display: block;
  width: 14px;
  height: 14px;
  box-sizing: border-box;
  border: 2px solid var(--fg2-line-strong);
  border-top-color: var(--fg2-muted);
  border-radius: 50%;
  animation: conv-send-spin 0.8s linear infinite;
}

.conv-send-dots {
  display: none;
}

@keyframes conv-send-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .conv-send-ring {
    display: none;
    animation: none;
  }

  .conv-send-dots {
    display: inline;
  }
}

.conv-send:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-assist {
  height: 29px;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 4px;
  margin-top: 5px;
}

.conv-assist-spacer {
  flex: 1;
  min-width: 0;
}

/* 📎: the shared ghost button, sized into the 29px assist row; theme tokens only. */
.conv-attach {
  position: relative;
  height: 26px;
  padding: 0 8px;
  gap: 4px;
}

.conv-attach.is-open {
  border-color: var(--fg2-line-strong);
  background: var(--fg2-control-surface-flat);
}

.conv-attach-badge {
  min-width: 16px;
  height: 16px;
  padding: 0 4px;
  box-sizing: border-box;
  border-radius: var(--fg2-control-radius-pill);
  border: 1px solid var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
  font-size: 11px;
  font-weight: 650;
  line-height: 14px;
  text-align: center;
}
</style>

