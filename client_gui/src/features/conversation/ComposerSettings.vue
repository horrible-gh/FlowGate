<template>
  <!-- ⚙ and the settings window (flowgate.v02test.0096 ④): a modeless window floating near the composer, so the chat
       stays readable and usable while it is open. It moves by its title bar, resizes by its corner, never leaves the
       screen and reopens at its last place, size and tab (localStorage). Tabs: 대화 (send action, context, 명령 실행 제한),
       음성 (this person's voice input settings) and AI (📌 pins, 채팅 최대 시간 and title AI) — every
       tab saves a change at once (F96-8: no 취소 / 저장 any more; a failed 대화 save puts the confirmed values back and
       the session shows the error toast). The height follows the open tab unless the person resized the window; all
       three tabs draw the same row (label left, value right, shared through the --csx-* variables below). A press
       elsewhere does not close it; ⚙, × or Esc inside it does. Opening never re-queries the chat settings: it redraws
       the last confirmed ones. -->
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
          <div v-if="tab === 'chat'" class="csx-rows" data-test="conv-chat-settings">
            <label class="csx-row">
              <span class="csx-label">{{ t('gui.conversation.settings.send_action') }}</span>
              <select v-model="draftSendAction" class="csx-select" data-test="conv-setting-send-action" @change="commit">
                <option value="none">{{ t('gui.conversation.settings.send_none') }}</option>
                <option value="invoke_ai" :disabled="!hasProviders">{{ t('gui.conversation.settings.send_invoke') }}</option>
                <option value="copy_mention">{{ t('gui.conversation.settings.send_copy') }}</option>
              </select>
            </label>
            <label class="csx-row">
              <span class="csx-label">{{ t('gui.conversation.settings.context') }}</span>
              <select v-model="draftRange" class="csx-select" data-test="conv-setting-context" @change="onRangeChange">
                <option v-for="n in settings.contextTurnsPresets" :key="n" :value="String(n)">{{ t('gui.conversation.settings.context_turns', { n }) }}</option>
                <option value="custom">{{ t('gui.conversation.settings.context_custom') }}</option>
                <option value="all">{{ t('gui.conversation.settings.context_all') }}</option>
                <!-- each chat's checkpoint (message ⋯ menu) decides; the last 5 turns without one. -->
                <option value="checkpoint">{{ t('gui.conversation.settings.context_checkpoint') }}</option>
                <option value="checkpoint_recent">{{ t('gui.conversation.settings.context_checkpoint_recent') }}</option>
              </select>
            </label>
            <div v-if="(draftRange === 'custom' || draftRange === 'checkpoint_recent')" class="csx-row csx-sub">
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
                  @change="commit"
                  @keydown.enter.prevent="commit"
                />
                <div v-if="!turnsValid" class="csx-err" role="alert" data-test="conv-setting-turns-error">
                  {{ t('gui.conversation.settings.context_invalid', { min: settings.contextTurnsMin, max: settings.contextTurnsMax }) }}
                </div>
              </div>
            </div>
            <!-- F108-6: 명령 실행 제한 — how long one command the AI runs may take (1초~30분 or 제한 없음). -->
            <div class="csx-row" data-test="conv-setting-command-limit-row">
              <span class="csx-label">{{ t('gui.conversation.settings.command_limit') }}</span>
              <ComposerDurationInput
                :model-value="commandLimitShown"
                :min="COMMAND_LIMIT_MIN"
                :max="COMMAND_LIMIT_MAX"
                :disabled="timeoutEnabled === false"
                :label="t('gui.conversation.settings.command_limit')"
                data-test="conv-setting-command-limit"
                @update:model-value="onCommandLimit"
              />
            </div>
          </div>
          <ComposerVoiceSettings v-else-if="tab === 'voice'" />
          <ComposerAiSettings v-else :providers="providers">
            <!-- F108-6: 채팅 최대 시간 — between 고정한 AI and 제목 짓기 AI (1분~7일 or 제한 없음). -->
            <div class="csx-row" data-test="conv-setting-chat-limit-row">
              <span class="csx-label">{{ t('gui.conversation.settings.chat_limit') }}</span>
              <ComposerDurationInput
                :model-value="draftChatLimit"
                :min="CHAT_LIMIT_MIN"
                :max="CHAT_LIMIT_MAX"
                :disabled="timeoutEnabled === false"
                :label="t('gui.conversation.settings.chat_limit')"
                data-test="conv-setting-chat-limit"
                @update:model-value="onChatLimit"
              />
            </div>
          </ComposerAiSettings>
        </div>
      </div>
      <div class="csx-grip" data-test="conv-settings-resize" aria-hidden="true" @pointerdown.stop="win.startResize" @dblclick="win.resetSize"></div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch, type DeepReadonly } from 'vue'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem, type TabOrientation } from '../../components/ui/Tabs.vue'
