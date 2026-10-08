<template>
  <!-- flowgate.v02.0047 — ⚙ and the chat settings popover above the composer (D0008 §6-2):
       the send action and the context range, saved through the session. Opening never
       re-queries: it redraws the last confirmed settings. -->
  <div ref="rootEl" class="csx">
    <button
      type="button"
      class="csx-btn"
      :class="{ 'is-on': open }"
      data-test="conv-settings-btn"
      :title="t('gui.conversation.settings.title')"
      :aria-label="t('gui.conversation.settings.title')"
      :aria-expanded="open"
      :disabled="disabled"
      @click="toggleSettings"
    >
      <span aria-hidden="true">⚙</span>
    </button>
    <div v-if="open" class="csx-pop" data-test="conv-settings">
      <div class="csx-head">
        <span>{{ t('gui.conversation.settings.title') }}</span>
        <button type="button" class="csx-link" :aria-label="t('gui.conversation.close')" @click="close">×</button>
      </div>
      <label class="csx-row">
        <span>{{ t('gui.conversation.settings.send_action') }}</span>
        <select v-model="draftSendAction" class="csx-select" data-test="conv-setting-send-action">
          <option value="none">{{ t('gui.conversation.settings.send_none') }}</option>
          <option value="invoke_ai" :disabled="!hasProviders">{{ t('gui.conversation.settings.send_invoke') }}</option>
          <option value="copy_mention">{{ t('gui.conversation.settings.send_copy') }}</option>
        </select>
      </label>
      <label class="csx-row">
        <span>{{ t('gui.conversation.settings.context') }}</span>
        <select v-model="draftRange" class="csx-select" data-test="conv-setting-context">
          <option v-for="n in settings.contextTurnsPresets" :key="n" :value="String(n)">{{ t('gui.conversation.settings.context_turns', { n }) }}</option>
          <option v-if="customTurns" :value="String(customTurns)">{{ t('gui.conversation.settings.context_turns', { n: customTurns }) }}</option>
          <option value="all">{{ t('gui.conversation.settings.context_all') }}</option>
        </select>
      </label>
      <div class="csx-foot">
        <button type="button" class="csx-link" @click="close">{{ t('gui.conversation.cancel') }}</button>
        <button type="button" class="csx-save" data-test="conv-settings-save" :disabled="saving" @click="saveSettings">
          {{ t('gui.conversation.save') }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePopover } from './popover'
import type { ChatSettings, ChatSettingsPatch, SendAction } from './types'

const props = defineProps<{
  settings: DeepReadonly<ChatSettings>
  saving: boolean
  hasProviders: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{
  save: [patch: ChatSettingsPatch, done: (ok: boolean) => void]
}>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const { open, close } = usePopover(rootEl)
const draftSendAction = ref<SendAction>('none')
const draftRange = ref('20')

const customTurns = computed(() =>
  props.settings.contextMode === 'recent' && !props.settings.contextTurnsPresets.includes(props.settings.contextTurns)
    ? props.settings.contextTurns
    : null,
)

function toggleSettings(): void {
  if (open.value) {
    close()
    return
  }
  draftSendAction.value = props.settings.sendAction
  draftRange.value = props.settings.contextMode === 'all' ? 'all' : String(props.settings.contextTurns)
  open.value = true
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
  emit('save', patch, (ok) => {
    if (ok) close()
  })
}
</script>

<style scoped>
/* Not positioned: the popover spans the composer, the nearest positioned box. */
.csx {
  flex: 0 0 auto;
}

.csx-btn {
  width: 26px;
  height: 27px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
  display: grid;
  place-items: center;
  font-size: 13px;
  cursor: pointer;
}

.csx-btn:hover:not(:disabled),
.csx-btn.is-on {
  color: var(--fg2-text);
  border-color: var(--fg2-control-hover-border);
}

.csx-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.csx-pop {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: calc(100% + 4px);
  z-index: 30;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
  padding: 8px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.csx-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--fg2-text);
  font-weight: 700;
}

.csx-head > span {
  flex: 1;
}

.csx-row {
  display: grid;
  grid-template-columns: 76px 1fr;
  align-items: center;
  gap: 7px;
  margin-top: 6px;
}

.csx-select {
  height: 27px;
  min-width: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: inherit;
  padding: 0 5px;
}

.csx-foot {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.csx-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-weight: 400;
  cursor: pointer;
}

.csx-save {
  height: 24px;
  padding: 0 10px;
  border: 1px solid var(--fg2-blue);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
  font: inherit;
  cursor: pointer;
}

.csx-save:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.csx-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
