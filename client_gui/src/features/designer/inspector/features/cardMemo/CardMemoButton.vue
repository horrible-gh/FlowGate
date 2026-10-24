<template>
  <!-- flowgate.v02.0056 (0017-CH) — the header ✎ 메모 of a Graph card: opens a new card memo window (what the
       documents tab's memo [+] did). The memo windows are drawn here, so they stay whichever tab is shown. A card not
       stored yet, or a read-only view, keeps the button disabled with the reason as its tooltip. -->
  <button
    type="button"
    class="inspector-card-memo-button"
    :disabled="reason !== null"
    :title="reason ?? t(k('add'))"
    :aria-label="reason ?? t(k('add'))"
    data-test="designer-inspector-memo"
    :aria-expanded="target.nodeType === 'gate' ? gateMemoPanel(target).open : undefined"
    @click="openNew"
  >✎ <span class="inspector-card-memo-button-label">{{ t(k('label')) }}</span></button>
  <CardMemoWindows :target="target" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import CardMemoWindows from './CardMemoWindows.vue'
import { cardMemoLockedReason } from './cardMemoModel'
import { openCardMemoWindow } from './useCardMemoWindows'
import { gateMemoPanel } from './useGateMemo'
import { cardMemoCardOf } from './useCardMemo'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardMemo.${key}`

const card = computed(() => cardMemoCardOf(props.target))

/** Why a new memo cannot be written here, or null. */
const reason = computed(() => {
  if (!card.value) return t(k('unsaved'))
  const locked = cardMemoLockedReason(props.target)
  return locked ? t(k(locked)) : null
})

function openNew() {
  if (!card.value || reason.value !== null) return
  if (props.target.nodeType === 'gate') gateMemoPanel(props.target).open = !gateMemoPanel(props.target).open
  else openCardMemoWindow(card.value, null)
}
</script>

<style scoped>
/* Drawn as the Frame's ✎ 메모 (InspectorHeader `.inspector-header-memo`, v7 `.hd-memo`). */
.inspector-card-memo-button {
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

.inspector-card-memo-button:hover:not(:disabled) {
  border-color: #3b6ea8;
  background: #15345a;
  color: #fff;
}

.inspector-card-memo-button:disabled {
  cursor: not-allowed;
  opacity: 0.55;
}

.inspector-card-memo-button:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

/* 264px Inspector: the label folds to its glyph (as the Frame's ✎ 메모). */
@container (max-width: 420px) {
  .inspector-card-memo-button-label {
    display: none;
  }

  .inspector-card-memo-button {
    padding: 0 6px;
  }
}
</style>
