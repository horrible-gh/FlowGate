<template>
  <!-- Above the input row: the live partial text while speaking, the "transcribing… N s" line with 취소, and the last
       voice error or notice (✕ dismisses it). -->
  <div v-if="voice.liveText.value && voice.phase.value !== 'idle'" class="cvn cvn-live" aria-live="polite" data-test="conv-voice-live">
    {{ voice.liveText.value }}
  </div>
  <div v-else-if="voice.phase.value === 'recording' && voice.mode.value === 'live'" class="cvn cvn-hint" data-test="conv-voice-live-hint">
    {{ t('gui.voice.mic.live_hint') }}
  </div>
  <div v-if="voice.phase.value === 'processing'" class="cvn cvn-busy" role="status" aria-live="polite" data-test="conv-voice-busy">
    <span class="cvn-spin" aria-hidden="true"></span>
    <span>{{ busyText }}</span>
    <button type="button" class="cvn-link" data-test="conv-voice-cancel" @click="voice.discard()">{{ t('gui.voice.mic.cancel') }}</button>
  </div>
  <div v-if="voice.notice.value" class="cvn" :class="voice.notice.value.kind === 'error' ? 'cvn-error' : 'cvn-info'" :role="voice.notice.value.kind === 'error' ? 'alert' : 'status'" data-test="conv-voice-notice">
    <span>{{ noticeText }}</span>
    <button type="button" class="cvn-link" :aria-label="t('gui.conversation.close')" @click="voice.dismissNotice()">✕</button>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { voiceErrorKey } from './voice/messages'
import type { VoiceInput } from './voice/useVoiceInput'
import { shortModelName, useVoice } from './voice/voiceStore'

const props = defineProps<{ voice: VoiceInput }>()

const { t, te } = useI18n()
const store = useVoice()

const busyText = computed(() => {
  const s = props.voice.processingSeconds.value
  if (props.voice.mode.value === 'audio') return t('gui.voice.mic.uploading', { s })
  return t('gui.voice.mic.transcribing', { model: shortModelName(store.model.value, store.selection.value), s })
})

const noticeText = computed(() => {
  const notice = props.voice.notice.value
  if (!notice) return ''
  if (notice.kind === 'info') return t(`gui.voice.mic.${notice.code}`)
  return t(voiceErrorKey(notice.code, (key) => te(key)))
})
</script>

<style scoped>
.cvn {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.cvn-live {
  display: block;
  padding: 6px 8px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  color: var(--fg2-muted-2, var(--fg2-muted));
  font: italic 12px/1.35 var(--fg2-font);
  white-space: pre-wrap;
}

.cvn-error {
  color: var(--fg2-status-danger-fg);
}

.cvn-info {
  color: var(--fg2-status-info-fg);
}

.cvn > span:first-child:not(.cvn-spin) {
  flex: 0 1 auto;
}

.cvn-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  cursor: pointer;
  padding: 0;
}

.cvn-spin {
  width: 11px;
  height: 11px;
  box-sizing: border-box;
  border: 2px solid var(--fg2-line-strong);
  border-top-color: var(--fg2-muted);
  border-radius: 50%;
  animation: cvn-spin 0.8s linear infinite;
}

@keyframes cvn-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .cvn-spin {
    animation: none;
  }
}
</style>
