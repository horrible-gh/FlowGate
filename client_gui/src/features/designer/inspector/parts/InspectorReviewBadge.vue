<template>
  <!-- flowgate.v02.0051 — the v7 🛡 n review badge (0035 insp-v4 `.ib.rv`): on a slot line, the
       number of AI review rounds with the round state as its color — pass (green: no finding
       left), warn (amber: a response is needed), run (purple: a round is running), limit (red: the
       rounds are used up and findings remain). The hover title spells it out. What pressing it
       opens (the review detail) is the feature's; display only here. -->
  <button
    type="button"
    class="inspector-review-badge"
    :class="`inspector-review-badge--${state}`"
    :title="title"
    :aria-label="title"
    :disabled="disabled"
    data-test="inspector-review-badge"
    :data-state="state"
    @click="emit('click')"
  >🛡 {{ count }}</button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

export type InspectorReviewState = 'pass' | 'warn' | 'run' | 'limit'

const props = withDefaults(
  defineProps<{
    /** Review rounds so far (the running one included). */
    count: number
    state: InspectorReviewState
    /** Findings still open (pass / limit). */
    remain?: number | null
    disabled?: boolean
  }>(),
  { remain: null, disabled: false },
)

const emit = defineEmits<{ click: [] }>()

const { t } = useI18n()

const title = computed(() => t(`gui.designer.inspector.review.${props.state}`, { n: props.count, remain: props.remain ?? 0 }))
</script>

<style scoped>
.inspector-review-badge {
  display: inline-grid;
  place-items: center;
  height: 26px;
  min-width: 26px;
  padding: 0 7px;
  border: 1px solid #246c53;
  border-radius: 6px;
  background: #0d2b22;
  color: #72e4b8;
  font-size: 11.5px;
  font-weight: 700;
  line-height: 1;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-review-badge:hover:not(:disabled) {
  filter: brightness(1.15);
}

.inspector-review-badge:disabled {
  cursor: not-allowed;
  opacity: 0.4;
}

.inspector-review-badge--warn {
  border-color: #7b5a17;
  background: #3b2a0d;
  color: #ffc75d;
}

.inspector-review-badge--run {
  border-color: #5e4b9e;
  background: #201a3c;
  color: #c9b6ff;
}

.inspector-review-badge--limit {
  border-color: #71313d;
  background: #3a1c24;
  color: #ff9eae;
}
</style>
