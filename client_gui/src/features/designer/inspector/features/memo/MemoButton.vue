<template>
  <!-- flowgate.v02.0053 T#9 — the header ✎ 메모 of a card with a document (v7 `.hd-memo`): dim while the
       document has no memo (`.none`), bright once it has one. A click opens the newest memo in the document
       window, or writes a new one and opens it in its editor. A refused write, or memos that could not be read,
       show in the tooltip only. -->
  <button
    type="button"
    class="inspector-memo"
    :class="{ 'is-none': !latest && !unread, 'is-failed': failure !== null || unread }"
    :disabled="busy"
    :title="tip"
    :aria-label="tip"
    data-test="designer-inspector-memo"
    :data-memo="latest?.docId ?? ''"
    @click="onClick"
  >✎ <span class="inspector-memo-label">{{ t(k('label')) }}</span></button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import { useMemo } from './useMemo'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.memo.${key}`

const { latest, unread, busy, failure, open } = useMemo(() => props.target)

const tip = computed(() => {
  if (failure.value) return t(k(`failure.${failure.value}`))
  if (unread.value) return t(k('unread'))
  return t(k(latest.value ? 'open' : 'write'))
})

function onClick() {
  void open(t(k('title'), { title: props.target.title }))
}
</script>

<style scoped>
/* Drawn as the Frame's ✎ 메모 (InspectorHeader `.inspector-header-memo`, v7 `.hd-memo`). */
.inspector-memo {
  flex: 0 0 auto;
  height: 26px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 6px;
  background: transparent;
  color: #c3d3e3;
  font: inherit;
  font-size: 11.5px;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-memo:hover:not(:disabled) {
  border-color: #3b6ea8;
  background: #15345a;
  color: #fff;
}

/* v7 `.hd-memo.none`: no memo yet — dim. */
.inspector-memo.is-none {
  color: #6f87a0;
}

.inspector-memo.is-failed {
  border-color: #b4474f;
}

.inspector-memo:disabled {
  cursor: progress;
  opacity: 0.7;
}

.inspector-memo:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

/* 264px Inspector: the label folds to its glyph (as the Frame's ✎ 메모). */
@container (max-width: 420px) {
  .inspector-memo-label {
    display: none;
  }

  .inspector-memo {
    padding: 0 6px;
  }
}
</style>
