<template>
  <!-- structure.0048 boundary fixture (flowgate.v02.0051 B14): a template handler that runs an undecided feature. -->
  <p>{{ target.title }}</p>
  <button type="button" @click="startTsc">run</button>
</template>

<script setup lang="ts">
defineProps<{ target: { title: string } }>()

function startTsc(): void {
  // the handler is the violation; its body is not read
}
</script>
