<template>
  <!-- flowgate.v02.0150 — the windows of every test card with one open (testCardWindowCards), drawn by the Frame for as
       long as it lives whatever is selected: no card, another card, another tab. Each card's windows keep the target it
       was last selected as; a card leaves when its last window closes (its own close / save / cancel). -->
  <TestCardWindows v-for="[key, card] in cards" :key="key" :target="card" @idle="releaseTestCardWindows(key)" />
</template>
<script setup lang="ts">
import { computed, watch } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import TestCardWindows from './TestCardWindows.vue'
import { followTestCardWindows, releaseTestCardWindows, testCardWindowCards } from './testCardSession'
const props = defineProps<{ target: InspectorTarget | null }>()
const cards = computed(() => [...testCardWindowCards.entries()])
watch(() => props.target, (target) => followTestCardWindows(target), { immediate: true })
</script>
