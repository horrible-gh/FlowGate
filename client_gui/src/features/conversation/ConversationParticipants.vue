<template>
  <div
    v-if="participants.length > 0"
    ref="rootEl"
    class="conv-people"
    data-test="conv-participants"
    tabindex="-1"
    @keydown.esc.stop.prevent="closeList(true)"
    @focusout="onFocusOut"
  >
    <span class="conv-people-label">{{ t('gui.conversation.participants.label') }}</span>
    <div ref="trackEl" class="conv-people-track">
      <button
        v-for="(p, index) in participants"
        v-show="index < visibleCount"
        :key="p.key"
        type="button"
        class="conv-person"
        :class="{ 'conv-person--ai': p.kind !== 'user' }"
        data-test="conv-participant"
        :data-last-read-seq="p.lastReadSeq"
        :title="description(p)"
        :aria-label="description(p)"
        aria-haspopup="dialog"
        :aria-expanded="listOpen"
        :aria-controls="listId"
        @click="openList($event, p.key)"
        @keydown.down.prevent="openList($event, p.key)"
      >
        <span aria-hidden="true">{{ p.kind === 'user' ? '●' : '◆' }}</span>
        <span class="conv-person-name">{{ p.displayName || p.key }}</span>
      </button>
      <button
        v-if="hiddenCount > 0"
        ref="moreEl"
        type="button"
        class="conv-person conv-people-more"
        data-test="conv-participants-more"
        :title="t('gui.conversation.participants.show_all', { count: participants.length })"
        :aria-label="t('gui.conversation.participants.show_all', { count: participants.length })"
        aria-haspopup="dialog"
        :aria-expanded="listOpen"
        :aria-controls="listId"
        @click="listOpen ? closeList(true) : openList($event)"
        @keydown.down.prevent="openList($event)"
      >+{{ hiddenCount }}</button>
    </div>

    <!-- Intrinsic widths remain measurable when the corresponding chip is folded.
         These non-interactive copies never enter layout, focus order or the accessibility tree. -->
    <div class="conv-people-measure" aria-hidden="true" inert>
      <div ref="measureEl" class="conv-people-measure-chips">
        <span v-for="p in participants" :key="p.key" class="conv-person">
          <span>{{ p.kind === 'user' ? '●' : '◆' }}</span>
          <span class="conv-person-name">{{ p.displayName || p.key }}</span>
        </span>
      </div>
      <span ref="moreProbeEl" class="conv-person conv-people-more">+{{ participants.length }}</span>
    </div>

    <section
      v-if="listOpen"
      :id="listId"
      ref="listEl"
      class="conv-people-popover"
      data-test="conv-participants-list"
      role="dialog"
      :aria-label="t('gui.conversation.participants.all', { count: participants.length })"
      :style="{ maxHeight: `${listMaxHeight}px` }"
    >
      <div class="conv-people-heading">
        <strong>{{ t('gui.conversation.participants.all', { count: participants.length }) }}</strong>
        <button type="button" class="conv-people-close" :aria-label="t('gui.conversation.close')" @click="closeList(true)">×</button>
      </div>
      <ul class="conv-people-list">
        <li
          v-for="(p, index) in participants"
          :key="p.key"
          tabindex="0"
          class="conv-people-entry"
          data-test="conv-participant-detail"
          :data-participant-key="p.key"
          :data-last-read-seq="p.lastReadSeq"
          :title="description(p)"
          @focus="focusedKey = p.key"
          @keydown.down.prevent="focusEntry(index + 1)"
          @keydown.up.prevent="focusEntry(index - 1)"
          @keydown.home.prevent="focusEntry(0)"
          @keydown.end.prevent="focusEntry(participants.length - 1)"
        >
          <span class="conv-people-icon" :class="{ 'conv-person--ai': p.kind !== 'user' }" aria-hidden="true">{{ p.kind === 'user' ? '●' : '◆' }}</span>
          <strong class="conv-people-name">{{ p.displayName || p.key }}</strong>
          <span class="conv-people-kind">{{ kindLabel(p) }}</span>
        </li>
      </ul>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed, getCurrentInstance, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { ConversationParticipant } from './types'

