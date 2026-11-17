<template>
  <!-- flowgate.v02.0031 — bottom of the Conversation (R0001 §8). Every control drives an existing
       contract through the session; nothing here opens a dialog (0235 R0001) — settings, the
       menus and the manual-copy fallback are inline. flowgate.v02.0047 (v9): input and send on
       the first line; AI picker, source mode icons, settings (⚙) and the ⋯ menu (mention copy,
       AI call) on the second. flowgate.v02.0061 T#14: `sourceLocked` turns the source mode icons off with the
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

    <!-- flowgate.v02test.0029: pasted / dropped images wait here (thumbnail, upload state, ✕) until the message is sent. -->
    <ComposerImageStrip
      :images="attach.images.value"
      :notice="attach.notice.value"
      :unsupported-ai="unsupportedAi"
      :hint="imageHint"
      @remove="attach.remove"
      @retry="attach.retry"
      @dismiss-notice="attach.dismissNotice"
    />

    <div class="conv-pill">
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
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <!-- 0264 R0001: while a chat AI run is in flight the send button is a STOP button. flowgate.v02gui.0011 B: with
           `stopState` it is one from the press of send on (chat created, message stored, AI call starting). -->
      <button
        :type="isStop ? 'button' : 'submit'"
        class="conv-send"
        :class="{ 'is-stop': isStop, 'is-stopping': stopMode === 'requested' }"
        data-test="conv-send"
        :title="sendTitle"
        :aria-label="sendTitle"
        :disabled="isStop ? stopMode === 'requested' : disabled || readOnly || sending || draft.trim() === '' || attach.blocked.value"
        @click="isStop && emit('stop')"
      >
        <!-- flowgate.v02gui.0011 T: a stop under way is a muted spinning ring instead of ■ (… with reduced motion), so a
             pressed stop button no longer looks like the one that can still be pressed. -->
        <span v-if="stopMode === 'requested'" class="conv-send-stopping" data-test="conv-send-stopping" aria-hidden="true">
          <span class="conv-send-ring"></span>
          <span class="conv-send-dots">…</span>
        </span>
        <span v-else aria-hidden="true">{{ isStop ? '■' : sending ? '…' : '➤' }}</span>
      </button>
    </div>

    <!-- flowgate.v02gui.0007 A: a failed send says why on its own bubble (with 다시 시도) and in the timeline's error
         line; the composer no longer repeats it. -->
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
      <span class="conv-assist-spacer"></span>
      <ComposerSettings
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
import { computed, nextTick, onBeforeUnmount, ref, shallowRef, watch, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import ComposerImageStrip from './ComposerImageStrip.vue'
import ComposerModelPicker from './ComposerModelPicker.vue'
import ComposerSettings from './ComposerSettings.vue'
import ComposerSourceModes from './ComposerSourceModes.vue'
import ComposerToolsMenu from './ComposerToolsMenu.vue'
import { createComposerImages, isImageFile } from './composerImages'
import { draftKey, draftUserId, loadDraft, saveDraft, type DraftOwner, type DraftSendSnapshot } from './drafts'
import { createSubmissionTrace, type SubmissionTrace, type SubmissionPhase } from './submissionTrace'
import type { ChatProvider, ChatSettings, ChatSettingsPatch, SourceAccessMode } from './types'

const props = defineProps<{
  /** The open chat; null while a new chat is a draft (its first send creates it). */
  chatId: string | null
  projectId?: string | null
  groupId?: string | null
  draftIntentId?: string | null
  /** Nothing can be sent now (no Flow, the chat is loading). */
  disabled: boolean
  readOnly?: boolean
  settings: DeepReadonly<ChatSettings>
  savingSettings: boolean
  pendingSourceMode: SourceAccessMode | null
  /** flowgate.v02.0061 T#14: the Flow's Graph run locks the source access mode. */
  sourceLocked?: boolean
  providers: readonly ChatProvider[]
  selectedProviderId: string
  providersStatus: string
  hasProviders: boolean
  sending: boolean
  submission?: SubmissionPhase | null
  running: boolean
  runStarting: boolean
  cancelling: boolean
  /**
   * flowgate.v02gui.0011 B: the stop button's state from the press of send on ('available' = can be pressed,
   * 'requested' = a stop is under way). Absent: the stop button follows `running` / `runStarting` / `cancelling`.
   */
  stopState?: 'none' | 'available' | 'requested'
  copyingMention: boolean
  manualCopyText: string | null
  /** Sends one message; resolves true when the server accepted it (the draft clears then). */
  onSend: (text: string, snapshot?: DraftSendSnapshot, trace?: SubmissionTrace) => Promise<boolean>
}>()

const emit = defineEmits<{
  'select-provider': [providerId: string]
  'save-settings': [patch: ChatSettingsPatch, done: (ok: boolean) => void]
  'set-source-mode': [mode: SourceAccessMode]
  'copy-mention': []
  'invoke-ai': []
  stop: []
  'manual-copy-again': []
  'manual-copy-dismiss': []
}>()

const { t } = useI18n()

const inputEl = ref<HTMLTextAreaElement | null>(null)
function currentOwner(): DraftOwner | null {
  if (!props.chatId && (!props.projectId || !props.groupId || !props.draftIntentId)) return null
  return { userId: draftUserId(), projectId: props.projectId ?? '', groupId: props.groupId ?? '',
    intentId: props.draftIntentId ?? '', chatId: props.chatId }
}

let visibleOwner = currentOwner()
// flowgate.v02test.0029: the attached images follow the same owner as the text (chat or new-chat draft).
const ownerRef = shallowRef<DraftOwner | null>(visibleOwner)
const attach = createComposerImages(() => ownerRef.value)
attach.restore(visibleOwner)
const draft = ref(loadDraft(visibleOwner))
const revisions = new Map<string, number>()
const pending = new Set<{ snapshot: DraftSendSnapshot; owners: DraftOwner[] }>()
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

// null = no stop button (the send button shows).
const stopMode = computed<'available' | 'requested' | null>(() => {
  if (props.stopState !== undefined) return props.stopState === 'none' ? null : props.stopState
  if (!props.running) return null
  return props.cancelling || props.runStarting ? 'requested' : 'available'
})
const isStop = computed(() => stopMode.value !== null)

const sendTitle = computed(() => {
  if (props.cancelling || (props.stopState !== undefined && stopMode.value === 'requested')) return t('gui.conversation.composer.stopping')
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

// ── flowgate.v02test.0029: message images — paste, drop, state of the attached ones ──────────────────────
const canAttach = computed(() => !props.disabled && !props.readOnly && !props.sending)
const dragging = ref(false)

/** The AI picked is known not to accept images (`accepts_images=false`): the send is not blocked, the user is told. */
const unsupportedAi = computed(() =>
  attach.images.value.length > 0 && props.providers.find((p) => p.id === props.selectedProviderId)?.acceptsImages === false)

/** Why the send is held now, for the note under the thumbnails. */
const imageHint = computed<'uploading' | 'checking' | 'unusable' | 'text' | null>(() => {
  if (attach.images.value.length === 0) return null
  if (attach.uploading.value) return 'uploading'
  if (attach.checking.value) return 'checking'
  if (attach.blocked.value) return 'unusable'
  return draft.value.trim() === '' ? 'text' : null
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

function onDragOver(event: DragEvent): void {
  if (!canAttach.value || !carriesFiles(event)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  dragging.value = true
}

function onDragLeave(event: DragEvent): void {
  const next = event.relatedTarget as Node | null
  if (!next || !(event.currentTarget as HTMLElement | null)?.contains(next)) dragging.value = false
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  if (!canAttach.value || !carriesFiles(event)) return
  event.preventDefault()
  attach.add(Array.from(event.dataTransfer?.files ?? []))
}

onBeforeUnmount(() => attach.dispose())

async function submit(): Promise<void> {
  const trace = createSubmissionTrace()
  trace.mark('submit')
  const text = draft.value.trim()
  const owner = visibleOwner
  if (!owner || !text || props.disabled || props.readOnly || props.sending || props.running) return
  // An image still uploading, failed or gone holds the send: it would otherwise be left out without a word.
  if (attach.blocked.value) return
  if ([...pending].some((p) => draftKey(p.snapshot.owner) === draftKey(owner))) return
  const imageIds = [...attach.readyIds.value]
  const snapshot: DraftSendSnapshot = { owner: { ...owner }, body: draft.value, revision: revisions.get(draftKey(owner)) ?? 0,
    ...(imageIds.length ? { images: imageIds } : {}) }
  const send = { snapshot, owners: [snapshot.owner] }
  // Persist before the first await, even if this owner is hidden by the time it fails.
  saveDraft(owner, snapshot.body)
  pending.add(send)
  let ok = false
  try {
    ok = await props.onSend(text, snapshot, trace)
  } finally {
    pending.delete(send)
  }
  if (!ok) return
  // The server linked the images to the message: they leave the composer (they are not deleted).
  if (imageIds.length) attach.sent(send.owners, imageIds)
  for (const target of send.owners) {
    const key = draftKey(target)
    if ((revisions.get(key) ?? 0) !== snapshot.revision || loadDraft(target) !== snapshot.body) continue
    saveDraft(target, '')
    if (!visibleOwner || draftKey(visibleOwner) !== key || draft.value !== snapshot.body) continue
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
  /* flowgate.v02gui.0003: the AI picker's popover caps its width to this box (100cqw). */
  container-type: inline-size;
  padding: 7px 8px;
  border-top: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
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
  grid-template-columns: minmax(0, 1fr) 39px;
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

/* flowgate.v02gui.0011 T: a stop under way — muted, full opacity (a faded ring would be harder to see), spinning ring. */
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
</style>
