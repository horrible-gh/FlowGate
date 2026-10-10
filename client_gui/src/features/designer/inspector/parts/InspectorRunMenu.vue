<template>
  <!-- flowgate.v02.0051 — the ⋯ menu items of a run card (0035 insp.js menu-pop, n.mode === 'run'):
       실행 이력 · ■ 중단 · ↻ 재실행. The run history, the stop and the re-run of a card have no
       Inspector API yet (0048 D#1 §8.4: Graph 실행 연동), so every item is shown disabled with that
       reason (0051-R §2.3) — the v7 menu keeps its shape, nothing looks as if it worked. Display
       only: a research / task card feature places it through its own wrapper. -->
  <div class="inspector-run-menu" data-test="inspector-run-menu">
    <InspectorMenuItem v-for="item in ITEMS" :key="item" :item="item" disabled :reason="t('gui.designer.inspector.unconnected')">{{ t(`gui.designer.inspector.menu.${item}`) }}</InspectorMenuItem>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import InspectorMenuItem from './InspectorMenuItem.vue'

const ITEMS = ['history', 'stop', 'rerun'] as const

const { t } = useI18n()
</script>

<style scoped>
.inspector-run-menu {
  display: flex;
  flex-direction: column;
  min-width: 0;
}
</style>
