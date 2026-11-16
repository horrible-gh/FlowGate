<template>
  <ModelessShell ref="shell" v-model:minimized="minimized" :open="true" :initial-x="60 + position * 36" :initial-y="160 + position * 28"
    :initial-width="680" :initial-height="430" :min-width="400" :min-height="260" :close-on-escape="false" @close="close">
    <template #header><span>{{ w.title }} <small class="flow-title">· {{ title }}</small></span></template>
    <template #actions><button :title="w.align" :aria-label="w.align" @mousedown.stop @click="emit('arrange')">▥</button></template>
    <section class="flow-files" tabindex="0" :class="{ dragging }" @keydown="keyDown"
      @dragover.prevent.stop="dragging = true" @dragleave.prevent="dragging = false" @drop.prevent.stop="drop">
      <div class="file-tools">
        <input v-model="search" type="search" :placeholder="w.search" :aria-label="w.search" />
        <button :disabled="readOnly || uploading" @click="input?.click()">＋ {{ w.add }}</button>
        <button :title="w.refresh" :aria-label="w.refresh" @click="reload">↻</button>
        <input ref="input" type="file" multiple hidden @change="pick" />
      </div>
      <div class="selection-tools">
        <label><input type="checkbox" :checked="allSelected" :disabled="!selectable.length" @change="selectAll" /> {{ w.all }}</label>
        <span>{{ selected.size }} / {{ files.length }}</span>
        <button :disabled="!selected.size" @click="zip">ZIP ↓</button>
        <button v-if="pending.size" class="icon undo" :title="w.undo" :aria-label="w.undo" @click="queue.clear()">↶<sup>{{ pending.size }}</sup></button>
        <button v-else class="icon" :title="w.remove" :aria-label="w.remove" :disabled="readOnly || !selected.size" @click="stageSelected"><TrashIcon /></button>
      </div>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <div v-if="collision" class="collision">
        <span>{{ collision.file.name }} · {{ w.conflict }}</span>
        <button @click="resolveCollision('cancel')">{{ w.cancel }}</button>
        <button @click="resolveCollision('keep_both')">{{ w.keep }}</button>
        <button @click="resolveCollision('overwrite')">{{ w.overwrite }}</button>
      </div>
      <div class="file-list">
        <AttachmentRequestItem v-for="request in requests" :key="request.id" :request="request" :disabled="readOnly" @decided="reload" />
        <div v-for="file in visible" :key="file.key" class="file-row" :class="{ pending: pending.has(file.key) }">
          <input type="checkbox" :aria-label="file.display_name" :checked="selected.has(file.key)" :disabled="pending.has(file.key) || deleting.has(file.key)" @change="toggle(file.key)" />
          <span class="filename" :title="file.display_name">{{ file.display_name }}</span>
          <span class="source" :title="file.source_title">{{ file.source_type }}</span>
          <span class="size">{{ size(file.size) }}</span>
          <button class="icon" :title="w.download" :aria-label="w.download" @click="download(file)">↓</button>
          <button v-if="pending.has(file.key)" class="icon undo" :title="w.undo" :aria-label="w.undo" @click="queue.undo(file.key)">
            ↶<svg class="countdown" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14" /></svg>
          </button>
          <button v-else class="icon" :title="w.remove" :aria-label="w.remove" :disabled="readOnly || deleting.has(file.key)" @click="stage([file])"><TrashIcon /></button>
        </div>
        <p v-if="!visible.length" class="empty">{{ loading ? w.loading : w.empty }}</p>
      </div>
      <div v-if="dragging" class="drop-hint">{{ w.drop }}</div>
    </section>
  </ModelessShell>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { useInspectorEvents } from '../inspector/useInspectorResource'
