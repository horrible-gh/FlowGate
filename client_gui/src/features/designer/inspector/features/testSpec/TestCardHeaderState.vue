<template>
  <template v-if="card">
    <InspectorStatusChip :label="label" :tone="card.presentation.value.tone" dot data-test="test-card-header-state" :data-stage="card.presentation.value.stage" />
    <InspectorStatusChip v-if="card.presentation.value.resultState.key === 'unchecked'" :label="t(k('stage.unchecked'))" tone="neutral" />
  </template>
</template>
<script setup lang="ts">
// flowgate.v02.0150: the test card windows are not drawn here (the header goes with the selection) but by the Frame's
// windows host (TestCardWindowHost), so a selection change or a click elsewhere never closes them.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.${key}`
const card = useTestCard(() => props.target)
const label = computed(() => {
  const state = card.value?.presentation.value
  const run = card.value?.test.runState.value
  return state ? t(k(state.stateKey), { count: state.pending, done: run?.done ?? 0, total: run?.total ?? 0 }) : ''
})
</script>
