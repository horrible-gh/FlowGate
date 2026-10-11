<template>
  <!-- flowgate.v02.0053 T#4 — a test state as a glyph and a color; the word is the tooltip (the v0.1
       verdict / mode badges carried the word on screen). `text` is a number or a short value shown after it. -->
  <span
    class="test-mark"
    :class="`test-mark--${mark.tone}`"
    role="img"
    :aria-label="label"
    :title="label"
    data-test="test-mark"
    :data-key="mark.key"
    :data-tone="mark.tone"
  >{{ mark.glyph }}<span v-if="text !== undefined && text !== null" class="test-mark-text">{{ text }}</span></span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestMark } from './specialDocumentModel'

const props = defineProps<{
  mark: TestMark
  text?: string | number | null
  /** Overrides the tooltip (default: the mark's word). */
  tooltip?: string
}>()

const { t } = useI18n()

const label = computed(() => props.tooltip ?? t(`gui.document_window.${props.mark.key}`))
</script>

<style scoped>
.test-mark {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  gap: 3px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  box-sizing: border-box;
  border: 1px solid var(--fg2-line);
  border-radius: 9px;
  color: var(--fg2-muted);
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
}

.test-mark--success {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.test-mark--danger {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.test-mark--warning {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.test-mark--info {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.test-mark-text {
  font-weight: 600;
}
</style>
