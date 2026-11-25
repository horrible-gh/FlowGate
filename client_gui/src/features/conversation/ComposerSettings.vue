<template>
  <!-- ⚙ and the settings window (flowgate.v02test.0096 ④): a modeless window floating near the composer, so the chat
       stays readable and usable while it is open. It moves by its title bar, resizes by its corner, never leaves the
       screen and reopens at its last place, size and tab (localStorage). Tabs: 대화 (send action, context, timeout —
       saved by 저장 as before), 음성 (this person's voice input settings, saved on change) and AI (this chat's AI,
       📌 pins, my repeat limit and title AI, saved on change). A press elsewhere does not close it; ⚙, × or Esc inside
       it does. Opening never re-queries the chat settings: it redraws the last confirmed ones. -->
  <div ref="rootEl" class="csx">
    <button
      ref="btnEl"
      type="button"
      class="csx-btn"
      :class="{ 'is-on': open }"
      data-test="conv-settings-btn"
      :title="t('gui.conversation.settings.window_title')"
      :aria-label="t('gui.conversation.settings.window_title')"
      :aria-expanded="open"
      :disabled="disabled"
      @click="toggleSettings"
    >
      <span aria-hidden="true">⚙</span>
    </button>
    <div
      v-if="open"
      ref="winEl"
      class="csx-win"
      :class="{ 'is-vertical': tabOrientation === 'vertical' }"
      role="dialog"
      aria-modal="false"
      :aria-label="t('gui.conversation.settings.window_title')"
      data-test="conv-settings"
      :style="winStyle"
      @keydown.esc.stop="close"
    >
      <div class="csx-head" data-test="conv-settings-titlebar" @pointerdown="win.startMove">
        <span>⚙ {{ t('gui.conversation.settings.window_title') }}</span>
        <button type="button" class="csx-x" data-test="conv-settings-close" :aria-label="t('gui.conversation.close')" :title="t('gui.conversation.close')" @click="close">×</button>
      </div>
      <div class="csx-main">
        <Tabs
          class="csx-tabs"
          :tabs="tabItems"
          :model-value="tab"
          :orientation="tabOrientation"
          :aria-label="t('gui.conversation.settings.window_title')"
          data-test="conv-settings-tabs"
          @update:model-value="selectTab"
        />
        <div class="csx-body" role="tabpanel" :data-tab="tab">
          <template v-if="tab === 'chat'">
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
                <!-- each chat's checkpoint (message ⋯ menu) decides; the last 5 turns without one. -->
                <option value="checkpoint">{{ t('gui.conversation.settings.context_checkpoint') }}</option>
                <option value="checkpoint_recent">{{ t('gui.conversation.settings.context_checkpoint_recent') }}</option>
              </select>
            </label>
            <div v-if="(draftRange === 'custom' || draftRange === 'checkpoint_recent')" class="csx-row csx-turns">
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
            <label class="csx-row">
              <span>{{ t('gui.conversation.settings.timeout') }}</span>
              <select v-model="draftTimeout" :disabled="timeoutEnabled === false" class="csx-select" data-test="conv-setting-timeout">
                <option value="none">{{ t('gui.conversation.settings.timeout_none') }}</option>
                <option v-for="n in [30, 60, 120, 240]" :key="n" :value="String(n)">{{ t('gui.conversation.settings.timeout_minutes', { n }) }}</option>
                <option value="custom">{{ t('gui.conversation.settings.context_custom') }}</option>
              </select>
            </label>
            <input v-if="draftTimeout === 'custom'" v-model="draftTimeoutText" class="csx-select" inputmode="numeric" data-test="conv-setting-timeout-minutes" :aria-label="t('gui.conversation.settings.timeout')" />
            <div v-if="!timeoutValid" class="csx-err" role="alert">{{ t('gui.conversation.settings.timeout_invalid') }}</div>
            <div class="csx-foot">
              <button type="button" class="csx-link" @click="close">{{ t('gui.conversation.cancel') }}</button>
              <button type="button" class="csx-save" data-test="conv-settings-save" :disabled="saving || !turnsValid || !timeoutValid" @click="saveSettings">
                {{ t('gui.conversation.save') }}
              </button>
            </div>
          </template>
          <ComposerVoiceSettings v-else-if="tab === 'voice'" />
          <ComposerAiSettings
            v-else
            :providers="providers"
            :selected-id="selectedProviderId"
            :disabled="disabled"
            @select-provider="(id) => emit('select-provider', id)"
          />
        </div>
      </div>
      <div class="csx-grip" data-test="conv-settings-resize" aria-hidden="true" @pointerdown.stop="win.startResize"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem, type TabOrientation } from '../../components/ui/Tabs.vue'
