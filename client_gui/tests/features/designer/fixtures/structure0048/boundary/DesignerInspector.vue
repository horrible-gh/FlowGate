<template>
  <section />
</template>

<script setup lang="ts">
import { useProjectStore } from '@/features/project'
import StatusChip from './parts/StatusChip.vue'

void useProjectStore
void StatusChip
void fetch('/api/v1/documents')
</script>