import ComposerAiSettings from './ComposerAiSettings.vue'
import ComposerDurationInput from './ComposerDurationInput.vue'
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
  /** false = a new chat not saved yet: 명령 실행 제한 and 채팅 최대 시간 (per-chat values) are disabled. */
  timeoutEnabled?: boolean
  /** The chat's AI list (the AI tab's 📌 pins and title AI choices). */
  providers?: readonly ChatProvider[]
  /** Tab placement: a row on top (default) or a column on the left — one prop to switch. */
  tabOrientation?: TabOrientation
}>(), { disabled: false, timeoutEnabled: undefined, providers: () => [], tabOrientation: 'horizontal' })

const emit = defineEmits<{
  save: [patch: ChatSettingsPatch, done: (ok: boolean) => void]
}>()

// F108-6 server contract (per-chat settings): command_timeout_seconds null = not set (shown as 300초), 0 = 제한 없음,
// 1~1800; timeout_seconds null = 제한 없음, 60~604800.
const COMMAND_LIMIT_DEFAULT = 300
const COMMAND_LIMIT_MIN = 1
const COMMAND_LIMIT_MAX = 1800
const CHAT_LIMIT_MIN = 60
const CHAT_LIMIT_MAX = 604800

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const btnEl = ref<HTMLElement | null>(null)
const winEl = ref<HTMLElement | null>(null)
const open = ref(false)
// F96-8: `height` is only the estimate for the first placement; the window is as tall as the open tab (capped at the
// screen, where the body scrolls) unless the person resized it with the grip.
const win = useFloatingWindow({ width: 360, height: 300, minWidth: 280, minHeight: 160 })
const winStyle = computed(() => ({
  left: `${win.rect.x}px`,
  top: `${win.rect.y}px`,
  width: `${win.rect.width}px`,
  ...(win.sized.value ? { height: `${win.rect.height}px` } : { maxHeight: `${win.maxHeight.value}px` }),
}))

// The rendered height goes back to the window logic, so a taller tab (or an opened voice list) stays on screen.
let sizeObserver: ResizeObserver | null = null
function measureWindow(): void {
  const el = winEl.value
  if (el) win.measure(el.getBoundingClientRect().height)
}
watch(winEl, (el) => {
  sizeObserver?.disconnect()
  sizeObserver = null
  if (!el) return
  if (typeof ResizeObserver !== 'undefined') {
    sizeObserver = new ResizeObserver(() => measureWindow())
    sizeObserver.observe(el)
  }
  void nextTick(measureWindow)
}, { flush: 'post' })
onBeforeUnmount(() => sizeObserver?.disconnect())

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
  void nextTick(measureWindow)
}

function close(): void {
  if (!open.value) return
  open.value = false
  win.onClose()
}
// 명령 실행 제한 in the server's form (0 = 제한 없음) and 채팅 최대 시간 (null = 제한 없음), both in seconds.
const draftCommandLimit = ref(COMMAND_LIMIT_DEFAULT)
const commandLimitShown = computed(() => (draftCommandLimit.value === 0 ? null : draftCommandLimit.value))
const draftChatLimit = ref<number | null>(null)

function onCommandLimit(seconds: number | null): void {
  draftCommandLimit.value = seconds ?? 0
  commit()
}

function onChatLimit(seconds: number | null): void {
  draftChatLimit.value = seconds
  commit()
}
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
  resetDrafts()
  // The last place, size and tab come back; the first time it opens right above the ⚙ button.
  const anchor = btnEl.value?.getBoundingClientRect?.() ?? null
  win.openAt(anchor)
  const remembered = win.storedTab()
  if (isTab(remembered)) tab.value = remembered
  open.value = true
}

/** The 대화 tab shows the last server-confirmed settings (on open, and after a failed save). */
function resetDrafts(): void {
  draftCommandLimit.value = props.settings.commandTimeoutSeconds ?? COMMAND_LIMIT_DEFAULT
  draftChatLimit.value = props.settings.timeoutSeconds ?? null
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
}

