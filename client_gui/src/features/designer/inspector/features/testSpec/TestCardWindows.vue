<template>
  <template v-if="card">
    <component :is="component" v-for="[kind, component] in components" :key="`${testCardSessionKey(target)}:${kind}`" :target="target" />
  </template>
</template>
<script setup lang="ts">
// flowgate.v02.0150: one card's open windows, drawn by the Frame's TestCardWindowHost with the target the card was last
// selected as (or by a page of its own); `idle` says no window of the card is open any more.
import { computed, defineAsyncComponent, watch, type Component } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { TEST_CARD_WINDOW_LOADERS, testCardSessionKey, type TestCardWindowKind } from './testCardSession'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget }>()
const emit = defineEmits<{ idle: [] }>()
const card = useTestCard(() => props.target)
const loaded = Object.entries(TEST_CARD_WINDOW_LOADERS).map(([path, loader]) => [
  path.split('/').pop()!.replace('Window.vue', '').toLowerCase() as TestCardWindowKind,
  defineAsyncComponent(loader as () => Promise<Component>),
] as const)
// Closed/card-switched windows unmount; drafts and shell geometry live in the card session.
const components = computed(() => loaded.filter(([kind]) => card.value && card.value.windows[kind].open))
watch(() => components.value.length === 0, (idle) => { if (idle) emit('idle') }, { immediate: true })
</script>