import ComposerAiSettings from './ComposerAiSettings.vue'
import ComposerVoiceSettings from './ComposerVoiceSettings.vue'
import { useFloatingWindow } from './floatingWindow'
import type { ChatProvider, ChatSettings, ChatSettingsPatch, SendAction } from './types'

type SettingsTab = 'chat' | 'voice' | 'ai'
const TAB_KEYS: readonly SettingsTab[] = ['chat', 'voice', 'ai']

const props = withDefaults(defineProps<{
  settings: DeepReadonly<ChatSettings>
  saving: boolean
  hasProviders: boolean
  disabled?: boolean
  timeoutEnabled?: boolean
  /** The AI tab's "이 대화의 AI": the chat's AI list and the AI picked now. */
  providers?: readonly ChatProvider[]
  selectedProviderId?: string
  /** Tab placement: a row on top (default) or a column on the left — one prop to switch. */
  tabOrientation?: TabOrientation
}>(), { disabled: false, timeoutEnabled: undefined, providers: () => [], selectedProviderId: '', tabOrientation: 'horizontal' })

const emit = defineEmits<{
  save: [patch: ChatSettingsPatch, done: (ok: boolean) => void]
  'select-provider': [providerId: string]
}>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const btnEl = ref<HTMLElement | null>(null)
const winEl = ref<HTMLElement | null>(null)
const open = ref(false)
const win = useFloatingWindow({ width: 360, height: 400, minWidth: 280, minHeight: 220 })
const winStyle = computed(() => ({
  left: `${win.rect.x}px`,
  top: `${win.rect.y}px`,
  width: `${win.rect.width}px`,
  height: `${win.rect.height}px`,
}))

const tabItems = computed<TabItem[]>(() => [
  { key: 'chat', label: t('gui.conversation.settings.tab_chat') },
  { key: 'voice', label: t('gui.conversation.settings.tab_voice') },
  { key: 'ai', label: t('gui.conversation.settings.tab_ai') },
])
const isTab = (value: unknown): value is SettingsTab => TAB_KEYS.includes(value as SettingsTab)
const storedTab = win.storedTab()
const tab = ref<SettingsTab>(isTab(storedTab) ? storedTab : 'chat')

function selectTab(value: string): void {
  if (!isTab(value)) return
  tab.value = value
  win.rememberTab(value)
}

function close(): void {
  if (!open.value) return
  open.value = false
  win.onClose()
}
const draftTimeout = ref('none')
const draftTimeoutText = ref('120')
const timeoutValid = computed(() => draftTimeout.value !== 'custom' || (/^\d+$/.test(draftTimeoutText.value) && Number(draftTimeoutText.value) >= 1 && Number(draftTimeoutText.value) <= 10080))
const draftSendAction = ref<SendAction>('none')
// A preset's number as text, 'custom' ("사용자 입력"), 'all' or 'checkpoint' ("체크포인트 사용").
const draftRange = ref('20')
// The typed "last N turns", used only while 'custom' is selected. It is raw text on
// purpose: 0, -3, 1.5, "abc", "" and anything over the server cap are rejected as
// typed -- never rounded or clamped into a value the person did not write.
const draftTurnsText = ref('20')
// The select's value before the person's latest pick.
let shownRange = draftRange.value