import { readFiles, uploadFile, deleteFile, downloadFile, downloadZip, type FlowFile } from './api'
import { createUndoQueue } from './undo'
import { words } from './words'
import AttachmentRequestItem from '@/features/attachmentRequests/AttachmentRequestItem.vue'
import { toAttachmentRequest, type AttachmentRequest } from '@/features/document/api/attachmentRequests'
import { getRequest } from '@shared/api'
const props = defineProps<{ flowId: string; projectId: string; title: string; position: number; readOnly: boolean }>()
const emit = defineEmits<{ close: []; arrange: [] }>()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const minimized = ref(false)
function reveal() { minimized.value = false; shell.value?.bringToFront() }
function arrange(rect: { x: number; y: number; width: number; height: number }) { reveal(); shell.value?.setGeometry(rect) }
const { locale } = useI18n()
const w = computed(() => words[locale.value as keyof typeof words] ?? words.en)
const TrashIcon = defineComponent({ setup: () => () => h('svg', { viewBox: '0 0 24 24', width: 16, height: 16, fill: 'none', stroke: 'currentColor', 'stroke-width': 1.6, 'aria-hidden': true }, [h('path', { d: 'M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7' })]) })
const files = ref<FlowFile[]>([]), selected = ref(new Set<string>()), deleting = ref(new Set<string>())
const search = ref(''), error = ref(''), loading = ref(false), uploading = ref(false), dragging = ref(false)
const input = ref<HTMLInputElement | null>(null)
const requests = ref<AttachmentRequest[]>([])
const tick = ref(0)
const pending = computed(() => { void tick.value; return new Map(queue.pending) })
let alive = true, readSequence = 0
const message = (e: unknown) => {
  const raw = e as { response?: { data?: { error?: { message?: string } } }; message?: string }
  return raw.response?.data?.error?.message || w.value.failed
}
async function reload() {
  const seq = ++readSequence; loading.value = true
  try {
    const [data, approvals] = await Promise.all([readFiles(props.flowId), getRequest<{ requests: unknown[] }>(`/api/v1/flows/${encodeURIComponent(props.flowId)}/attachment-requests`)])
    if (alive && seq === readSequence) {
      files.value = data
      requests.value = approvals.data.requests.flatMap(r => toAttachmentRequest(r) ?? [])
      selected.value = new Set([...selected.value].filter(key => data.some(f => f.key === key)))
    }
  }
  catch (e) { if (alive && seq === readSequence) error.value = message(e) }
  finally { if (alive && seq === readSequence) loading.value = false }
}
const queue = createUndoQueue<FlowFile>(async file => {
  if (props.readOnly) return
  deleting.value.add(file.key)
  try { await deleteFile(props.flowId, file); files.value = files.value.filter(f => f.key !== file.key); selected.value.delete(file.key) }
  catch (e) { error.value = message(e); await reload() }
  finally { deleting.value.delete(file.key) }
}, () => tick.value++)
const visible = computed(() => files.value.filter(f => f.display_name.toLocaleLowerCase().includes(search.value.toLocaleLowerCase())))
const selectable = computed(() => visible.value.filter(f => !pending.value.has(f.key) && !deleting.value.has(f.key)))
const allSelected = computed(() => selectable.value.length > 0 && selectable.value.every(f => selected.value.has(f.key)))
function selectAll() { selected.value = allSelected.value ? new Set() : new Set(selectable.value.map(f => f.key)) }
function toggle(key: string) { if (selected.value.has(key)) selected.value.delete(key); else selected.value.add(key) }
function stage(items: FlowFile[]) { if (!props.readOnly) queue.stage(items.filter(f => !deleting.value.has(f.key))) }
function stageSelected() { stage(files.value.filter(f => selected.value.has(f.key))); selected.value.clear() }
function keyDown(event: KeyboardEvent) {
  const target = event.target as HTMLElement
  if (target.matches('input, textarea, [contenteditable="true"]')) return
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'z' && pending.value.size) { event.preventDefault(); event.stopPropagation(); queue.undo() }
}
async function download(file: FlowFile) { try { await downloadFile(props.flowId, file) } catch (e) { error.value = message(e) } }
async function zip() { try { await downloadZip(props.flowId, [...selected.value]) } catch (e) { error.value = message(e) } }
const collision = ref<{ file: File; key: string; content_sha256: string } | null>(null)
let answer: ((choice: string) => void) | null = null
function resolveCollision(choice: string) { answer?.(choice); answer = null; collision.value = null }
const uploads: File[] = []
async function add(incoming: File[]) {
  if (props.readOnly) return
  uploads.push(...incoming)
  if (uploading.value) return
  uploading.value = true; error.value = ''
  try {
    while (uploads.length && alive && !props.readOnly) {
      const file = uploads.shift()!
      try { await uploadFile(props.flowId, file) }
      catch (e) {
        const raw = e as { response?: { data?: { error?: { code?: string; details?: { key: string; content_sha256: string } } } } }
        const conflict = raw.response?.data?.error
        if (conflict?.code !== 'ATTACHMENT_NAME_CONFLICT' || !conflict.details) { error.value = message(e); continue }
        const existing = conflict.details
        collision.value = { file, ...existing }
        const choice = await new Promise<string>(resolve => { answer = resolve })
        if (choice !== 'cancel' && alive && !props.readOnly) {
          try { await uploadFile(props.flowId, file, choice, existing) } catch (failure) { error.value = message(failure) }
        }
      }
      await reload()
    }
  } finally { uploading.value = false }
}
function pick(event: Event) { const el = event.target as HTMLInputElement; void add(Array.from(el.files ?? [])); el.value = '' }
function drop(event: DragEvent) { dragging.value = false; void add(Array.from(event.dataTransfer?.files ?? [])) }
function close() { queue.clear(); resolveCollision('cancel'); emit('close') }
function size(bytes: number) { return bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB` }
watch(() => props.readOnly, locked => { if (locked) { queue.clear(); resolveCollision('cancel'); uploads.length = 0 } })
useInspectorEvents(() => ({ projectId: props.projectId, flowId: props.flowId, docId: null }), {
  events: ['attachment_request_updated', 'flow_attachments_updated'], matches: (frame, scope) => frame.groupId === scope.flowId, changed: () => { void reload() },
})
void reload()
onBeforeUnmount(() => { alive = false; queue.clear(); resolveCollision('cancel'); uploads.length = 0 })
defineExpose({ add, reload, reveal, arrange })
</script>

<style scoped>
.flow-title { color: var(--fg2-muted); font-weight: 400; }
.flow-files { position: relative; display: flex; flex-direction: column; height: 100%; gap: 10px; color: var(--fg2-text); font-size: 12px; outline: none; }
.file-tools, .selection-tools { display: flex; align-items: center; gap: 8px; }
.file-tools input[type=search] { flex: 1; min-width: 80px; }
button, input[type=search] { color: var(--fg2-text); background: var(--fg2-panel); border: 1px solid var(--fg2-line-strong); border-radius: 5px; padding: 6px 9px; }
button { cursor: pointer; } button:disabled { opacity: .4; cursor: default; }
button:hover:not(:disabled) { background: #173551; } button:focus-visible { outline: 2px solid var(--fg2-focus); }
.selection-tools label { display: flex; gap: 5px; align-items: center; } .selection-tools > span { flex: 1; color: var(--fg2-muted); }
.file-list { overflow: auto; flex: 1; min-height: 0; border-top: 1px solid var(--fg2-line); }
.file-row { display: flex; align-items: center; gap: 8px; min-height: 43px; border-bottom: 1px solid var(--fg2-line); }
.filename { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.source { font-size: 10px; border: 1px solid var(--fg2-line-strong); border-radius: 4px; padding: 2px 5px; color: var(--fg2-muted); }
.size { color: var(--fg2-muted); width: 60px; text-align: right; font-variant-numeric: tabular-nums; }
.icon { position: relative; width: 32px; height: 32px; padding: 0; display: grid; place-items: center; flex-shrink: 0; font-size: 20px; }
.pending .filename { text-decoration: line-through; opacity: .45; } .pending .source, .pending .size { opacity: .45; }
.undo { color: #4bd9c7; } .undo sup { position: absolute; right: -3px; top: -5px; font-size: 10px; background: #174c54; border-radius: 8px; padding: 1px 4px; }
.countdown { position: absolute; inset: 0; width: 100%; height: 100%; transform: rotate(-90deg); pointer-events: none; }
.countdown circle { fill: none; stroke: currentColor; stroke-width: 2; stroke-dasharray: 88; animation: countdown 5s linear forwards; }
@keyframes countdown { to { stroke-dashoffset: 88; } }
.empty { text-align: center; padding: 40px; color: var(--fg2-muted); }
.error { margin: 0; color: #ff9ca8; } .collision { display: flex; flex-wrap: wrap; gap: 6px; padding: 10px; border: 1px solid #886d38; border-radius: 6px; } .collision span { width: 100%; }
.drop-hint { position: absolute; inset: 0; display: grid; place-items: center; background: #103654e8; border: 2px dashed #61b9ff; pointer-events: none; }
</style>
