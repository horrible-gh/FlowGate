<template>
  <Teleport to="body">
    <section ref="root" class="fa-window" :class="{ focused, 'not-current': !current }" :style="placeStyle"
      tabindex="-1" role="dialog" :aria-label="`${w.title} · ${title}`" data-test="flow-attachment-window" :data-flow-id="flowId"
      @pointerdown="emit('focus')" @keydown="keyDown">
      <header class="fa-head" @pointerdown="startDrag">
        <span class="fa-head-square"><FaIcon name="clip" /></span>
        <span class="fa-title">{{ w.title }}</span>
        <span class="fa-flow" :title="title">{{ title }}</span>
        <span class="fa-spacer" />
        <button type="button" class="fa-close" :title="w.close" :aria-label="w.close" data-test="flow-attachment-close"
          @pointerdown.stop @click="close"><FaIcon name="close" :size="13" /></button>
      </header>

      <div class="fa-tools">
        <label class="fa-search"><FaIcon name="search" :size="13" />
          <input v-model="search" :placeholder="w.search" :aria-label="w.search" autocomplete="off" data-test="flow-attachment-search" />
        </label>
        <button type="button" class="fa-add" :disabled="readOnly" data-test="flow-attachment-add" @click="input?.click()">
          <FaIcon name="plus" :size="13" />{{ w.add }}
        </button>
        <input ref="input" type="file" multiple hidden @change="pick" />
      </div>
      <div class="fa-count"><b data-test="flow-attachment-count">{{ w.count(visibleLive.length) }}</b></div>

      <div class="fa-selection">
        <label class="fa-select-all">
          <input type="checkbox" class="fa-box" :checked="allSelected" :indeterminate="someSelected" :disabled="!visibleLive.length"
            :aria-label="w.allLabel" data-test="flow-attachment-select-all" @change="selectAll" />{{ w.all }}
        </label>
        <span class="fa-selected">{{ w.selected(visibleSelected.length) }}</span>
        <div class="fa-selection-actions">
          <button type="button" :disabled="!visibleSelected.length || zipping" :title="w.zipTitle" data-test="flow-attachment-zip" @click="zip">
            {{ zipping ? w.preparing : w.zip }}
          </button>
          <button v-if="bulk && !visibleSelected.length" type="button" class="fa-icon-btn undo" :title="w.undoMany(bulk.items.length)"
            :aria-label="w.undoMany(bulk.items.length)" data-test="flow-attachment-bulk-undo" @click="undoBulk">
            <svg class="fa-ring" viewBox="0 0 30 28" preserveAspectRatio="none" aria-hidden="true">
              <rect x="1" y="1" width="28" height="26" rx="6" pathLength="110" :style="ringDelay(bulk.start)" />
            </svg>
            <FaIcon name="undo" :size="13" /><span class="fa-undo-badge">{{ bulk.items.length }}</span>
          </button>
          <button v-else type="button" class="fa-icon-btn delete" :disabled="readOnly || !visibleSelected.length" :title="w.removeSelected"
            :aria-label="w.removeSelected" data-test="flow-attachment-bulk-delete" @click="removeSelected">
            <FaIcon name="trash" :size="13" />
          </button>
        </div>
      </div>

      <div v-if="errorLines.length" class="fa-errors" data-test="flow-attachment-errors">
        <p v-for="line in errorLines" :key="line.key" class="fa-error" role="alert" data-test="flow-attachment-error">
          <span class="fa-error-text">{{ line.text }}</span>
          <button type="button" class="fa-error-close" :title="w.closeError" :aria-label="w.closeError" data-test="flow-attachment-error-close"
            @click="dismiss(line)"><FaIcon name="close" :size="11" /></button>
        </p>
      </div>

      <p v-if="flow.currentFile" class="fa-count" role="status" data-test="flow-attachment-progress">
        {{ w.uploadProgress(flow.currentFile.name, flow.pendingCount) }}
      </p>

      <div class="fa-list" data-test="flow-attachment-list">
        <AttachmentRequestItem v-for="request in requests" :key="request.id" :request="request" :disabled="readOnly" @decided="reload" />
        <div v-for="file in visible" :key="file.key" class="fa-row" data-test="flow-attachment-row" :data-key="file.key"
          :class="{ selected: selected.has(file.key), gone: isGone(file) }">
          <label class="fa-row-select">
            <input type="checkbox" class="fa-box" :checked="selected.has(file.key)" :disabled="isGone(file)"
              :aria-label="w.pick(file.display_name)" @change="toggle(file)" />
          </label>
          <span class="fa-glyph" :class="isImage(file) ? 'image' : 'text'"><FaIcon :name="isImage(file) ? 'image' : 'file'" /></span>
          <div class="fa-meta" @click="toggle(file)">
            <div class="fa-name" :title="file.display_name">{{ file.display_name }}</div>
            <div class="fa-source"><span class="fa-tag" :title="file.source_title">{{ originLabel(file) }}</span><span>{{ when(file.uploaded_at) }}</span></div>
          </div>
          <span class="fa-size">{{ humanSize(file.size) }}</span>
          <div class="fa-actions">
            <button type="button" class="fa-action" :title="w.download" :aria-label="w.download" data-test="flow-attachment-download"
              :disabled="isGone(file)" @click="download(file)"><FaIcon name="download" :size="13" /></button>
            <button v-if="isGone(file)" type="button" class="fa-action undo" :title="w.undo" :aria-label="w.undo" data-test="flow-attachment-undo"
              @click="undoItem(file.key)">
              <svg class="fa-ring" viewBox="0 0 30 28" preserveAspectRatio="none" aria-hidden="true">
                <rect x="1" y="1" width="28" height="26" rx="6" pathLength="110" :style="ringDelay(pendingStart(file.key))" />
              </svg>
              <FaIcon name="undo" :size="13" />
            </button>
            <button v-else type="button" class="fa-action del" :title="w.remove" :aria-label="w.remove" data-test="flow-attachment-delete"
              :disabled="readOnly" @click="remove([file])"><FaIcon name="trash" :size="13" /></button>
          </div>
        </div>
        <div v-if="!visible.length && !requests.length" class="fa-empty">
          <FaIcon name="folder" :size="28" /><strong>{{ flow.loading && !flow.loaded ? w.loading : search ? w.noResult : w.empty }}</strong>
        </div>
      </div>

      <div v-if="collision" class="fa-conflict" data-test="flow-attachment-conflict">
        <div class="fa-conflict-card">
          <h3>{{ w.conflictTitle }}</h3>
          <p :title="title">{{ collision.file.name }}</p>
          <div class="fa-conflict-actions">
            <button type="button" data-conflict="cancel" @click="resolveCollision('cancel')">{{ w.cancel }}</button>
            <button type="button" data-conflict="keep_both" @click="resolveCollision('keep_both')">{{ w.keep }}</button>
            <button type="button" data-conflict="overwrite" class="replace" @click="resolveCollision('overwrite')">{{ w.overwrite }}</button>
          </div>
        </div>
      </div>
    </section>
  </Teleport>
