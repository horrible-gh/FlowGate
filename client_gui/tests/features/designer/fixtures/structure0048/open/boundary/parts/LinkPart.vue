<!-- structure.0048 B15 fixture (boundary): a part opening a tab through globalThis. -->
<template>
  <button type="button" @click="go">open</button>
</template>

<script setup lang="ts">
const props = defineProps<{ url: string }>()
const go = () => globalThis.open(props.url)
</script>
