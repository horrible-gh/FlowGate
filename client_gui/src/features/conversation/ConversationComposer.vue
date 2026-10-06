<template>
  <!-- flowgate.v02.0031 — bottom of the Conversation (R0001 §8): chat settings, AI source mode
       (읽기 / 편집 / 1회 편집), mention copy, AI call, AI select, input and send / stop. Every
       control drives an existing contract through the session; nothing here opens a dialog
       (0235 R0001) — settings and the manual-copy fallback are inline panels. -->
  <form class="conv-compose" data-test="conv-composer" @submit.prevent="submit">
    <div v-if="manualCopyText" class="conv-panel" data-test="conv-manual-copy">
      <div class="conv-panel-head">
        <span>{{ t('gui.conversation.composer.manual_copy_title') }}</span>
        <button type="button" class="conv-link" data-test="conv-manual-copy-again" @click="emit('manual-copy-again')">
          {{ t('gui.conversation.composer.manual_copy_again') }}
        </button>
        <button type="button" class="conv-link" @click="emit('manual-copy-dismiss')">{{ t('gui.conversation.close') }}</button>
      </div>
      <textarea class="conv-manual-text" readonly spellcheck="false" :value="manualCopyText" @focus="($event.target as HTMLTextAreaElement).select()"></textarea>
    </div>

    <div v-if="settingsOpen" class="conv-panel" data-test="conv-settings">
      <div class="conv-panel-head">
        <span>{{ t('gui.conversation.settings.title') }}</span>
        <button type="button" class="conv-link" :aria-label="t('gui.conversation.close')" @click="settingsOpen = false">×</button>
      </div>
      <label class="conv-setting-row">
        <span>{{ t('gui.conversation.settings.send_action') }}</span>
        <select v-model="draftSendAction" class="conv-select" data-test="conv-setting-send-action">
          <option value="none">{{ t('gui.conversation.settings.send_none') }}</option>
          <option value="invoke_ai" :disabled="!hasProviders">{{ t('gui.conversation.settings.send_invoke') }}</option>
          <option value="copy_mention">{{ t('gui.conversation.settings.send_copy') }}</option>
        </select>
      </label>
      <label class="conv-setting-row">
        <span>{{ t('gui.conversation.settings.context') }}</span>
        <select v-model="draftRange" class="conv-select" data-test="conv-setting-context">
          <option v-for="n in settings.contextTurnsPresets" :key="n" :value="String(n)">{{ t('gui.conversation.settings.context_turns', { n }) }}</option>
          <option v-if="customTurns" :value="String(customTurns)">{{ t('gui.conversation.settings.context_turns', { n: customTurns }) }}</option>
          <option value="all">{{ t('gui.conversation.settings.context_all') }}</option>
        </select>
      </label>
      <div class="conv-panel-foot">
        <button type="button" class="conv-link" @click="settingsOpen = false">{{ t('gui.conversation.cancel') }}</button>
        <button type="button" class="conv-save" data-test="conv-settings-save" :disabled="savingSettings" @click="saveSettings">
          {{ t('gui.conversation.save') }}
        </button>
      </div>
    </div>

    <div class="conv-assist">
      <button
        type="button"
        class="conv-icon-btn"
        :class="{ 'is-on': settingsOpen }"
        data-test="conv-settings-btn"
        :title="t('gui.conversation.settings.title')"
        :aria-label="t('gui.conversation.settings.title')"
        :aria-expanded="settingsOpen"
        :disabled="readOnly"
        @click="toggleSettings"
      >
        <span aria-hidden="true">⚙</span>
      </button>
      <!-- Server-confirmed only: the highlight moves once the PATCH answers (0568 T0004 §2). -->
      <div
        v-if="settings.sourceAccessModes.length > 0"
        class="conv-source"
        role="group"
        data-test="conv-source-mode"
        :aria-label="t('gui.conversation.composer.source_mode')"
      >
        <button
          v-for="mode in settings.sourceAccessModes"
          :key="mode"
          type="button"
          :class="{ 'is-on': settings.sourceAccessMode === mode }"
          :aria-pressed="settings.sourceAccessMode === mode"
          :title="t(`gui.conversation.composer.source_tip.${mode}`)"
          :data-mode="mode"
          :disabled="readOnly || pendingSourceMode !== null"
          @click="emit('set-source-mode', mode)"
        >
          {{ pendingSourceMode === mode ? '…' : t(`gui.conversation.composer.source.${mode}`) }}
        </button>
      </div>
      <span class="conv-assist-spacer"></span>
      <button
        type="button"
        class="conv-icon-btn"
        data-test="conv-copy-mention"
        :title="t('gui.conversation.composer.copy_mention')"
        :aria-label="t('gui.conversation.composer.copy_mention')"
        :disabled="disabled || copyingMention"
        @click="emit('copy-mention')"
      >
        <span aria-hidden="true">⧉</span>
      </button>
      <button
        v-if="hasProviders"
        type="button"
        class="conv-icon-btn conv-icon-btn--ai"
        data-test="conv-invoke-ai"
        :title="running ? t('gui.conversation.composer.ai_running') : t('gui.conversation.composer.invoke_ai')"
        :aria-label="t('gui.conversation.composer.invoke_ai')"
        :disabled="disabled || readOnly || running"
        @click="emit('invoke-ai')"
      >
        <span aria-hidden="true">{{ running ? '…' : '◆' }}</span>
      </button>
    </div>

    <div class="conv-pill">
      <select
        class="conv-provider"
        data-test="conv-provider-select"
        :title="t('gui.conversation.composer.provider')"
        :aria-label="t('gui.conversation.composer.provider')"
        :value="selectedProviderId"
        :disabled="readOnly || providers.length === 0"
        @change="emit('select-provider', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="providers.length === 0" value="">{{ providerPlaceholder }}</option>
        <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
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
  </form>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import { loadDraft, saveDraft } from './drafts'
