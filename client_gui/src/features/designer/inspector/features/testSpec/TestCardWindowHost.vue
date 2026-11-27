<template>
  <!-- the windows of every test card with one open (testCardWindowCards), drawn by the Frame for as
       long as it lives whatever is selected: no card, another card, another tab. Each card's windows keep the target it
       was last selected as; a card leaves when its last window closes (its own close / save / cancel). -->
  <TestCardWindows v-for="[key, card] in cards" :key="key" :target="card" @idle="releaseTestCardWindows(key)" />
</template>
<script setup lang="ts">
import { computed, watch } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import TestCardWindows from './TestCardWindows.vue'
import { followTestCardWindows, releaseTestCardWindows, testCardScenarioRequest, testCardScenarioRequested, testCardWindowCards } from './testCardSession'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget | null }>()
const cards = computed(() => [...testCardWindowCards.entries()])
watch(() => props.target, (target) => followTestCardWindows(target), { immediate: true })

// F81-9: the Canvas 시나리오 button's request — the selected card's scenario window opens once it can.
const requested = computed(() => testCardScenarioRequested(testCardScenarioRequest.value, props.target) ? props.target : null)
const requestedCard = useTestCard(() => requested.value)
watch(() => props.target, (target) => {
  if (testCardScenarioRequest.value && target && !testCardScenarioRequested(testCardScenarioRequest.value, target)) testCardScenarioRequest.value = null
})
watch(() => !!requested.value && !!requestedCard.value?.canOpen('scenario'), (ready) => {
  if (!ready || !requestedCard.value) return
  if (requestedCard.value.openWindow('scenario')) testCardScenarioRequest.value = null
}, { immediate: true })
</script>
