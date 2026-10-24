<template>
  <template v-if="card">
    <component :is="component" v-for="[kind, component] in components" :key="`${testCardSessionKey(target)}:${kind}`" :target="target" />
  </template>
</template>
<script setup lang="ts">
import { computed, defineAsyncComponent, type Component } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { TEST_CARD_WINDOW_LOADERS, testCardSessionKey, type TestCardWindowKind } from './testCardSession'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget }>()
const card = useTestCard(() => props.target)
const loaded = Object.entries(TEST_CARD_WINDOW_LOADERS).map(([path, loader]) => [
  path.split('/').pop()!.replace('Window.vue', '').toLowerCase() as TestCardWindowKind,
  defineAsyncComponent(loader as () => Promise<Component>),
] as const)
// Closed/card-switched windows unmount; drafts and shell geometry live in the card session.
const components = computed(() => loaded.filter(([kind]) => card.value && card.value.windows[kind].open))
</script>
