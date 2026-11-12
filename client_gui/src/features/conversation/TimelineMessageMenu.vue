<template>
  <!-- flowgate.v02test.0025 — a message's ⋯ menu, right of its copy button. Shown while the context setting is
       체크포인트 사용: ⤒ send from this message (included) / ⤓ from the next message (excluded) / ✕ clear the checkpoint.
       Same popover behaviour as the composer's ⋯ menu (a press outside or Esc closes it). -->
  <div ref="rootEl" class="tlm">
    <button
      type="button"
      class="tlm-btn"
      :class="{ 'is-on': !!kind }"
      data-test="tl-more"
      aria-haspopup="menu"
      :aria-expanded="open"
      :aria-label="t('gui.conversation.checkpoint.menu')"
      :title="t('gui.conversation.checkpoint.menu')"
      :disabled="busy"
      @click="toggleMenu"
    >⋯</button>
    <div v-if="open" ref="popEl" class="tlm-pop" role="menu" data-test="tl-more-menu" :aria-label="t('gui.conversation.checkpoint.menu')">
      <!-- The icon says where the line goes: ⤒ above this message (it is sent), ⤓ below it (it is not). ✓ marks the
           way in use, at the right. -->
      <button
        type="button"
        role="menuitemradio"
        class="tlm-item"
        data-test="tl-checkpoint-include"
        :aria-checked="kind === 'include'"
        @click="pick(seq, 'include')"
      >
        <span class="tlm-icon" aria-hidden="true">⤒</span>
        <span class="tlm-label">{{ t('gui.conversation.checkpoint.from_here') }}</span>
        <span class="tlm-check" aria-hidden="true">{{ kind === 'include' ? '✓' : '' }}</span>
      </button>
      <button
        type="button"
        role="menuitemradio"
        class="tlm-item"
        data-test="tl-checkpoint-exclude"
        :aria-checked="kind === 'exclude'"
        @click="pick(seq, 'exclude')"
      >
        <span class="tlm-icon" aria-hidden="true">⤓</span>
        <span class="tlm-label">{{ t('gui.conversation.checkpoint.after_here') }}</span>
        <span class="tlm-check" aria-hidden="true">{{ kind === 'exclude' ? '✓' : '' }}</span>
      </button>
      <button
        v-if="kind"
        type="button"
        role="menuitem"
        class="tlm-item"
        data-test="tl-checkpoint-clear"
        @click="pick(null)"
      >
        <span class="tlm-icon" aria-hidden="true">✕</span>
        <span class="tlm-label">{{ t('gui.conversation.checkpoint.clear_here') }}</span>
        <span class="tlm-check" aria-hidden="true"></span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePopover } from './popover'
import type { CheckpointKind } from './types'

const props = defineProps<{
  /** The message's seq. */
  seq: number
  /** This message's checkpoint kind when it is the chat's checkpoint, else null. */
  kind: CheckpointKind | null
  busy?: boolean
}>()

const emit = defineEmits<{ checkpoint: [seq: number | null, kind?: CheckpointKind] }>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const popEl = ref<HTMLElement | null>(null)
const { open, close } = usePopover(rootEl)

function toggleMenu(): void {
  open.value = !open.value
  // The log scrolls: keep the opened menu in view (the last message's menu would otherwise open below the fold).
  if (open.value) void nextTick(() => popEl.value?.scrollIntoView?.({ block: 'nearest' }))
}

function pick(seq: number | null, kind?: CheckpointKind): void {
  close()
  if (seq == null) emit('checkpoint', null)
  else if (kind !== props.kind) emit('checkpoint', seq, kind)
}
</script>

<style scoped>
.tlm {
  position: relative;
  flex-shrink: 0;
}

.tlm-btn {
  width: 18px;
  height: 18px;
  padding: 0;
  display: grid;
  place-items: center;
  border: 1px solid transparent;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
}

.tlm-btn:hover,
.tlm-btn:focus-visible,
.tlm-btn[aria-expanded='true'] {
  border-color: var(--fg2-line-strong);
  color: var(--fg2-text);
}

.tlm-btn.is-on {
  color: var(--fg2-status-info-fg);
  border-color: var(--fg2-status-info-border);
}

.tlm-btn:disabled {
  cursor: default;
  opacity: 0.5;
}

.tlm-pop {
  position: absolute;
  top: calc(100% + 3px);
  right: 0;
  z-index: 30;
  width: max-content;
  min-width: 150px;
  padding: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
}

.tlm-item {
  width: 100%;
  height: 28px;
  padding: 0 8px 0 4px;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  text-align: left;
  font: inherit;
  font-size: 11px;
  white-space: nowrap;
  cursor: pointer;
}

.tlm-item:hover {
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
}

.tlm-icon {
  flex: 0 0 14px;
  text-align: center;
  color: var(--fg2-status-info-fg);
}

.tlm-label {
  flex: 1;
}

.tlm-check {
  flex: 0 0 12px;
  margin-left: 8px;
  text-align: center;
  color: var(--fg2-status-info-fg);
}

.tlm-btn:focus-visible,
.tlm-item:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
