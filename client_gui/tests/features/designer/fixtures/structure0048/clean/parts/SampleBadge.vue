<template>
  <Badge>{{ label }}</Badge>
</template>

<script setup lang="ts">
import Badge from '../../../../components/ui/Badge.vue'

defineProps<{ label: string }>()
</script>