const props = defineProps<{ participants: readonly ConversationParticipant[] }>()
const { t, locale } = useI18n()
const listId = `conv-participants-${getCurrentInstance()!.uid}`
const rootEl = ref<HTMLElement | null>(null)
const trackEl = ref<HTMLElement | null>(null)
const measureEl = ref<HTMLElement | null>(null)
const moreProbeEl = ref<HTMLElement | null>(null)
const moreEl = ref<HTMLButtonElement | null>(null)
const listEl = ref<HTMLElement | null>(null)
const visibleCount = ref(props.participants.length)
const hiddenCount = computed(() => Math.max(0, props.participants.length - visibleCount.value))
const listOpen = ref(false)
const listMaxHeight = ref(280)
const focusedKey = ref<string | null>(null)
let opener: HTMLElement | null = null
let observer: ResizeObserver | null = null

function kindLabel(p: ConversationParticipant): string {
  return p.kind === 'user' || p.kind === 'ai' ? t(`gui.conversation.participants.${p.kind}`) : p.kind
}

function description(p: ConversationParticipant): string {
  // The read position (lastReadSeq) stays in the data and in data-last-read-seq, but is not shown in the UI.
  return `${p.displayName || p.key} · ${kindLabel(p)} · ${p.key}`
}

function fit(): void {
  const track = trackEl.value
  const measure = measureEl.value
  const probe = moreProbeEl.value
  if (!track || !measure || !probe || track.clientWidth === 0) return
  const widths = Array.from(measure.children, (el) => el.getBoundingClientRect().width)
  if (widths.length !== props.participants.length) return
  const gap = Number.parseFloat(getComputedStyle(track).columnGap) || 0
  const prefix = [0]
  for (const width of widths) prefix.push(prefix[prefix.length - 1] + width)
  let count = widths.length
  if (prefix[count] + gap * Math.max(0, count - 1) > track.clientWidth) {
    // Reserve exactly +N, including the gap before it. No chip is forced into a
    // space it cannot fit, and a two-digit overflow count is measured as such.
    for (count = widths.length - 1; count >= 0; count--) {
      probe.textContent = `+${widths.length - count}`
      if (prefix[count] + gap * count + probe.getBoundingClientRect().width <= track.clientWidth) break
    }
  }
  const activeChip = track.contains(document.activeElement) ? document.activeElement as HTMLElement : null
  visibleCount.value = Math.max(0, count)
  if (activeChip) void nextTick(() => {
    if (!activeChip.isConnected || activeChip.style.display === 'none') {
      const target = moreEl.value ?? track.querySelector<HTMLElement>('button:not([style*="display: none"])')
      target?.focus()
    }
  })
  const row = rootEl.value!.getBoundingClientRect()
  const panelBottom = rootEl.value!.closest('.conv-panel')?.getBoundingClientRect().bottom ?? window.innerHeight
  listMaxHeight.value = Math.max(0, Math.min(280, window.innerHeight - row.bottom - 8, panelBottom - row.bottom - 8))
}

function focusEntry(index: number): void {
  const entries = listEl.value?.querySelectorAll<HTMLElement>('[data-participant-key]')
  if (!entries?.length) return
  entries[(index + entries.length) % entries.length]?.focus()
}

async function openList(event: Event, key?: string): Promise<void> {
  opener = event.currentTarget as HTMLElement
  fit()
  listOpen.value = true
  await nextTick()
  focusEntry(Math.max(0, props.participants.findIndex((p) => p.key === key)))
}

function closeList(restoreFocus = false): void {
  listOpen.value = false
  focusedKey.value = null
  if (restoreFocus) {
    const target = opener?.isConnected && opener.style.display !== 'none' ? opener
      : moreEl.value ?? trackEl.value?.querySelector<HTMLElement>('button:not([style*="display: none"])') ?? rootEl.value
    target?.focus()
  }
}