import type { ChatProvider, ChatSettings, ChatSettingsPatch, SendAction, SourceAccessMode } from './types'

const props = defineProps<{
  chatId: string | null
  /** No chat to talk in (no Flow / no conversation yet). */
  disabled: boolean
  readOnly?: boolean
  settings: DeepReadonly<ChatSettings>
  savingSettings: boolean
  pendingSourceMode: SourceAccessMode | null
  providers: readonly ChatProvider[]
  selectedProviderId: string
  providersStatus: string
  hasProviders: boolean
  sending: boolean
  running: boolean
  runStarting: boolean
  cancelling: boolean
  copyingMention: boolean
  manualCopyText: string | null
  /** Sends one message; resolves true when the server accepted it (the draft clears then). */
  onSend: (text: string) => Promise<boolean>
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
const draft = ref(loadDraft(props.chatId))

watch(
  () => props.chatId,
  (chatId) => {
    draft.value = loadDraft(chatId)
    void nextTick(autoGrow)
  },
)

const providerPlaceholder = computed(() =>
  props.providersStatus === 'loading'
    ? t('gui.conversation.composer.provider_loading')
    : props.providersStatus === 'error'
      ? t('gui.conversation.composer.provider_failed')
      : t('gui.conversation.composer.provider_none'),
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
  el.style.height = `${Math.min(el.scrollHeight, 96)}px`
}

function onInput(): void {
  saveDraft(props.chatId, draft.value)
  autoGrow()
}

async function submit(): Promise<void> {
  const text = draft.value.trim()
  if (!text || props.disabled || props.readOnly || props.sending || props.running) return
  const chatId = props.chatId
  const ok = await props.onSend(text)
  // The draft survives a failed send (P0003 scenario 17); a sent one is cleared.
  if (!ok || chatId !== props.chatId) return
  draft.value = ''
  saveDraft(chatId, '')
  await nextTick()
  autoGrow()
  inputEl.value?.focus()
}

// ── Chat settings panel (D0008 §6-2) ─────────────────────────────────────────────────
const settingsOpen = ref(false)
const draftSendAction = ref<SendAction>('none')
const draftRange = ref('20')

const customTurns = computed(() =>
  props.settings.contextMode === 'recent' && !props.settings.contextTurnsPresets.includes(props.settings.contextTurns)
    ? props.settings.contextTurns
    : null,
)

function toggleSettings(): void {
  if (settingsOpen.value) {
    settingsOpen.value = false
    return
  }
  // Opening never re-queries: it redraws the last confirmed settings.
  draftSendAction.value = props.settings.sendAction
  draftRange.value = props.settings.contextMode === 'all' ? 'all' : String(props.settings.contextTurns)
  settingsOpen.value = true
}

function saveSettings(): void {
  const patch: ChatSettingsPatch = { send_action: draftSendAction.value }
  if (draftRange.value === 'all') {
    // L0010 §2-7-4: never send context_turns with 'all' — the number must survive.
    patch.context_mode = 'all'
  } else {
    patch.context_mode = 'recent'
    patch.context_turns = Number(draftRange.value)
  }
  emit('save-settings', patch, (ok) => {
    if (ok) settingsOpen.value = false
  })
}

defineExpose({ focus: () => inputEl.value?.focus() })
</script>

<style scoped>
.conv-compose {
  position: relative;
  flex: 0 0 auto;
  padding: 7px 8px 8px;
  border-top: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
}

.conv-panel {
  margin-bottom: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  padding: 7px 8px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.conv-panel-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--fg2-text);
  font-weight: 700;
}

