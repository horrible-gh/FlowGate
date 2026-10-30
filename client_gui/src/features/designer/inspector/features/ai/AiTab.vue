<template>
  <div data-test="inspector-ai">
    <SettingsTab :target="target" panel="ai" />
    <AiInvocations :target="target" />
  </div>
</template>
<script setup lang="ts">
import type { InspectorTarget } from '../../inspectorTarget'
import SettingsTab from '../settings/SettingsTab.vue'
import AiInvocations from './AiInvocations.vue'
defineProps<{ target: InspectorTarget }>()
</script>
