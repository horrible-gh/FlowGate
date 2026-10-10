<template>
  <p>{{ target.title }}</p>
</template>

<script setup lang="ts">
defineProps<{ target: { title: string } }>()
</script>