const turnsValid = computed(() => {
  // A preset and 'all' carry no typed text, so only 'custom' can be invalid.
  if (draftRange.value !== 'custom' && draftRange.value !== 'checkpoint_recent') return true
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
  const minutes = props.settings.timeoutMinutes ?? null
  draftTimeout.value = minutes == null ? 'none' : [30, 60, 120, 240].includes(minutes) ? String(minutes) : 'custom'
  draftTimeoutText.value = String(minutes ?? 120)
  draftSendAction.value = props.settings.sendAction
  // A saved number that is not a preset reopens as "사용자 입력" with that number filled in.
  draftRange.value =
    props.settings.contextMode === 'all' || props.settings.contextMode === 'checkpoint' || props.settings.contextMode === 'checkpoint_recent'
      ? props.settings.contextMode
      : props.settings.contextTurnsPresets.includes(props.settings.contextTurns)
        ? String(props.settings.contextTurns)
        : 'custom'
  draftTurnsText.value = String(draftRange.value === 'checkpoint_recent' ? (props.settings.checkpointMaxTurns ?? 5) : props.settings.contextTurns)
  shownRange = draftRange.value
  // The last place, size and tab come back; the first time it opens right above the ⚙ button.
  const anchor = btnEl.value?.getBoundingClientRect?.() ?? null
  win.openAt(anchor)
  const remembered = win.storedTab()
  if (isTab(remembered)) tab.value = remembered
  open.value = true
}

// Only a person's own pick runs this (not the reopen redraw): choosing "사용자 입력" right
// after a preset starts the input from that preset's number.
function onRangeChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'custom' && /^\d+$/.test(shownRange)) draftTurnsText.value = shownRange
  if (value === 'checkpoint_recent') draftTurnsText.value = String(props.settings.checkpointMaxTurns ?? 5)
  shownRange = value
}

function saveSettings(): void {
  const patch: ChatSettingsPatch = { send_action: draftSendAction.value }
  if (!turnsValid.value || !timeoutValid.value) return
  if (draftRange.value === 'checkpoint_recent') {
    patch.context_mode = 'checkpoint_recent'
    patch.checkpoint_max_turns = Number(draftTurnsText.value.trim())
  } else if (draftRange.value === 'all' || draftRange.value === 'checkpoint') {
    // L0010 §2-7-4: never send context_turns with 'all' (nor 'checkpoint') — the number must survive.
    patch.context_mode = draftRange.value === 'all' ? 'all' : 'checkpoint'
  } else {
    if (!turnsValid.value || !timeoutValid.value) return
    patch.context_mode = 'recent'
    patch.context_turns = Number(draftRange.value === 'custom' ? draftTurnsText.value.trim() : draftRange.value)
  }
  const timeout = draftTimeout.value === 'none' ? null : Number(draftTimeout.value === 'custom' ? draftTimeoutText.value : draftTimeout.value)
  if (timeout !== (props.settings.timeoutMinutes ?? null)) patch.timeout_minutes = timeout
  emit('save', patch, (ok) => {
    if (ok) close()
  })
}
</script>

<style scoped>
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

/* The modeless window: fixed to the viewport (left / top / size from floatingWindow), above the chat but without a
   backdrop, so everything around it stays usable. */
.csx-win {
  position: fixed;
  z-index: 9000;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-overlay-radius, var(--fg2-control-radius));
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
  font-size: 11px;
  color: var(--fg2-muted);
  overflow: hidden;
}

.csx-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 6px 6px 12px;
  border-bottom: 1px solid var(--fg2-line);
  background: color-mix(in srgb, var(--fg2-text) 4%, var(--fg2-panel));
  color: var(--fg2-text);
  font-weight: 700;
  cursor: move;
  user-select: none;
  touch-action: none;
  flex: none;
}

.csx-head > span {
  flex: 1;
  min-width: 0;
}

.csx-x {
  width: 24px;
  height: 22px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 14px;
  cursor: pointer;
}

.csx-x:hover {
  background: #d64a5c;
  color: #fff;
}

.csx-main {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.csx-win.is-vertical .csx-main {
  flex-direction: row;
}

.csx-tabs {
  flex: none;
  padding: 0 8px;
}

.csx-tabs :deep(.ui-tab) {
  padding: 7px 10px;
  font-size: 12px;
}

.csx-win.is-vertical .csx-tabs {
  padding: 6px 0;
}

.csx-body {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 8px 10px 12px;
}

/* Corner grip: drag to resize. */
.csx-grip {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 14px;
  height: 14px;
  cursor: nwse-resize;
  touch-action: none;
  background: linear-gradient(135deg, transparent 50%, var(--fg2-line-strong) 50%, var(--fg2-line-strong) 60%, transparent 60%, transparent 72%, var(--fg2-line-strong) 72%, var(--fg2-line-strong) 82%, transparent 82%);
}

.csx-row {
  display: grid;
  /* The label column fits its words; the control takes the rest, so long values are not cut. */
  grid-template-columns: minmax(64px, max-content) minmax(0, 1fr);
  align-items: center;
  gap: 7px;
  margin-top: 6px;
}

.csx-select {
  height: 27px;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
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
