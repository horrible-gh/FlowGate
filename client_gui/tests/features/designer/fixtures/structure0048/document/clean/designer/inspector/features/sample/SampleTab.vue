<template>
  <p>{{ resource.data.value?.title }}</p>
</template>

<script setup lang="ts">
import type { InspectorTarget } from '../../inspectorTarget'
import { useSample } from './useSample'

const props = defineProps<{ target: InspectorTarget }>()
const resource = useSample(() => props.target)
</script>