</template>

<script setup lang="ts">
// one floating attachment window per Flow, drawn after the final mockup
// (mirageglass rph76zwu): header with the Flow chip, search + Add file, count, selection bar
// (ZIP / 🗑 or the numbered ↶), rows with a type tile, source tag and time, and an in-window name conflict card.
// (R1): the delete queue, the file list and its error live in the Flow work manager
// (operations.ts), not here. This window draws them and forwards the presses; closing or unmounting it only detaches
// the view — a staged delete still goes out when its five seconds run out, and a window opened again within them shows
// the row struck through with ↶ and the ring continuing from the time left.
import { computed, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useInspectorEvents } from '../inspector/useInspectorResource'
import { downloadFile, downloadZip, type FlowFile } from './api'
import * as ops from './operations'
import { UNDO_MS } from './undo'
import { wordsFor } from './words'
import { FaIcon } from './icons'
import AttachmentRequestItem from '@/features/attachmentRequests/AttachmentRequestItem.vue'
import { toAttachmentRequest, type AttachmentRequest } from '@/features/document'
import { getRequest } from '@shared/api'

const props = defineProps<{
  flowId: string; projectId: string; title: string; position: number; readOnly: boolean
  current: boolean; focused: boolean; zIndex: number
}>()
const emit = defineEmits<{ close: []; focus: [] }>()
const { locale } = useI18n()
const w = computed(() => wordsFor(locale.value))

