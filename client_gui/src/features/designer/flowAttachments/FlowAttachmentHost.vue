<template>
  <FlowAttachmentWindow v-for="(win, i) in windows" :key="win.id" :ref="el => setWindow(win.id, el)"
    :flow-id="win.id" :project-id="win.projectId" :title="win.title" :position="i" :read-only="isReadOnly(win.id)"
    :current="win.id === flowId" :focused="win.id === focusedId" :z-index="win.z"
    @focus="focus(win.id)" @close="close(win.id)" />
  <Teleport to="body">
    <div v-if="dropping" class="fa-drop-overlay" data-test="flow-attachment-drop-overlay" aria-live="polite">
      <div class="fa-drop-card">
        <span class="fa-drop-symbol"><FaIcon name="upload" :size="31" /></span>
        <strong>{{ w.dropTitle }}</strong>
        <div class="fa-drop-target"><FaIcon name="flow" :size="17" /><b>{{ title || flowId }}</b></div>
      </div>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
// flowgate.v02gui.0030 — the Flow attachment windows (one per Flow) and the full-screen file drop of the final
// mockup: a file dropped anywhere on the Designer goes to the Flow that is open now. While any window is open,
// opening another Flow opens that Flow's window too; a Flow that already has one brings it to the front.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { allocateOverlayZIndex } from '@/core/overlay/zIndex'
import FlowAttachmentWindow from './FlowAttachmentWindow.vue'
import { FaIcon } from './icons'
import { connectReadOnly } from './operations'
import { flowAttachmentUi } from './state'
import { wordsFor } from './words'

// isArchived (flowgate.v02test.0036 T#1): the Designer's definite archive state of a Flow (null = not known yet). While
// this Host is mounted the Flow work manager also follows it (a fast path: the Designer already reads this state), so a
// Flow archived while its window is closed drops its unsent deletes at once. The manager does not depend on it: it
// watches the archive state of every Project with pending work itself, for its own lifetime (after this Host unmounts
// or the Designer moves to another Project too). isReadOnly also locks an unknown state, so it is not used for dropping work.
const props = defineProps<{ flowId: string | null; projectId: string | null; title: string; isReadOnly: (id: string) => boolean; isArchived?: (id: string) => boolean | null }>()
const { locale } = useI18n()
const w = computed(() => wordsFor(locale.value))
const windows = ref<{ id: string; projectId: string; title: string; z: number }[]>([])
const focusedId = ref<string | null>(null)
const refs = new Map<string, InstanceType<typeof FlowAttachmentWindow>>()
function setWindow(id: string, el: unknown) { if (el) refs.set(id, el as InstanceType<typeof FlowAttachmentWindow>); else refs.delete(id) }

function focus(id: string) {
  const win = windows.value.find(x => x.id === id)
  if (!win) return
  focusedId.value = id
  win.z = allocateOverlayZIndex()
}
async function open(files: File[] = []) {
  const id = props.flowId, projectId = props.projectId
  if (!id || !projectId) return
  const existing = windows.value.find(x => x.id === id)
  if (existing) existing.title = props.title || id
  else windows.value.push({ id, projectId, title: props.title || id, z: allocateOverlayZIndex() })
  focus(id)
  await nextTick()
  const view = refs.get(id)
  view?.reveal()
  if (files.length) void view?.add(files)
  else if (existing) void view?.reload()
}
function close(id: string) {
  windows.value = windows.value.filter(x => x.id !== id)
  if (focusedId.value === id) focusedId.value = windows.value.at(-1)?.id ?? null
}

watch(() => props.flowId, (id, before) => {
  if (!id || id === before) return
  if (windows.value.some(x => x.id === id)) focus(id)
  else if (windows.value.length) void open()
})
watch(() => props.title, title => {
  const win = windows.value.find(x => x.id === props.flowId)
  if (win && title) win.title = title
})
watch(() => !!props.flowId && windows.value.some(x => x.id === props.flowId), open => { flowAttachmentUi.currentOpen = open }, { immediate: true })

/* ── full-screen drop: capture phase, so the canvas, tabs, side panels, chat and the windows all send the
   files to the same place — the open Flow. ── */
const dropping = ref(false)
const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
const canDrop = () => !!props.flowId && !!props.projectId && !props.isReadOnly(props.flowId)
function onDragOver(event: DragEvent) {
  if (!hasFiles(event) || !canDrop()) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy'
  dropping.value = true
}
function onDragLeave(event: DragEvent) {
  if (!hasFiles(event)) return
  if (event.relatedTarget === null && (event.clientX <= 0 || event.clientY <= 0 || event.clientX >= innerWidth - 1 || event.clientY >= innerHeight - 1)) dropping.value = false
}
function onDrop(event: DragEvent) {
  if (!hasFiles(event)) return
  dropping.value = false
  if (!canDrop()) return
  event.preventDefault(); event.stopPropagation()
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length) void open(files)
}
function hide() { dropping.value = false }
const disconnectReadOnly = connectReadOnly((projectId, flowId) => projectId === props.projectId ? props.isArchived?.(flowId) ?? null : null)
onMounted(() => {
  document.addEventListener('dragenter', onDragOver, true)
  document.addEventListener('dragover', onDragOver, true)
  document.addEventListener('dragleave', onDragLeave, true)
  document.addEventListener('drop', onDrop, true)
  document.addEventListener('dragend', hide, true)
  window.addEventListener('blur', hide)
})
onBeforeUnmount(() => {
  document.removeEventListener('dragenter', onDragOver, true)
  document.removeEventListener('dragover', onDragOver, true)
  document.removeEventListener('dragleave', onDragLeave, true)
  document.removeEventListener('drop', onDrop, true)
  document.removeEventListener('dragend', hide, true)
  window.removeEventListener('blur', hide)
  disconnectReadOnly()
  flowAttachmentUi.currentOpen = false
})
defineExpose({ open })
</script>

<style scoped>
.fa-drop-overlay { position: fixed; inset: 0; z-index: 2147483000; display: grid; place-items: center; box-sizing: border-box;
  background: rgba(5, 17, 31, .73); border: 4px dashed #4dacff; backdrop-filter: blur(3px); }
.fa-drop-card { width: min(540px, calc(100vw - 48px)); box-sizing: border-box; padding: 38px 36px; display: flex; flex-direction: column; align-items: center;
  gap: 14px; text-align: center; border: 1px solid #5aaff0; border-radius: 14px; background: #102c47; box-shadow: 0 16px 60px #000c; color: #eaf6ff; pointer-events: none; }
.fa-drop-symbol { width: 60px; height: 60px; display: grid; place-items: center; border: 1px solid #358bd4; border-radius: 16px; background: #184a76; color: #8cdaff; }
.fa-drop-card strong { font-size: 20px; }
.fa-drop-target { display: flex; align-items: center; gap: 8px; padding: 10px 16px; border: 1px solid #3f8fc9; border-radius: 8px; background: #153d60; color: #dbf0ff; font-size: 13px; }
.fa-drop-target :deep(.fa-icon) { color: #63d5c1; }
</style>
