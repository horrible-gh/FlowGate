<template>
  <FlowAttachmentWindow v-for="(win, i) in windows" :key="win.id" :ref="el => setWindow(win.id, el)"
    :flow-id="win.id" :project-id="win.projectId" :title="win.title" :position="i" :read-only="isReadOnly(win.id)"
    :current="win.id === flowId" :focused="win.id === focusedId" :z-index="win.z"
    @focus="focus(win.id)" @close="close(win.id)" />
  <Teleport to="body">
    <div v-if="dropping && dropping.kind !== 'blocked'" class="fa-drop-overlay" data-test="flow-attachment-drop-overlay" aria-live="polite">
      <div class="fa-drop-card">
        <span class="fa-drop-symbol"><FaIcon name="upload" :size="31" /></span>
        <strong>{{ w.dropTitle }}</strong>
        <div class="fa-drop-target"><FaIcon name="flow" :size="17" /><b>{{ title || flowId }}</b></div>
        <small v-if="dropping.kind === 'pending'" class="fa-drop-note" data-test="flow-attachment-drop-checking">{{ w.dropChecking }}</small>
      </div>
    </div>
    <!-- 0074 TR0014: a file that cannot go anywhere says so before it is let go. pointer-events: none, so the
         element under the pointer (e.g. the chat input's image drop) still gets the drop. -->
    <div v-else-if="blockedReason" class="fa-drop-overlay blocked" data-test="flow-attachment-drop-blocked" :data-reason="blockedReason" aria-live="polite">
      <div class="fa-drop-card">
        <span class="fa-drop-symbol"><FaIcon name="close" :size="31" /></span>
        <strong>{{ w.dropBlocked(w.dropBlockReason[blockedReason]) }}</strong>
      </div>
    </div>
  </Teleport>
  <ToastHost :toasts="toasts" :dismiss-label="w.dismiss" @dismiss="dismissToast" />
</template>

<script setup lang="ts">
// the Flow attachment windows (one per Flow) and the full-screen file drop of the final
// mockup: a file dropped anywhere on the Designer goes to the Flow that is open now. While any window is open,
// opening another Flow opens that Flow's window too; a Flow that already has one brings it to the front.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { allocateOverlayZIndex } from '@/core/overlay/zIndex'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import FlowAttachmentWindow from './FlowAttachmentWindow.vue'
import { FaIcon } from './icons'
import { registerAttachmentFocus } from './focus'
import { connectReadOnly, enqueueUpload } from './operations'
import { flowAttachmentUi } from './state'
import { wordsFor, type DropBlock } from './words'

// isArchived: the Designer's definite archive state of a Flow (null = not known yet). While
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
  await openFor({ id, projectId, title: props.title || id }, files)
}
/** Opens (or brings to the front) the window of `target` and sends `files` to that Flow. A drop held while the archive
 *  state was being read (TR0014) lands here too, possibly after the user moved to another tab. */