/* ── place: dragged by the header, resized by the corner (CSS resize) ── */
const root = ref<HTMLElement | null>(null)
const WIDTH = 560, HEIGHT = 455
const place = reactive({ x: 0, y: 0 })
// R5: the ring's duration is UNDO_MS itself (one number for the timer and the border).
const placeStyle = computed(() => ({ left: `${place.x}px`, top: `${place.y}px`, zIndex: props.zIndex, '--fa-undo-ms': `${UNDO_MS}ms` }))
function initialPlace() {
  const x = Math.max(245, Math.round(innerWidth * 0.28)) + props.position * 185
  const y = Math.max(140, Math.round(innerHeight * 0.21)) + props.position * 60
  place.x = Math.min(x, Math.max(10, innerWidth - WIDTH - 15))
  place.y = Math.min(y, Math.max(8, innerHeight - HEIGHT - 15))
}
initialPlace()
onMounted(() => {
  const el = root.value
  if (!el) return
  el.style.width = `${Math.min(WIDTH, innerWidth - 25)}px`
  el.style.height = `${Math.min(HEIGHT, innerHeight - 25)}px`
})
let drag: { sx: number; sy: number; x: number; y: number } | null = null
function startDrag(event: PointerEvent) {
  if ((event.target as Element).closest('button')) return
  drag = { sx: event.clientX, sy: event.clientY, x: place.x, y: place.y }
  document.addEventListener('pointermove', moveDrag)
  document.addEventListener('pointerup', endDrag)
}
function moveDrag(event: PointerEvent) {
  if (!drag || !root.value) return
  place.x = Math.max(0, Math.min(innerWidth - root.value.offsetWidth, drag.x + event.clientX - drag.sx))
  place.y = Math.max(0, Math.min(innerHeight - 60, drag.y + event.clientY - drag.sy))
}
function endDrag() {
  drag = null
  document.removeEventListener('pointermove', moveDrag)
  document.removeEventListener('pointerup', endDrag)
}
function keepInView() {
  if (!root.value) return
  place.x = Math.min(place.x, Math.max(0, innerWidth - root.value.offsetWidth))
  place.y = Math.min(place.y, Math.max(0, innerHeight - 65))
}
function reveal() { root.value?.focus({ preventScroll: true }) }
function setGeometry(rect: { x: number; y: number; width: number; height: number }) {
  place.x = rect.x; place.y = rect.y
  if (root.value) { root.value.style.width = `${rect.width}px`; root.value.style.height = `${rect.height}px` }
}

/* ── files: the Flow's state in the work manager ── */
const scope: ops.FlowScope = Object.freeze({ projectId: props.projectId, flowId: props.flowId })
const viewId = `window-${Math.random().toString(36).slice(2)}`
const flow = shallowRef(ops.attachView(scope, viewId))
// A new session (sign-out, another user) is a new state; the window follows it.
watch(ops.sessionGeneration, () => { flow.value = ops.attachView(scope, viewId) })
const requests = ref<AttachmentRequest[]>([])
const selected = ref(new Set<string>())
const search = ref(''), zipping = ref(false)
const input = ref<HTMLInputElement | null>(null)
let alive = true, requestSequence = 0
// The failures kept with the Flow (R3), newest first. Text is built here so a reopened window uses its current language:
// what failed, then the server's message or the generic one. The "cancelled files" note is one more line.
interface ErrorLine { key: string; text: string; id: number | null }
const named = (names: string[]) => names.length > 3 ? `${names.slice(0, 3).join(', ')} ${w.value.more(names.length - 3)}` : names.join(', ')
const errorLines = computed<ErrorLine[]>(() => {
  const state = flow.value
  const lines: (ErrorLine & { order: number })[] = [...state.errors, ...(state.readError ? [state.readError] : [])].map(e => ({
    key: `e${e.id}`, id: e.id, order: e.order, text: `${w.value.errorLead[e.kind](named(e.targets))} — ${e.text || w.value.failed}`,
  }))
  if (state.cancelledUploads.length) lines.push({ key: 'cancelled', id: null, order: state.cancelledOrder, text: w.value.cancelledNames(named(state.cancelledUploads)) })
  return lines.sort((a, b) => b.order - a.order)
})
function dismiss(line: ErrorLine) {
  if (line.id === null) ops.clearCancelled(scope); else ops.clearError(scope, line.id)
}
async function readRequests() {
  const seq = ++requestSequence
  try {
    const approvals = await getRequest<{ requests: unknown[] }>(`/api/v1/flows/${encodeURIComponent(props.flowId)}/attachment-requests`)
    if (!alive || seq !== requestSequence) return
    requests.value = (approvals.data.requests ?? []).flatMap(r => toAttachmentRequest(r) ?? [])
  } catch (e) {
    if (alive && seq === requestSequence) ops.readFailed(scope, e)
  }
}
async function reload() { await Promise.all([ops.refresh(scope), readRequests()]) }
// A selection keeps only files the list still has.
watch(() => flow.value.files, data => { selected.value = new Set([...selected.value].filter(key => data.some(f => f.key === key))) })