.conv-panel-head > span {
  flex: 1;
}

.conv-panel-foot {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.conv-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-weight: 400;
  cursor: pointer;
}

.conv-save {
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--fg2-blue);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
  font: inherit;
  cursor: pointer;
}

.conv-save:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
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

.conv-setting-row {
  display: grid;
  grid-template-columns: 76px 1fr;
  align-items: center;
  gap: 7px;
  margin-top: 6px;
}

.conv-select {
  height: 27px;
  min-width: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: inherit;
  padding: 0 5px;
}

.conv-assist {
  height: 28px;
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 5px;
}

.conv-assist-spacer {
  flex: 1;
}

.conv-icon-btn {
  width: 26px;
  height: 25px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
  display: grid;
  place-items: center;
  font-size: 12px;
  cursor: pointer;
}

.conv-icon-btn:hover:not(:disabled),
.conv-icon-btn.is-on {
  color: var(--fg2-text);
  border-color: var(--fg2-control-hover-border);
}

.conv-icon-btn--ai {
  color: var(--fg2-violet);
}

.conv-icon-btn:disabled,
.conv-source button:disabled,
.conv-provider:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.conv-source {
  display: flex;
  height: 25px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  overflow: hidden;
}

.conv-source button {
  height: 23px;
  padding: 0 7px;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-muted);
  font: inherit;
  font-size: 10px;
  cursor: pointer;
}

.conv-source button:last-child {
  border-right: 0;
}

.conv-source button.is-on {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.conv-pill {
  min-height: 39px;
  display: grid;
  grid-template-columns: minmax(64px, 88px) minmax(0, 1fr) 31px;
  align-items: end;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  overflow: hidden;
}

.conv-provider {
  height: 39px;
  min-width: 0;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-muted);
  font: inherit;
  font-size: 10.5px;
  padding: 0 4px;
  outline: 0;
}

.conv-input {
  min-width: 0;
  max-height: 96px;
  height: 39px;
  resize: none;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--fg2-text);
  padding: 10px 7px 7px;
  font: 12px/1.35 var(--fg2-font);
}

.conv-input::placeholder {
  color: var(--fg2-muted-2);
}

.conv-send {
  width: 31px;
  height: 31px;
  margin: 4px 4px 4px 0;
  padding: 0;
  border: 1px solid var(--fg2-blue);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  display: grid;
  place-items: center;
  font-size: 13px;
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

.conv-icon-btn:focus-visible,
.conv-source button:focus-visible,
.conv-send:focus-visible,
.conv-provider:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
