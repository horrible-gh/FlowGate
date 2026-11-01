<template>
  <!-- flowgate.v02.0113 T#4 (0030-M §1, 0029-TR 시안): the AI tab is the card's AI settings only — 지시 · 실행 · 검수 AI,
       검수 횟수 and 고급 ▾. The invocation history is no longer drawn here; its server record and read API stay. -->
  <div data-test="inspector-ai">
    <SettingsTab :target="target" panel="ai" />
  </div>
</template>
<script setup lang="ts">
import type { InspectorTarget } from '../../inspectorTarget'
import SettingsTab from '../settings/SettingsTab.vue'
defineProps<{ target: InspectorTarget }>()
</script>
