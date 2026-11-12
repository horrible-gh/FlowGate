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
        <select v-model="draftRange" class="csx-select" data-test="conv-setting-context" @change="onRangeChange">
          <option v-for="n in settings.contextTurnsPresets" :key="n" :value="String(n)">{{ t('gui.conversation.settings.context_turns', { n }) }}</option>
          <option value="custom">{{ t('gui.conversation.settings.context_custom') }}</option>
          <option value="all">{{ t('gui.conversation.settings.context_all') }}</option>
          <!-- flowgate.v02test.0025: each chat's checkpoint (message ⋯ menu) decides; the last 5 turns without one. -->
          <option value="checkpoint">{{ t('gui.conversation.settings.context_checkpoint') }}</option>
        </select>
      </label>
      <div v-if="draftRange === 'custom'" class="csx-row csx-turns">
        <span></span>
        <div>
          <input
            v-model="draftTurnsText"
            type="text"
            inputmode="numeric"
            autocomplete="off"
            class="csx-select csx-num"
            :class="{ 'is-invalid': !turnsValid }"
            data-test="conv-setting-turns"
            :aria-label="t('gui.conversation.settings.context')"
            :aria-invalid="!turnsValid"
            @keydown.enter.prevent="saveSettings"
          />
          <div v-if="!turnsValid" class="csx-err" role="alert" data-test="conv-setting-turns-error">
            {{ t('gui.conversation.settings.context_invalid', { min: settings.contextTurnsMin, max: settings.contextTurnsMax }) }}
          </div>
        </div>
      </div>
      <div class="csx-foot">
        <button type="button" class="csx-link" @click="close">{{ t('gui.conversation.cancel') }}</button>
        <button type="button" class="csx-save" data-test="conv-settings-save" :disabled="saving || !turnsValid" @click="saveSettings">
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
// A preset's number as text, 'custom' (사용자 입력), 'all' or 'checkpoint' (체크포인트 사용).
const draftRange = ref('20')
// The typed "last N turns", used only while 'custom' is selected. It is raw text on
// purpose: 0, -3, 1.5, "abc", "" and anything over the server cap are rejected as
// typed -- never rounded or clamped into a value the person did not write.
const draftTurnsText = ref('20')
// The select's value before the person's latest pick.
let shownRange = draftRange.value

const turnsValid = computed(() => {
  // A preset and 'all' carry no typed text, so only 'custom' can be invalid.
  if (draftRange.value !== 'custom') return true
  const text = draftTurnsText.value.trim()
  if (!/^\d+$/.test(text)) return false
  const n = Number(text)
  return Number.isSafeInteger(n) && n >= props.settings.contextTurnsMin && n <= props.settings.contextTurnsMax
})

function toggleSettings(): void {
  if (open.value) {
    close()
    return
  }
  draftSendAction.value = props.settings.sendAction
  // A saved number that is not a preset reopens as 사용자 입력 with that number filled in.
  draftRange.value =
    props.settings.contextMode === 'all' || props.settings.contextMode === 'checkpoint'
      ? props.settings.contextMode
      : props.settings.contextTurnsPresets.includes(props.settings.contextTurns)
        ? String(props.settings.contextTurns)
        : 'custom'
  draftTurnsText.value = String(props.settings.contextTurns)
  shownRange = draftRange.value
  open.value = true
}

// Only a person's own pick runs this (not the reopen redraw): choosing 사용자 입력 right
// after a preset starts the input from that preset's number.
function onRangeChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'custom' && /^\d+$/.test(shownRange)) draftTurnsText.value = shownRange
  shownRange = value
}

function saveSettings(): void {
  const patch: ChatSettingsPatch = { send_action: draftSendAction.value }
  if (draftRange.value === 'all' || draftRange.value === 'checkpoint') {
    // L0010 §2-7-4: never send context_turns with 'all' (nor 'checkpoint') — the number must survive.
    patch.context_mode = draftRange.value === 'all' ? 'all' : 'checkpoint'
  } else {
    if (!turnsValid.value) return
    patch.context_mode = 'recent'
    patch.context_turns = Number(draftRange.value === 'custom' ? draftTurnsText.value.trim() : draftRange.value)
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

.csx-num {
  width: 100%;
  box-sizing: border-box;
}

.csx-num.is-invalid {
  border-color: var(--fg2-status-danger-fg, #d33);
}

.csx-err {
  margin-top: 3px;
  color: var(--fg2-status-danger-fg, #d33);
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
