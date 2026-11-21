<template>
  <!-- the composer's attached message images: a thumbnail per image above the input, each
       with its own state (uploading / checking a restored one / failed with 다시 시도 / gone) and a ✕ to take it out, then the notes that apply
       (what was refused and images that hold the send). -->
  <div v-if="images.length || notice" class="conv-imgs" data-test="conv-images">
    <ul v-if="images.length" class="conv-imgs-list">
      <li
        v-for="(image, index) in images"
        :key="image.localId"
        class="conv-img"
        :class="`is-${image.status}`"
        data-test="conv-image"
        :data-status="image.status"
      >
        <img v-if="image.previewUrl" class="conv-img-pic" :src="image.previewUrl" :alt="t('gui.conversation.image.alt', { n: index + 1 })" />
        <span v-else class="conv-img-pic conv-img-blank" aria-hidden="true">🖼</span>
        <span v-if="image.status === 'uploading'" class="conv-img-state" role="status" data-test="conv-image-uploading">
          <span class="conv-img-ring" aria-hidden="true"></span>{{ t('gui.conversation.image.uploading') }}
        </span>
        <span v-else-if="image.status === 'checking'" class="conv-img-state" role="status" data-test="conv-image-checking">
          <span class="conv-img-ring" aria-hidden="true"></span>{{ t('gui.conversation.image.checking') }}
        </span>
        <span v-else-if="image.status === 'failed'" class="conv-img-state conv-img-state--bad" role="alert" data-test="conv-image-error">
          {{ t(`gui.conversation.image.error.${image.error ?? 'failed'}`) }}
          <button type="button" class="conv-img-retry" data-test="conv-image-retry" @click="emit('retry', image.localId)">{{ t('gui.conversation.retry') }}</button>
        </span>
        <span v-else-if="image.status === 'missing'" class="conv-img-state conv-img-state--bad" role="alert" data-test="conv-image-missing">
          {{ t('gui.conversation.image.missing') }}
        </span>
        <button
          type="button"
          class="conv-img-remove"
          data-test="conv-image-remove"
          :aria-label="t('gui.conversation.image.remove')"
          :title="t('gui.conversation.image.remove')"
          @click="emit('remove', image.localId)"
        >✕</button>
      </li>
    </ul>
    <p v-if="notice" class="conv-imgs-note conv-imgs-note--warn" role="status" data-test="conv-image-notice" :data-notice="notice">
      {{ t(`gui.conversation.image.notice.${notice}`) }}
      <button type="button" class="conv-img-retry" :aria-label="t('gui.conversation.close')" @click="emit('dismiss-notice')">✕</button>
    </p>
    <p v-if="hint" class="conv-imgs-note" role="status" data-test="conv-image-hint" :data-hint="hint">
      {{ t(`gui.conversation.image.hint.${hint}`) }}
    </p>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ComposerImage, ImageNoticeKey } from './composerImages'

defineProps<{
  images: readonly ComposerImage[]
  notice: ImageNoticeKey | null
  /** Why the send is held right now (an image still uploading / not usable). */
  hint: 'uploading' | 'checking' | 'unusable' | null
}>()

const emit = defineEmits<{ remove: [localId: string]; retry: [localId: string]; 'dismiss-notice': [] }>()

const { t } = useI18n()
</script>

<style scoped>
.conv-imgs {
  margin-bottom: 6px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.conv-imgs-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.conv-img {
  position: relative;
  width: 72px;
  min-height: 72px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.conv-img.is-failed,
.conv-img.is-missing {
  border-color: var(--fg2-status-danger-border);
}

.conv-img-pic {
  width: 100%;
  height: 54px;
  object-fit: cover;
  display: block;
}

.conv-img-blank {
  display: grid;
  place-items: center;
  font-size: 22px;
  color: var(--fg2-muted-2);
}

.conv-img.is-uploading .conv-img-pic,
.conv-img.is-checking .conv-img-pic {
  opacity: 0.55;
}

.conv-img.is-missing .conv-img-pic {
  opacity: 0.35;
}

.conv-img-state {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 3px;
  padding: 2px 4px;
  font-size: 10px;
  line-height: 1.25;
}

.conv-img-state--bad {
  color: var(--fg2-status-danger-fg);
  background: var(--fg2-status-danger-bg);
}

.conv-img-ring {
  display: inline-block;
  width: 9px;
  height: 9px;
  box-sizing: border-box;
  border: 2px solid var(--fg2-line-strong);
  border-top-color: var(--fg2-muted);
  border-radius: 50%;
  animation: conv-img-spin 0.8s linear infinite;
}

@keyframes conv-img-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (prefers-reduced-motion: reduce) {
  .conv-img-ring {
    animation: none;
  }
}

.conv-img-remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 18px;
  height: 18px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 50%;
  background: var(--fg2-panel);
  color: var(--fg2-text);
  font-size: 10px;
  line-height: 1;
  cursor: pointer;
}

.conv-img-remove:focus-visible,
.conv-img-retry:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-img-retry {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  cursor: pointer;
  padding: 0;
}

.conv-imgs-note {
  margin: 0;
}

.conv-imgs-note--warn {
  color: var(--fg2-status-warning-fg);
}
</style>
