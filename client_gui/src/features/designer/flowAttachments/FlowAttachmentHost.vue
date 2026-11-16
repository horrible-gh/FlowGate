<template>
  <FlowAttachmentWindow v-for="(win, i) in windows" :key="win.id" :ref="el => setWindow(win.id, el)"
    :flow-id="win.id" :project-id="win.projectId" :title="win.title" :position="i" :read-only="isReadOnly(win.id)" @close="close(win.id)" @arrange="arrange" />
</template>
<script setup lang="ts">
import { nextTick, ref } from 'vue'
import FlowAttachmentWindow from './FlowAttachmentWindow.vue'
const props = defineProps<{ flowId: string | null; projectId: string | null; title: string; isReadOnly: (id: string) => boolean }>()
const windows = ref<{ id: string; projectId: string; title: string }[]>([])
const refs = new Map<string, InstanceType<typeof FlowAttachmentWindow>>()
function setWindow(id: string, el: unknown) { if (el) refs.set(id, el as InstanceType<typeof FlowAttachmentWindow>); else refs.delete(id) }
async function open(files: File[] = []) {
  const id = props.flowId, projectId = props.projectId
  if (!id || !projectId) return
  if (!windows.value.some(w => w.id === id)) windows.value.push({ id, projectId, title: props.title || id })
  await nextTick()
  refs.get(id)?.reveal()
  if (files.length) void refs.get(id)?.add(files)
  else void refs.get(id)?.reload()
}
function arrange() {
  const count = windows.value.length
  const columns = Math.min(count, Math.max(1, Math.floor(window.innerWidth / 420)))
  const rows = Math.ceil(count / columns)
  const width = Math.max(280, (window.innerWidth - 24) / columns - 12)
  const height = Math.max(260, Math.min(480, (window.innerHeight - 100) / rows - 12))
  windows.value.forEach((win, index) => refs.get(win.id)?.arrange({
    x: 12 + (index % columns) * (width + 12), y: 76 + Math.floor(index / columns) * (height + 12), width, height,
  }))
}
function close(id: string) { windows.value = windows.value.filter(w => w.id !== id) }
defineExpose({ open })
</script>