function onPointerDown(event: PointerEvent): void {
  if (listOpen.value && !rootEl.value?.contains(event.target as Node)) closeList()
}

function onFocusOut(event: FocusEvent): void {
  if (event.relatedTarget && !rootEl.value?.contains(event.relatedTarget as Node)) closeList()
}

// The strip may initially be empty or be unmounted when switching tools/chats.
watch([rootEl, trackEl, measureEl], () => {
  observer?.disconnect()
  for (const element of [rootEl.value, trackEl.value, measureEl.value, rootEl.value?.parentElement]) {
    if (element) observer?.observe(element)
  }
  fit()
}, { flush: 'post' })

watch(() => props.participants, () => {
  if (!props.participants.length) closeList()
  fit()
  if (listOpen.value && focusedKey.value && !props.participants.some((p) => p.key === focusedKey.value)) focusEntry(0)
}, { deep: true, flush: 'post' })
watch(locale, () => { fit() }, { flush: 'post' })

onMounted(() => {
  if (typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(fit)
    for (const element of [rootEl.value, trackEl.value, measureEl.value, rootEl.value?.parentElement]) {
      if (element) observer.observe(element)
    }
  }
  window.addEventListener('resize', fit)
  document.addEventListener('pointerdown', onPointerDown)
  fit()
})

onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('resize', fit)
  document.removeEventListener('pointerdown', onPointerDown)
})
</script>

<style scoped>
.conv-people {
  position: relative;
  flex: 0 0 auto;
  min-width: 0;
  min-height: 29px;
  padding: 5px 8px;
  display: flex;
  align-items: center;
  gap: 5px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
}
.conv-people-label { flex: 0 0 auto; font-size: 10px; color: var(--fg2-muted-2); }
.conv-people-track { flex: 1 1 0; min-width: 0; display: flex; align-items: center; gap: 5px; }
.conv-person {
  box-sizing: border-box;
  flex: 0 0 auto;
  height: 21px;
  padding: 0 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 10px;
  line-height: normal;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  gap: 3px;
}
button.conv-person { cursor: pointer; }
.conv-person--ai { color: var(--fg2-violet); }
.conv-people-more { min-width: 32px; justify-content: center; color: var(--fg2-violet); background: var(--fg2-control-surface); }
.conv-people-measure { position: absolute; width: 0; height: 0; overflow: hidden; visibility: hidden; pointer-events: none; }
.conv-people-measure-chips { display: flex; width: max-content; }
.conv-people-popover {
  position: absolute;
  top: 100%;
  right: 8px;
  z-index: 20;
  box-sizing: border-box;
  width: min(320px, calc(100% - 16px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
  color: var(--fg2-text);
  font-size: 11px;
}
.conv-people-heading { flex: 0 0 auto; display: flex; align-items: center; justify-content: space-between; padding: 5px 8px; border-bottom: 1px solid var(--fg2-line); }
.conv-people-close { flex: 0 0 auto; border: 0; border-radius: var(--fg2-control-radius-sm); background: transparent; color: var(--fg2-muted); font: inherit; font-size: 18px; cursor: pointer; }
.conv-people-list { min-height: 0; margin: 0; padding: 4px; list-style: none; overflow: auto; overscroll-behavior: contain; }
.conv-people-entry { display: flex; align-items: baseline; gap: 7px; padding: 6px 5px; border-radius: var(--fg2-control-radius-sm); }
.conv-people-icon { flex: 0 0 auto; }
.conv-people-name { flex: 1 1 auto; min-width: 0; font-weight: 600; overflow-wrap: anywhere; }
.conv-people-kind { flex: 0 0 auto; color: var(--fg2-muted-2); font-size: 10px; }
button.conv-person:hover, .conv-people-entry:hover, .conv-people-close:hover { background: var(--fg2-control-surface); }
.conv-person:focus-visible, .conv-people-entry:focus-visible, .conv-people-close:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: -2px; }
</style>
