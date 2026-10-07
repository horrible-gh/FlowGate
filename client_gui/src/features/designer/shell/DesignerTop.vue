<template>
  <header class="designer-top" data-test="designer-top">
    <DesignerHeader @command="emit('command', $event)" @search="emit('search', $event)" />
    <DesignerMenuBar
      :project-available="projectAvailable" :flow-available="flowAvailable" :has-selection="hasSelection"
      :running="running !== null" :left-collapsed="leftCollapsed" :right-collapsed="rightCollapsed"
      :left-mode="leftMode" :right-tool="rightTool" :density="density" @command="onCommand"
    />
    <DesignerToolbar :flow-available="flowAvailable" :running="running" :zoom="zoom" :density="density" @command="onCommand" />
  </header>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { DesignerLeftMode, DesignerRightTool } from './designerLayout'
import DesignerHeader from './DesignerHeader.vue'
import DesignerMenuBar from './DesignerMenuBar.vue'
import DesignerToolbar from './DesignerToolbar.vue'

defineProps<{
  projectAvailable: boolean; flowAvailable: boolean; hasSelection?: boolean
  leftCollapsed: boolean; rightCollapsed: boolean; leftMode: DesignerLeftMode; rightTool: DesignerRightTool
}>()
const emit = defineEmits<{ command: [id: string]; search: [payload: { scope: string; query: string }] }>()
const running = ref<'next' | 'all' | null>(null)
const zoom = ref(100)
const density = ref('normal')
function onCommand(id: string) {
  if (id === 'run.next') running.value = 'next'
  else if (id === 'run.all') running.value = 'all'
  else if (id === 'run.stop') running.value = null
  else if (id.startsWith('density.')) density.value = id.slice('density.'.length)
  else if (id === 'zoom.in') zoom.value = Math.min(400, zoom.value + 25)
  else if (id === 'zoom.out') zoom.value = Math.max(25, zoom.value - 25)
  else if (id === 'zoom.100' || id === 'zoom.fit') zoom.value = 100
  emit('command', id)
}
</script>

<style scoped>
.designer-top{display:flex;flex:none;min-width:0;flex-direction:column;background:var(--fg2-panel);color:var(--fg2-text)}
</style>