async function openFor(target: DropTarget, files: File[] = []) {
  const { id, projectId } = target
  if (files.length && !props.isReadOnly(id)) enqueueUpload({ projectId, flowId: id }, files)
  const existing = windows.value.find(x => x.id === id)
  if (existing) existing.title = target.title
  else windows.value.push({ id, projectId, title: target.title, z: allocateOverlayZIndex() })
  focus(id)
  await nextTick()
  const view = refs.get(id)
  view?.reveal()
  if (!files.length && existing) void view?.reload()
}
// focusAttachment() (focus.ts, 0074 item 3): the open Flow's window opens (or comes to the front); a Flow that already
// has a window comes to the front. Any other Flow has no Project known here, so nothing opens and the answer is false.
async function focusFile(flowId: string, key: string): Promise<boolean> {
  if (flowId === props.flowId) await open()
  else if (windows.value.some(x => x.id === flowId)) { focus(flowId); await nextTick(); refs.get(flowId)?.reveal() }
  else return false
  const view = refs.get(flowId)
  return view ? view.focusFile(key) : false
}
function close(id: string) {
  windows.value = windows.value.filter(x => x.id !== id)
  if (focusedId.value === id) focusedId.value = windows.value.at(-1)?.id ?? null
}
/** Opens the open Flow's window, or closes it when it is already open. */
function toggle() {
  const id = props.flowId
  if (!id) return
  if (windows.value.some(x => x.id === id)) close(id)
  else void open()
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
   files to the same place — the open Flow.
   0074 TR0014: a dragged file never reaches the browser. dragover / drop are always prevented while the drag carries
   `Files`, whatever the state; otherwise a file let go in a "not accepting" moment (just after opening a Flow or
   switching tabs, while the archive state is still being read and isReadOnly() locks it) was opened by the browser.
   - ok: the open Flow takes the files (as before).
   - pending: the archive state is not known yet (isArchived = null, locked only for that reason) — the drop is held
     and goes up as soon as the state is known; an archived Flow gets a notice instead.
   - blocked: no open Flow, an archived Flow or a read-only one — "cannot upload here: <reason>" before the drop,
     dropEffect none, nothing goes up. The drop is not stopped there, so the element under it may still use it.
   Drags without `Files` (cards, quick messages, text) are not touched. ── */
interface DropTarget { id: string; projectId: string; title: string }
type DropState = { kind: 'ok' | 'pending' } | { kind: 'blocked'; reason: DropBlock }
const dropping = ref<DropState | null>(null)
const blockedReason = computed(() => dropping.value?.kind === 'blocked' ? dropping.value.reason : null)
const hasFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
function dropState(): DropState {
  const id = props.flowId
  if (!id || !props.projectId) return { kind: 'blocked', reason: 'noFlow' }
  const archived = props.isArchived?.(id)
  if (archived === true) return { kind: 'blocked', reason: 'archived' }
  if (!props.isReadOnly(id)) return { kind: 'ok' }
  // isReadOnly() also locks a state not read yet; only isArchived tells "not known" (null) from a definite lock.
  return archived === null ? { kind: 'pending' } : { kind: 'blocked', reason: 'readOnly' }
}
function onDragOver(event: DragEvent) {
  if (!hasFiles(event)) return
  event.preventDefault()
  const state = dropState()
  if (event.dataTransfer) event.dataTransfer.dropEffect = state.kind === 'blocked' ? 'none' : 'copy'
  dropping.value = state
}
function onDragLeave(event: DragEvent) {
  if (!hasFiles(event)) return
  if (event.relatedTarget === null && (event.clientX <= 0 || event.clientY <= 0 || event.clientX >= innerWidth - 1 || event.clientY >= innerHeight - 1)) dropping.value = null
}
function onDrop(event: DragEvent) {
  if (!hasFiles(event)) return
  dropping.value = null
  event.preventDefault()
  const state = dropState()
  if (state.kind === 'blocked') {
    if (state.reason !== 'noFlow') notify(state.reason)
    return
  }
  event.stopPropagation()
  const files = Array.from(event.dataTransfer?.files ?? [])
  const target = { id: props.flowId!, projectId: props.projectId!, title: props.title || props.flowId! }
  if (!files.length) return
  if (state.kind === 'ok') void openFor(target, files)
  else held.value = [...held.value, { target, files }]
}

/* Drops held until the archive state of their Flow is known (TR0014). */
const held = ref<{ target: DropTarget; files: File[] }[]>([])
function settleHeld() {
  if (!held.value.length) return
  const still: typeof held.value = []
  for (const item of held.value) {
    const { id, projectId } = item.target
    if (projectId !== props.projectId) continue // the Designer moved to another Project: its state is no longer read here
    const archived = props.isArchived?.(id) ?? null
    if (archived === true) notify('archived')
    else if (!props.isReadOnly(id)) void openFor(item.target, item.files)
    else if (archived === false) notify('readOnly')
    else still.push(item)
  }
  held.value = still
}
watch(() => held.value.map(item => `${item.target.id}:${props.isArchived?.(item.target.id) ?? null}:${props.isReadOnly(item.target.id)}`).join('|') + `@${props.projectId}`,
  settleHeld, { flush: 'sync' })

const toasts = ref<ToastHostItem[]>([])
let toastSeq = 0
function notify(reason: DropBlock) {
  toasts.value = [...toasts.value, { id: ++toastSeq, tone: 'warning', message: w.value.dropRefused(w.value.dropBlockReason[reason]) }]
}
function dismissToast(id: string | number) { toasts.value = toasts.value.filter(item => item.id !== id) }
function hide() { dropping.value = null }
const unregisterFocus = registerAttachmentFocus(focusFile)
const disconnectReadOnly = connectReadOnly((projectId, flowId) => projectId === props.projectId ? props.isArchived?.(flowId) ?? null : null)
onMounted(() => {
  flowAttachmentUi.toggle = toggle
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
  if (flowAttachmentUi.toggle === toggle) flowAttachmentUi.toggle = null
  unregisterFocus()
  flowAttachmentUi.currentOpen = false
})
defineExpose({ open, close, toggle })
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
.fa-drop-note { color: #a9cbe6; font-size: 12px; }
.fa-drop-overlay.blocked { pointer-events: none; background: rgba(24, 10, 14, .62); border-color: #e2707c; }
.fa-drop-overlay.blocked .fa-drop-card { border-color: #e2707c; background: #2c1820; }
.fa-drop-overlay.blocked .fa-drop-symbol { border-color: #c4505e; background: #4a1f29; color: #ffb3bb; }
</style>