const pendingStart = (key: string) => { void flow.value.revision; return ops.pendingGroup(scope, key)?.start }
const isGone = (file: FlowFile) => { void flow.value.revision; return !!ops.pendingGroup(scope, file.key) }
// A file whose five seconds ran out leaves the list while its delete is on the way; a failure brings it back.
// A staged file the latest read no longer has keeps its row (its snapshot) until it is sent or taken back.
const shown = computed(() => {
  const state = flow.value
  void state.revision
  const listed = state.files.filter(f => !state.deleting.has(f.key))
  const missing = ops.pendingFiles(scope).filter(p => !state.files.some(f => f.key === p.key) && !state.deleting.has(p.key))
  return [...listed, ...missing]
})
const visible = computed(() => {
  const q = search.value.trim().toLocaleLowerCase()
  if (!q) return shown.value
  return shown.value.filter(f => f.display_name.toLocaleLowerCase().includes(q) || originLabel(f).toLocaleLowerCase().includes(q))
})
// Keep hidden selections, but scope counts and bulk actions to visible, actionable files.
const visibleLive = computed(() => visible.value.filter(f => !isGone(f)))
const visibleSelected = computed(() => visibleLive.value.filter(f => selected.value.has(f.key)))
const bulk = computed(() => { void flow.value.revision; return ops.bulkGroup(scope) })
const allSelected = computed(() => visibleLive.value.length > 0 && visibleSelected.value.length === visibleLive.value.length)
const someSelected = computed(() => visibleSelected.value.length > 0 && !allSelected.value)
function selectAll() {
  const next = new Set(selected.value)
  const deselect = allSelected.value
  for (const file of visibleLive.value) {
    if (deselect) next.delete(file.key); else next.add(file.key)
  }
  selected.value = next
}
function toggle(file: FlowFile) {
  if (isGone(file)) return
  const next = new Set(selected.value)
  if (next.has(file.key)) next.delete(file.key); else next.add(file.key)
  selected.value = next
}
function remove(items: FlowFile[]) {
  if (props.readOnly) return
  const next = new Set(selected.value)
  for (const f of items) next.delete(f.key)
  selected.value = next
  ops.stageDelete(scope, items)
  keepFocus()
}
// The pressed 🗑 / ↶ is replaced by the other icon; focus stays in the window so Ctrl+Z still reaches it.
function keepFocus() { root.value?.focus({ preventScroll: true }) }
function undoItem(key: string) { ops.undoItem(scope, key); keepFocus() }
function removeSelected() { remove(visibleSelected.value) }
function undoBulk() { ops.undoGroup(scope); keepFocus() }
function ringDelay(start: number | undefined) {
  return { animationDelay: `-${Math.min(UNDO_MS, Math.max(0, Date.now() - (start ?? Date.now())))}ms` }
}
// Ctrl+Z reaches only this (focused) window, and only this window's Flow queue; a search box keeps its own undo.
function keyDown(event: KeyboardEvent) {
  if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 'z' || event.shiftKey) return
  if ((event.target as HTMLElement).closest('input:not([type=checkbox]), textarea, [contenteditable="true"]')) return
  if (ops.undoLast(scope)) { event.preventDefault(); event.stopPropagation() }
}
/** Ctrl+Z from the host while this window is the focused one. */
function undoLast() { return ops.undoLast(scope) }

async function download(file: FlowFile) {
  const started = ops.beginAction()
  try { await downloadFile(props.flowId, file); ops.actionSucceeded(scope, started) } catch (e) { ops.actionFailed(scope, 'download', e, [file.display_name]) }
}
async function zip() {
  const chosen = visibleSelected.value
  if (!chosen.length) return
  zipping.value = true
  const started = ops.beginAction()
  try { await downloadZip(props.flowId, chosen.map(f => f.key)); ops.actionSucceeded(scope, started) } catch (e) { ops.actionFailed(scope, 'download', e, chosen.map(f => f.display_name)) } finally { zipping.value = false }
}

