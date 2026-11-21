<template>
  <!-- A voice message's recording. The bytes come through the signed-in session (the content endpoint takes no token
       in its address), so they are fetched on the first press and played from a local object URL. -->
  <div class="tla" data-test="tl-audio">
    <audio v-if="url" ref="audioEl" class="tla-player" :src="url" controls preload="auto" data-test="tl-audio-player"></audio>
    <button v-else type="button" class="tla-play" :disabled="loading" :aria-label="t('gui.voice.audio.label')" data-test="tl-audio-play" @click="load">
      <span aria-hidden="true">▶</span>
      <span>{{ loading ? t('gui.voice.audio.loading') : t('gui.voice.audio.play') }}</span>
    </button>
    <span v-if="failed" class="tla-error" role="alert">{{ t('gui.voice.audio.failed') }}</span>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchMessageAudioBlob, type TurnAudio } from './voice/api'
import { installVoiceMessages } from './voice/messages'

const props = defineProps<{ audio: TurnAudio }>()

const composer = useI18n({ useScope: 'global' })
installVoiceMessages(composer)
const { t } = composer

const url = ref<string | null>(null)
const loading = ref(false)
const failed = ref(false)
const audioEl = ref<HTMLAudioElement | null>(null)
let alive = true

function revoke(): void {
  if (url.value && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url.value)
  url.value = null
}

async function load(): Promise<void> {
  if (loading.value) return
  loading.value = true
  failed.value = false
  const audioId = props.audio.audioId
  try {
    const blob = await fetchMessageAudioBlob(props.audio.url)
    if (!alive || props.audio.audioId !== audioId) return
    url.value = typeof URL.createObjectURL === 'function' ? URL.createObjectURL(blob) : null
    if (!url.value) { failed.value = true; return }
    await nextTick()
    // Autoplay may be refused by the browser; the player's own controls remain.
    try { void audioEl.value?.play?.()?.catch?.(() => undefined) } catch { /* no playback support */ }
  } catch {
    if (alive) failed.value = true
  } finally {
    if (alive) loading.value = false
  }
}

watch(() => props.audio.audioId, () => { revoke(); failed.value = false })

onBeforeUnmount(() => {
  alive = false
  revoke()
})
</script>

<style scoped>
.tla {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}

.tla-player {
  height: 32px;
  max-width: 100%;
}

.tla-play {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 16px;
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: 11.5px/1.3 var(--fg2-font);
  padding: 4px 12px;
  cursor: pointer;
}

.tla-play:disabled {
  cursor: progress;
  opacity: var(--fg2-control-disabled-opacity);
}

.tla-error {
  font-size: 11px;
  color: var(--fg2-status-danger-fg);
}
</style>
