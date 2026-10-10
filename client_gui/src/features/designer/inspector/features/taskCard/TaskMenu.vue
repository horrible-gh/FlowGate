<template>
  <!-- flowgate.v02.0051 stage 2 — the 작업 card's ⋯ menu items: the common run-card items
       (실행 이력 · ■ 중단 · ↻ 재실행), all shown disabled until their API exists (D#1 §8.4). -->
  <InspectorRunMenu data-test="inspector-task-menu" />
</template>

<script setup lang="ts">
import type { InspectorTarget } from '../../inspectorTarget'
import InspectorRunMenu from '../../parts/InspectorRunMenu.vue'

defineProps<{
  target: InspectorTarget
  close?: () => void
}>()
</script>