/* Upload state and collision resolvers belong to the manager. */
const collision = computed(() => flow.value.collision)
function resolveCollision(choice: 'cancel' | 'keep_both' | 'overwrite') {
  if (collision.value) ops.answerCollision(collision.value.id, choice)
}
watch(() => collision.value?.id, id => { if (id) emit('focus') })
function add(incoming: File[]) {
  if (!props.readOnly) ops.enqueueUpload(scope, incoming)
}
function pick(event: Event) { const el = event.target as HTMLInputElement; void add(Array.from(el.files ?? [])); el.value = '' }
// Closing is not "undo all": the staged deletes keep counting in the work manager.
function close() { ops.detachView(scope, viewId); emit('close') }

/* ── row text ── */
const isImage = (file: FlowFile) => (file.content_type ?? '').startsWith('image/') || /\.(png|jpe?g|gif|webp|svg|bmp)$/i.test(file.display_name)
function originLabel(file: FlowFile) {
  const known = w.value.origin
  return known[file.origin ?? 'card'] ?? known.card
}
function humanSize(size: number) {
  return size >= 1048576 ? `${(size / 1048576).toFixed(1)} MB` : size >= 1024 ? `${Math.round(size / 1024)} KB` : `${size} B`
}
function when(value: string | null | undefined) {
  if (!value) return ''
  const at = new Date(value)
  if (Number.isNaN(at.getTime())) return ''
  const now = new Date()
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  const diff = Math.round((day(now) - day(at)) / 86400000)
  if (diff === 0) return `${String(at.getHours()).padStart(2, '0')}:${String(at.getMinutes()).padStart(2, '0')}`
  if (diff === 1) return w.value.yesterday
  return `${at.getMonth() + 1}/${at.getDate()}`
}

// Read-only drops this Flow's unsent deletes in the manager (the manager also hears the archive state itself, so a
// closed window is not needed for that); writable again brings nothing back. A window that opens read-only does not
// drop anything by itself: an unknown archive state also shows as read-only until it is read.
watch(() => props.readOnly, (locked, before) => {
  if (before === undefined) { if (!locked) ops.setReadOnly(scope, false); return }
  ops.setReadOnly(scope, locked)
}, { immediate: true })
useInspectorEvents(() => ({ projectId: props.projectId, flowId: props.flowId, docId: null }), {
  events: ['attachment_request_updated', 'flow_attachments_updated'], matches: (frame, scope) => frame.groupId === scope.flowId, changed: () => { void reload() },
})
void reload()
addEventListener('resize', keepInView)
onBeforeUnmount(() => {
  alive = false; ops.detachView(scope, viewId); endDrag()
  removeEventListener('resize', keepInView)
})
defineExpose({ add, reload, reveal, setGeometry, undoLast })
</script>

<style scoped>
.fa-window { position: fixed; min-width: 430px; min-height: 310px; max-width: calc(100vw - 18px); max-height: calc(100vh - 12px);
  display: flex; flex-direction: column; overflow: hidden; resize: both; box-sizing: border-box; outline: none;
  border: 1px solid #4a88bf; border-radius: 9px; background: #0d2033; color: #d8e8f5; font-size: 12px;
  box-shadow: 0 18px 58px rgba(0, 0, 0, .62), 0 0 0 1px rgba(12, 33, 49, .6); transition: box-shadow .18s; }