// Only a person's own pick runs this (not the reopen redraw): choosing "사용자 입력" right
// after a preset starts the input from that preset's number. The pick is then saved like any other change.
function onRangeChange(event: Event): void {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'custom' && /^\d+$/.test(shownRange)) draftTurnsText.value = shownRange
  if (value === 'checkpoint_recent') draftTurnsText.value = String(props.settings.checkpointMaxTurns ?? 5)
  shownRange = value
  commit()
}

/**
 * The 대화 tab's values as a patch, or null when they are what the server already holds. Same shape as the former
 * 저장: send_action and the context always, timeout_seconds / command_timeout_seconds only when they changed. Never context_turns with 'all' /
 * 'checkpoint' (L0010 §2-7-4: the kept number must survive).
 * While the typed context number is not valid, only the two times are sent (when they changed): 명령 실행 제한 and
 * 채팅 최대 시간 still save at once, and send action / context wait for a valid number as before.
 */
function buildPatch(): ChatSettingsPatch | null {
  const s = props.settings
  if (!turnsValid.value) {
    const times: ChatSettingsPatch = {}
    if (draftChatLimit.value !== (s.timeoutSeconds ?? null)) times.timeout_seconds = draftChatLimit.value
    if (draftCommandLimit.value !== (s.commandTimeoutSeconds ?? COMMAND_LIMIT_DEFAULT)) times.command_timeout_seconds = draftCommandLimit.value
    return Object.keys(times).length ? times : null
  }
  const patch: ChatSettingsPatch = { send_action: draftSendAction.value }
  let sameContext: boolean
  if (draftRange.value === 'checkpoint_recent') {
    patch.context_mode = 'checkpoint_recent'
    patch.checkpoint_max_turns = Number(draftTurnsText.value.trim())
    sameContext = s.contextMode === 'checkpoint_recent' && (s.checkpointMaxTurns ?? 5) === patch.checkpoint_max_turns
  } else if (draftRange.value === 'all' || draftRange.value === 'checkpoint') {
    patch.context_mode = draftRange.value === 'all' ? 'all' : 'checkpoint'
    sameContext = s.contextMode === patch.context_mode
  } else {
    patch.context_mode = 'recent'
    patch.context_turns = Number(draftRange.value === 'custom' ? draftTurnsText.value.trim() : draftRange.value)
    sameContext = s.contextMode !== 'all' && s.contextMode !== 'checkpoint' && s.contextMode !== 'checkpoint_recent' && s.contextTurns === patch.context_turns
  }
  if (draftChatLimit.value !== (s.timeoutSeconds ?? null)) patch.timeout_seconds = draftChatLimit.value
  if (draftCommandLimit.value !== (s.commandTimeoutSeconds ?? COMMAND_LIMIT_DEFAULT)) patch.command_timeout_seconds = draftCommandLimit.value
  const unchanged = sameContext && patch.send_action === s.sendAction && !('timeout_seconds' in patch) && !('command_timeout_seconds' in patch)
  return unchanged ? null : patch
}

// One save at a time: a change made while one is on its way is sent (with every value as shown then) once it answers.
let inFlight = false
let queued = false

/**
 * A finished pick or typed value of the 대화 or AI tab, saved at once. An invalid typed context number only holds back
 * send action and context (buildPatch); the two times are saved regardless.
 */
function commit(): void {
  if (inFlight) {
    queued = true
    return
  }
  const patch = buildPatch()
  if (!patch) return
  inFlight = true
  emit('save', patch, (ok) => {
    inFlight = false
    if (!ok) {
      // The server kept the previous values: show them again (the session already showed the error toast).
      queued = false
      resetDrafts()
      return
    }
    if (queued) {
      queued = false
      // After the confirmed settings reach this window, so the change is weighed against them.
      void nextTick(commit)
    }
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
  font-size: 11.5px;
  color: var(--fg2-muted);
  overflow: hidden;
  /* F96-8: one row shape for the 대화 / 음성 / AI tabs — the tab components read these. */
  --csx-label-width: 88px;
  --csx-row-gap: 8px;
  --csx-row-height: 28px;
  --csx-control-height: 26px;
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
  flex: 1 1 auto;
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
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  padding: 10px 12px 14px;
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

.csx-rows {
  display: grid;
  gap: var(--csx-row-gap);
}

/* Label left, value right, on one line; the label column is the same width in every tab. */
.csx-row {
  display: grid;
  grid-template-columns: var(--csx-label-width) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-height: var(--csx-row-height);
}

.csx-sub {
  margin-top: calc(var(--csx-row-gap) * -0.5);
}

.csx-label {
  color: var(--fg2-text);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.csx-select {
  height: var(--csx-control-height);
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

.csx-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
