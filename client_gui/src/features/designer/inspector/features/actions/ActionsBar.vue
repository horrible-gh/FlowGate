<template>
  <!-- flowgate.v02.0048 T#6 — header actions (D#1 §10.2 T#6 row): on v0.1 Flows the single run and the rework
       of a rejected document. A thin container: useActions reads and calls, actionsModel decides what is shown
       and usable. A disabled operation shows its one reason; a refused call shows why and the state is read
       again.
       flowgate.v02.0056 F2-1: the document 승인 / 반려 buttons (and the reject reason dialog, the TR commit line)
       are gone — a card is approved with its own [승인] (the Canvas card, the documents tab's approval bar), the
       gate keeps its ✓ 확인 · ✕ 반려, and a v0.2 card has no user [반려]. -->
  <div
    v-if="shown"
    class="inspector-actions"
    role="group"
    :aria-label="t(k('label'))"
    data-test="inspector-actions"
    :data-busy="busy ?? ''"
  >
    <Button
      v-if="availability.run.visible"
      size="sm"
      variant="secondary"
      :disabled="!availability.run.enabled"
      :loading="busy === 'run'"
      :title="t(k('run_hint'))"
      data-test="actions-run"
      @click="run"
    >{{ t(k('run')) }}</Button>
    <Button
      v-if="availability.rework.visible"
      size="sm"
      variant="secondary"
      :disabled="!availability.rework.enabled"
      :loading="busy === 'rework'"
      :title="t(k('rework_hint'))"
      data-test="actions-rework"
      @click="rework"
    >{{ t(k('rework')) }}</Button>
    <span v-if="line" class="inspector-actions-line" :title="line.text" data-test="actions-line" :data-kind="line.kind">
      <InspectorReason :text="line.text" :tone="line.tone" />
    </span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { ACTIONS_OPERATIONS } from './actionsModel'
import { useActions } from './useActions'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.actions.${key}`

const { availability, busy, failureKey, run, rework } = useActions(() => props.target)

const anyVisible = computed(() => ACTIONS_OPERATIONS.some((operation) => availability.value[operation].visible))

/** One line beside the buttons: a refusal, else the disabled reason. */
const line = computed(() => {
  if (failureKey.value) return { kind: 'failure', tone: 'danger' as const, text: t(k(failureKey.value)) }
  const disabled = ACTIONS_OPERATIONS.map((operation) => availability.value[operation]).find((item) => item.visible && !item.enabled)
  // `busy` is told by the button's own spinner.
  if (disabled?.reason && disabled.reason !== 'reason.busy') return { kind: 'reason', tone: 'muted' as const, text: t(k(disabled.reason)) }
  return null
})

const shown = computed(() => anyVisible.value || line.value !== null)
</script>

<style scoped>
.inspector-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-actions-line {
  display: block;
  max-width: 280px;
  min-width: 0;
  overflow: hidden;
  white-space: nowrap;
}

.inspector-actions-line :deep(.inspector-reason) {
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
