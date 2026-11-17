<template>
  <!-- flowgate.v02test.0029 T0005 — the images of one message: a row of thumbnails under its text; pressing one opens the
       original large (a modal, closed with Esc, the backdrop or ✕). The bytes are fetched through the signed-in session
       and shown from a blob address — the content address is never put on `<img src>` and no token is put on a URL. -->
  <div class="tl-imgs" data-test="tl-images">
    <button
      v-for="(image, index) in images"
      :key="image.imageId"
      type="button"
      class="tl-img"
      :class="`is-${stateOf(image.imageId)}`"
      data-test="tl-image"
      :data-state="stateOf(image.imageId)"
      :aria-label="t('gui.conversation.image.open', { n: index + 1, total: images.length })"
      :disabled="stateOf(image.imageId) === 'loading'"
      @click="onThumb(image, index)"
    >
      <img v-if="urls[image.imageId]" class="tl-img-pic" :src="urls[image.imageId]" :alt="t('gui.conversation.image.alt', { n: index + 1 })" />
      <span v-else-if="stateOf(image.imageId) === 'failed'" class="tl-img-note" data-test="tl-image-failed">{{ t('gui.conversation.image.load_failed') }}</span>
      <span v-else class="tl-img-note" aria-hidden="true">…</span>
    </button>

    <Teleport to="body">
      <div
        v-if="openIndex !== null && openImage"
        class="tl-imgview"
        data-test="tl-image-viewer"
        role="dialog"
        aria-modal="true"
        :aria-label="t('gui.conversation.image.viewer')"
        @click.self="close"
      >
        <button ref="closeEl" type="button" class="tl-imgview-close" data-test="tl-image-close" :aria-label="t('gui.conversation.close')" @click="close">✕</button>
        <button v-if="images.length > 1" type="button" class="tl-imgview-nav tl-imgview-prev" data-test="tl-image-prev" :aria-label="t('gui.conversation.image.prev')" @click="step(-1)">‹</button>
        <img v-if="urls[openImage.imageId]" class="tl-imgview-pic" data-test="tl-image-full" :src="urls[openImage.imageId]" :alt="t('gui.conversation.image.alt', { n: openIndex + 1 })" />
        <span v-else class="tl-imgview-note">{{ t('gui.conversation.image.load_failed') }}</span>
        <button v-if="images.length > 1" type="button" class="tl-imgview-nav tl-imgview-next" data-test="tl-image-next" :aria-label="t('gui.conversation.image.next')" @click="step(1)">›</button>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { fetchMessageImageBlob } from './api'
import type { TurnImage } from './types'

const props = defineProps<{ images: readonly TurnImage[] }>()

const { t } = useI18n()

type LoadState = 'loading' | 'ready' | 'failed'
const urls = ref<Record<string, string>>({})
const states = ref<Record<string, LoadState>>({})
const stateOf = (id: string): LoadState => states.value[id] ?? 'loading'

const openIndex = ref<number | null>(null)
const openImage = computed(() => (openIndex.value === null ? null : props.images[openIndex.value] ?? null))
const closeEl = ref<HTMLButtonElement | null>(null)
let opener: HTMLElement | null = null
let alive = true

function makeUrl(blob: Blob): string | null {
  return typeof URL.createObjectURL === 'function' ? URL.createObjectURL(blob) : null
}

function drop(id: string): void {
  const url = urls.value[id]
  if (url && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
  const { [id]: _gone, ...rest } = urls.value
  urls.value = rest
}

async function load(image: TurnImage): Promise<void> {
  states.value = { ...states.value, [image.imageId]: 'loading' }
  try {
    const url = makeUrl(await fetchMessageImageBlob(image.url))
    if (!alive || !props.images.some((i) => i.imageId === image.imageId)) {
      if (url && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
      return
    }
    if (url) urls.value = { ...urls.value, [image.imageId]: url }
    states.value = { ...states.value, [image.imageId]: url ? 'ready' : 'failed' }
  } catch {
    if (alive) states.value = { ...states.value, [image.imageId]: 'failed' }
  }
}

watch(
  () => props.images.map((image) => image.imageId).join('|'),
  () => {
    const ids = new Set(props.images.map((image) => image.imageId))
    for (const id of Object.keys(urls.value)) if (!ids.has(id)) drop(id)
    for (const image of props.images) if (!(image.imageId in states.value)) void load(image)
    if (openIndex.value !== null && openIndex.value >= props.images.length) close()
  },
  { immediate: true },
)

function onThumb(image: TurnImage, index: number): void {
  // A thumbnail that did not load is tried again by pressing it; a loaded one opens large.
  if (stateOf(image.imageId) === 'failed') {
    void load(image)
    return
  }
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
  openIndex.value = index
}

function close(): void {
  openIndex.value = null
  const target = opener
  opener = null
  void nextTick(() => target?.focus?.())
}

function step(delta: number): void {
  if (openIndex.value === null || props.images.length === 0) return
  openIndex.value = (openIndex.value + delta + props.images.length) % props.images.length
}

function onKey(event: KeyboardEvent): void {
  if (event.key === 'Escape') {
    event.stopPropagation()
    close()
  } else if (event.key === 'ArrowLeft') step(-1)
  else if (event.key === 'ArrowRight') step(1)
}

watch(openIndex, (now, before) => {
  if (now !== null && before === null) {
    document.addEventListener('keydown', onKey, true)
    void nextTick(() => closeEl.value?.focus())
  } else if (now === null && before !== null) document.removeEventListener('keydown', onKey, true)
})

onBeforeUnmount(() => {
  alive = false
  document.removeEventListener('keydown', onKey, true)
  for (const id of Object.keys(urls.value)) drop(id)
})
</script>

<style scoped>
.tl-imgs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 6px;
}

.tl-img {
  width: 96px;
  height: 72px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  overflow: hidden;
  cursor: zoom-in;
  display: grid;
  place-items: center;
  color: var(--fg2-muted);
  font-size: 11px;
}

.tl-img.is-failed {
  cursor: pointer;
  border-color: var(--fg2-status-danger-border);
  color: var(--fg2-status-danger-fg);
}

.tl-img.is-loading {
  cursor: progress;
}

.tl-img:focus-visible,
.tl-imgview button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.tl-img-pic {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

.tl-img-note {
  padding: 4px;
  text-align: center;
}

.tl-imgview {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.78);
}

.tl-imgview-pic {
  max-width: 92vw;
  max-height: 90vh;
  object-fit: contain;
  background: #111;
}

.tl-imgview-note {
  color: #fff;
}

.tl-imgview-close,
.tl-imgview-nav {
  position: absolute;
  border: 1px solid rgba(255, 255, 255, 0.4);
  border-radius: 50%;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  cursor: pointer;
  width: 34px;
  height: 34px;
  font-size: 16px;
  line-height: 1;
}

.tl-imgview-close {
  top: 14px;
  right: 14px;
}

.tl-imgview-nav {
  top: 50%;
  transform: translateY(-50%);
  font-size: 22px;
}

.tl-imgview-prev {
  left: 14px;
}

.tl-imgview-next {
  right: 14px;
}
</style>
