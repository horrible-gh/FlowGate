<template>
  <!-- flowgate.v02.0031 — bottom of the Conversation (R0001 §8). Every control drives an existing
       contract through the session; nothing here opens a dialog (0235 R0001) — settings, the
       menus and the manual-copy fallback are inline. flowgate.v02.0047 (v9): input and send on
       the first line; AI picker, source mode icons, settings (⚙) and the ⋯ menu (mention copy,
       AI call) on the second. flowgate.v02.0061 T#14: `sourceLocked` turns the source mode icons off with the
       reason (the Flow's Graph run); nothing else changes. -->
  <form class="conv-compose" data-test="conv-composer" @submit.prevent="submit">
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
        @keydown.enter.exact.prevent="submit"
      ></textarea>
      <!-- 0264 R0001: while a chat AI run is in flight the send button is a STOP button. -->
      <button
        :type="running ? 'button' : 'submit'"
        class="conv-send"
        :class="{ 'is-stop': running }"
        data-test="conv-send"
        :title="sendTitle"
        :aria-label="sendTitle"
        :disabled="running ? cancelling || runStarting : disabled || readOnly || sending || draft.trim() === ''"
        @click="running && emit('stop')"
      >
        <span aria-hidden="true">{{ running ? '■' : sending ? '…' : '➤' }}</span>
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
import { computed, nextTick, ref, watch, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import ComposerModelPicker from './ComposerModelPicker.vue'
import ComposerSettings from './ComposerSettings.vue'
import ComposerSourceModes from './ComposerSourceModes.vue'
import ComposerToolsMenu from './ComposerToolsMenu.vue'
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
      transfer.owners.push(owner)
    } else {
      draft.value = loadDraft(owner)
    }
    visibleOwner = owner
    void nextTick(autoGrow)
  },
)

const sendTitle = computed(() => {
  if (props.cancelling) return t('gui.conversation.composer.stopping')
  if (props.running) return t('gui.conversation.composer.stop')
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

async function submit(): Promise<void> {
  const trace = createSubmissionTrace()
  trace.mark('submit')
  const text = draft.value.trim()
  const owner = visibleOwner
  if (!owner || !text || props.disabled || props.readOnly || props.sending || props.running) return
  if ([...pending].some((p) => draftKey(p.snapshot.owner) === draftKey(owner))) return
  const snapshot: DraftSendSnapshot = { owner: { ...owner }, body: draft.value, revision: revisions.get(draftKey(owner)) ?? 0 }
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
