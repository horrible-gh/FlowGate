<template>
  <!-- Voice input inside the input row: the recording pill (dot, time, model, ✕) and the mic / ■ button. The root is
       always rendered so the input row's grid keeps its columns; it is empty while voice input is not offered. -->
  <span class="cvb" data-test="conv-voice">
    <span v-if="voice.phase.value === 'recording' || voice.phase.value === 'starting'" class="cvb-pill" data-test="conv-voice-pill">
      <span class="cvb-dot" aria-hidden="true"></span>
      <span class="cvb-time" data-test="conv-voice-time">{{ formatClock(voice.seconds.value) }}</span>
      <span class="cvb-model" :class="{ 'is-live': voice.mode.value === 'live' }">{{ pillLabel }}</span>
      <button type="button" class="cvb-x" data-test="conv-voice-discard" :title="t('gui.voice.mic.discard')" :aria-label="t('gui.voice.mic.discard')" @click="voice.discard()">✕</button>
    </span>
    <button
      v-if="offered"
      type="button"
      class="cvb-mic"
      :class="{ 'is-rec': voice.phase.value === 'recording', 'is-off': !usable }"
      data-test="conv-voice-mic"
      :title="micTitle"
      :aria-label="micTitle"
      :aria-pressed="voice.phase.value === 'recording'"
      :disabled="disabled || voice.phase.value === 'processing' || voice.phase.value === 'starting' || (!usable && voice.phase.value === 'idle')"
      @click="voice.toggle()"
    >
      <svg v-if="voice.phase.value === 'recording'" viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" /></svg>
      <svg v-else viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="9" y="3" width="6" height="11" rx="3" /><path d="M5.5 11a6.5 6.5 0 0 0 13 0" /><path d="M12 17.5V21" />
      </svg>
    </button>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatClock, type VoiceInput } from './voice/useVoiceInput'
import { realtimeActive, shortModelName, useVoice } from './voice/voiceStore'

const props = defineProps<{
  voice: VoiceInput
  /** Nothing can be typed now (no Flow, read only, sending). */
  disabled: boolean
}>()

const { t } = useI18n()
const store = useVoice()

const status = computed(() => store.state.status)
/** Shown at all: once installed for everyone; an administrator also sees it before installation (to find ⚙). */
const offered = computed(() => {
  const s = status.value
  if (!s) return false
  return s.state !== 'not_installed' || s.canManage
})
const usable = computed(() => status.value?.state === 'on' && !!store.selection.value)
const modelName = computed(() => shortModelName(store.model.value, store.selection.value))
const live = computed(() => realtimeActive(store.state.prefs, store.model.value))

const micTitle = computed(() => {
  if (props.voice.phase.value === 'recording') return t('gui.voice.mic.stop')
  const s = status.value
  if (s?.state === 'not_installed' || (s?.state === 'installing' && !store.selection.value)) return t('gui.voice.mic.not_installed')
  if (s?.state === 'error') return t('gui.voice.mic.error')
  if (!usable.value) return t('gui.voice.mic.off')
  if (store.state.prefs.action === 'audio') return t('gui.voice.mic.tip_audio', { model: modelName.value })
  return live.value ? t('gui.voice.mic.tip_realtime', { model: modelName.value }) : t('gui.voice.mic.tip_text', { model: modelName.value })
})

const pillLabel = computed(() => props.voice.mode.value === 'live' ? `${t('gui.voice.mic.realtime')} ${modelName.value}` : modelName.value)
</script>

<style scoped>
.cvb {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  align-self: end;
  min-width: 0;
}

.cvb-mic {
  width: 32px;
  height: 32px;
  margin: 5px 4px 5px 0;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
  display: grid;
  place-items: center;
  cursor: pointer;
}

.cvb-mic svg {
  width: 17px;
  height: 17px;
}

.cvb-mic:hover:not(:disabled) {
  color: var(--fg2-text);
}

.cvb-mic.is-rec {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.cvb-mic:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.cvb-mic:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.cvb-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  margin: 9px 0;
  padding: 0 4px 0 8px;
  border: 1px solid var(--fg2-status-danger-border);
  border-radius: 12px;
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-text);
  font-size: 11px;
  white-space: nowrap;
}

.cvb-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--fg2-status-danger-fg);
  animation: cvb-blink 1s steps(2, start) infinite;
}

.cvb-time {
  font-variant-numeric: tabular-nums;
}

.cvb-model {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--fg2-muted);
}

.cvb-model.is-live {
  color: var(--fg2-status-info-fg);
}

.cvb-x {
  border: 0;
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  cursor: pointer;
  padding: 0 4px;
}

.cvb-x:hover {
  color: var(--fg2-text);
}

@keyframes cvb-blink {
  to {
    visibility: hidden;
  }
}

@media (prefers-reduced-motion: reduce) {
  .cvb-dot {
    animation: none;
  }
}
</style>