.fa-window.focused { border-color: #66b6fc; box-shadow: 0 21px 65px rgba(0, 0, 0, .75), 0 0 0 1px #265a85; }
.fa-window :deep(.fa-icon) { flex-shrink: 0; }
.fa-window button { font: inherit; cursor: pointer; }
.fa-window button:disabled { cursor: default; opacity: .45; }
.fa-window button:focus-visible, .fa-window input:focus-visible { outline: 2px solid #66b6fc; outline-offset: 1px; }

/* The header stays above the in-window conflict card (z-index 5): × must work while a question is open. */
.fa-head { position: relative; z-index: 6; height: 45px; flex-shrink: 0; display: flex; align-items: center; gap: 9px; padding: 0 11px; user-select: none; cursor: grab;
  background: linear-gradient(180deg, #18334e, #10273e); border-bottom: 1px solid #355876; }
.fa-head:active { cursor: grabbing; }
.fa-head-square { display: grid; place-items: center; width: 27px; height: 27px; border-radius: 6px; background: #20639e; color: #dcf1ff; }
.fa-title { font-size: 13px; font-weight: 750; color: #f0f7ff; white-space: nowrap; }
.fa-flow { max-width: 195px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 10px; padding: 3px 8px;
  border: 1px solid #3980b9; border-radius: 13px; background: #163f63; color: #c5e9ff; }
.not-current .fa-flow { border-color: #37566f; background: #1b3246; color: #b9d1e4; }
.fa-spacer { flex: 1; }
.fa-close { width: 27px; height: 25px; display: grid; place-items: center; padding: 0; border: 1px solid #345977; border-radius: 5px; background: #142e47; color: #bad7ed; }
.fa-close:hover { background: #60313e; border-color: #aa5862; color: #fff; }

.fa-tools { display: flex; align-items: center; gap: 7px; padding: 10px 12px 8px; flex-shrink: 0; }
.fa-search { flex: 1; min-width: 80px; height: 31px; box-sizing: border-box; display: flex; align-items: center; gap: 7px; padding: 0 9px;
  border: 1px solid #355471; border-radius: 5px; background: #0b1b2c; color: #6e91ad; }
.fa-search input { width: 100%; min-width: 0; border: 0; outline: none; background: none; color: #deeffc; font: inherit; font-size: 11px; }
.fa-search input::placeholder { color: #6686a2; }
.fa-add { height: 31px; display: flex; align-items: center; gap: 6px; padding: 0 11px; white-space: nowrap; font-weight: 650;
  border: 1px solid #3189e1; border-radius: 5px; background: linear-gradient(#287ad0, #1765bc); color: #fff; }
.fa-add:hover:not(:disabled) { background: #328be4; }
.fa-count { padding: 0 12px 8px; color: #7596b3; font-size: 10px; flex-shrink: 0; }
.fa-count b { color: #bcdef3; }

.fa-selection { height: 40px; flex-shrink: 0; display: flex; align-items: center; gap: 8px; padding: 0 12px; color: #c3def0;
  background: #10263c; border-top: 1px solid #2b455f; border-bottom: 1px solid #304f69; }
.fa-select-all { display: inline-flex; align-items: center; gap: 7px; cursor: pointer; white-space: nowrap; font-size: 11px; font-weight: 650; }
.fa-box { width: 15px; height: 15px; margin: 0; flex: none; accent-color: #379cf5; cursor: pointer; }
.fa-selected { font-size: 10px; color: #86a9c3; white-space: nowrap; }
.fa-selection-actions { margin-left: auto; display: flex; align-items: center; gap: 5px; }
.fa-selection-actions button { padding: 5px 8px; font-size: 10px; white-space: nowrap; border: 1px solid #386282; border-radius: 5px; background: #17354d; color: #c3dff3; }
.fa-selection-actions button:not(:disabled):hover { border-color: #559cd3; background: #234a70; }
.fa-selection-actions .fa-icon-btn { position: relative; width: 28px; height: 26px; padding: 0; display: grid; place-items: center; }
.fa-selection-actions .delete:not(:disabled) { background: #3b2535; color: #ffbdc2; border-color: #754657; }
.fa-selection-actions .delete:not(:disabled):hover { background: #5a3041; border-color: #ba6379; }

.fa-errors { flex-shrink: 0; max-height: 96px; overflow-y: auto; }
.fa-error { margin: 0; padding: 6px 8px 6px 12px; display: flex; align-items: flex-start; gap: 8px; font-size: 11px; color: #ffb3bc; background: #3a1f2b; border-bottom: 1px solid #6d3445; }
.fa-error-text { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.fa-error-close { flex: none; width: 20px; height: 20px; display: grid; place-items: center; padding: 0; border: 1px solid #7a4553; border-radius: 4px; background: transparent; color: #ffcdd3; }
.fa-error-close:hover { background: #5b2e3d; border-color: #b8646f; color: #fff; }

.fa-list { position: relative; flex: 1; min-height: 0; overflow-y: auto; background: #0c1d30; }
.fa-list::-webkit-scrollbar { width: 9px; }
.fa-list::-webkit-scrollbar-track { background: #14283e; }
.fa-list::-webkit-scrollbar-thumb { background: #3b6789; border: 2px solid #14283e; border-radius: 8px; }
.fa-row { min-height: 61px; box-sizing: border-box; display: flex; align-items: center; gap: 11px; padding: 8px 12px; border-bottom: 1px solid #203a54; }
.fa-row:hover { background: #153049; }
.fa-row.selected { background: #143855; }
.fa-row-select { display: flex; align-items: center; }
.fa-glyph { flex: 0 0 33px; width: 33px; height: 33px; box-sizing: border-box; display: grid; place-items: center;
  border: 1px solid #2b5273; border-radius: 7px; background: #18344f; color: #75b5e5; }
.fa-glyph.image { color: #74d6d5; background: #123f4b; border-color: #286a72; }
.fa-glyph.text { color: #b8a5ff; background: #292d55; border-color: #56548f; }
.fa-meta { flex: 1; min-width: 0; cursor: pointer; }
.fa-name { margin-bottom: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 650; color: #dbeaff; }
.fa-source { display: flex; align-items: center; gap: 7px; white-space: nowrap; font-size: 10px; color: #7698b3; }
.fa-tag { padding: 2px 6px; border-radius: 3px; background: #1a3e58; color: #9dcae9; }
.fa-size { min-width: 53px; text-align: right; font-size: 10px; color: #87a5be; }
.fa-actions { flex: 0 0 auto; display: flex; align-items: center; gap: 3px; }
.fa-action { position: relative; width: 26px; height: 26px; padding: 0; display: grid; place-items: center;
  border: 1px solid transparent; border-radius: 5px; background: transparent; color: #a5c4d9; }
.fa-action:hover:not(:disabled) { border-color: #477394; background: #203d57; color: #dff3ff; }
.fa-action.del:hover:not(:disabled) { color: #ffbac0; background: #472837; border-color: #94505a; }

.fa-row.gone, .fa-row.gone:hover { background: #0e1a27; }
.fa-row.gone .fa-name { color: #5f7489; text-decoration: line-through; }
.fa-row.gone .fa-glyph, .fa-row.gone .fa-source, .fa-row.gone .fa-size, .fa-row.gone .fa-row-select,
.fa-row.gone [data-test="flow-attachment-download"] { opacity: .3; pointer-events: none; }
.fa-window .undo, .fa-selection-actions .undo { position: relative; color: #5abaff; background: #0f2f4c; border-color: #1f3d58; opacity: 1; }
.fa-window .undo:hover { background: #174a78; color: #fff; border-color: #1f3d58; }
.fa-ring { position: absolute; inset: -1px; width: calc(100% + 2px); height: calc(100% + 2px); pointer-events: none; overflow: visible; }
.fa-ring rect { fill: none; stroke: #5abaff; stroke-width: 2; stroke-dasharray: 110; stroke-dashoffset: 0; animation: fa-ringdown var(--fa-undo-ms) linear forwards; }
@keyframes fa-ringdown { to { stroke-dashoffset: 110; } }
.fa-undo-badge { position: absolute; top: -6px; right: -6px; min-width: 14px; height: 14px; padding: 0 3px; box-sizing: border-box;
  border-radius: 8px; background: #5abaff; color: #04121f; font-size: 9px; font-weight: 800; line-height: 14px; text-align: center; }

.fa-empty { height: 100%; min-height: 120px; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 9px; padding: 30px; text-align: center; color: #7596b4; }

.fa-conflict { position: absolute; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(3, 15, 28, .8); }
.fa-conflict-card { width: min(385px, 100%); box-sizing: border-box; padding: 17px; border: 1px solid #426c90; border-radius: 8px; background: #142c45; box-shadow: 0 16px 44px #0009; }
.fa-conflict-card h3 { margin: 0 0 8px; font-size: 13px; color: #eaf6ff; }
.fa-conflict-card p { margin: 0 0 16px; font-size: 11px; line-height: 1.55; color: #9db9d1; overflow-wrap: anywhere; }
.fa-conflict-actions { display: flex; justify-content: flex-end; gap: 7px; }
.fa-conflict-actions button { padding: 7px 11px; border: 1px solid #426787; border-radius: 5px; background: #1a3c59; color: #dbeeff; }
.fa-conflict-actions .replace { background: #206dbd; border-color: #3898ea; color: #fff; }

@media (max-width: 650px) {
  .fa-selection { gap: 5px; padding: 0 8px; }
  .fa-row { padding: 7px 8px; gap: 7px; }
  .fa-size { display: none; }
}
</style>
