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
        <Button variant="ghost" size="sm" class="fa-close" :title="w.close" :aria-label="w.close" data-test="flow-attachment-close"
          @pointerdown.stop @click="close"><FaIcon name="close" :size="13" /></Button>
      </header>

      <div class="fa-tools">
        <label class="fa-search"><FaIcon name="search" :size="13" />
          <input v-model="search" :placeholder="w.search" :aria-label="w.search" autocomplete="off" data-test="flow-attachment-search" />
        </label>
        <Button variant="primary" size="sm" class="fa-add" :disabled="readOnly" data-test="flow-attachment-add" @click="input?.click()">
          <FaIcon name="plus" :size="13" />{{ w.add }}
        </Button>
        <input ref="input" type="file" multiple hidden @change="pick" />
      </div>
      <!-- 0074 item 3: origin chips with counts (counts follow the search), the shown count and the order menu. -->
      <div class="fa-filters">
        <div class="fa-chips" role="group" :aria-label="w.chipsLabel">
          <Button v-for="chip in ORIGIN_FILTERS" :key="chip" variant="secondary" size="sm" class="fa-chip" :class="{ active: filter === chip }"
            :aria-pressed="filter === chip" :data-test="`flow-attachment-filter-${chip}`" @click="filter = chip">
            {{ w.chips[chip] }}<span class="fa-chip-count" data-test="flow-attachment-filter-count">{{ chipCounts[chip] }}</span>
          </Button>
        </div>
        <span class="fa-spacer" />
        <span class="fa-count"><b data-test="flow-attachment-count">{{ w.count(visibleLive.length) }}</b></span>
        <select v-model="sort" class="fa-sort" :aria-label="w.sortLabel" data-test="flow-attachment-sort">
          <option v-for="key in SORT_KEYS" :key="key" :value="key">{{ w.sorts[key] }}</option>
        </select>
      </div>

      <div class="fa-selection">
        <label class="fa-select-all">
          <input type="checkbox" class="fa-box" :checked="allSelected" :indeterminate="someSelected" :disabled="!visibleLive.length"
            :aria-label="w.allLabel" data-test="flow-attachment-select-all" @change="selectAll" />{{ w.all }}
        </label>
        <span class="fa-selected">{{ w.selected(visibleSelected.length) }}</span>
      </div>

      <div v-if="errorLines.length" class="fa-errors" data-test="flow-attachment-errors">
        <p v-for="line in errorLines" :key="line.key" class="fa-error" role="alert" data-test="flow-attachment-error">
          <span class="fa-error-text">{{ line.text }}</span>
          <Button variant="ghost" size="sm" class="fa-error-close" :title="w.closeError" :aria-label="w.closeError" data-test="flow-attachment-error-close"
            @click="dismiss(line)"><FaIcon name="close" :size="11" /></Button>
        </p>
      </div>

      <p v-if="flow.currentFile" class="fa-count fa-progress" role="status" data-test="flow-attachment-progress">
        {{ w.uploadProgress(flow.currentFile.name, flow.pendingCount) }}
      </p>

      <div class="fa-body">
        <div ref="list" class="fa-list" data-test="flow-attachment-list">
          <AttachmentRequestItem v-for="request in requests" :key="request.id" :request="request" :disabled="readOnly" @decided="reload" />
          <!-- 0074 item 4: the whole row selects (Shift = range over the shown rows); only its buttons do not. -->
          <div v-for="file in visible" :key="file.key" class="fa-row" data-test="flow-attachment-row" :data-key="file.key"
            :class="{ selected: selected.has(file.key), gone: isGone(file), flash: flashKey === file.key }"
            @mousedown="holdTextSelection" @click="rowClick(file, $event)">
            <span class="fa-row-select">
              <input type="checkbox" class="fa-box" :checked="selected.has(file.key)" :disabled="isGone(file)"
                :aria-label="w.pick(file.display_name)" @click.stop="boxClick($event)" @change="boxChange(file, $event)" />
            </span>
            <span class="fa-glyph" :class="isImage(file) ? 'image' : 'text'"><FaIcon :name="isImage(file) ? 'image' : 'file'" /></span>
            <div class="fa-meta">
              <div class="fa-name" :title="file.display_name">{{ file.display_name }}</div>
              <div class="fa-source"><span class="fa-tag" :title="file.source_title">{{ originLabel(file) }}</span><span>{{ when(file.uploaded_at) }}</span></div>
            </div>
            <span class="fa-size">{{ humanSize(file.size) }}</span>
            <div class="fa-actions">
              <Button variant="ghost" size="sm" class="fa-action" :title="w.download" :aria-label="w.download" data-test="flow-attachment-download"
                :disabled="isGone(file)" @click="download(file)"><FaIcon name="download" :size="13" /></Button>
              <Button v-if="isGone(file)" variant="ghost" size="sm" class="fa-action undo" :title="w.undo" :aria-label="w.undo" data-test="flow-attachment-undo"
                @click="undoItem(file.key)">
                <svg class="fa-ring" viewBox="0 0 30 28" preserveAspectRatio="none" aria-hidden="true">
                  <rect x="1" y="1" width="28" height="26" rx="6" pathLength="110" :style="ringDelay(pendingStart(file.key))" />
                </svg>
                <FaIcon name="undo" :size="13" />
              </Button>
              <Button v-else variant="ghost" size="sm" class="fa-action del" :title="w.remove" :aria-label="w.remove" data-test="flow-attachment-delete"
                :disabled="readOnly" @click="remove([file])"><FaIcon name="trash" :size="13" /></Button>
            </div>
          </div>
          <div v-if="!visible.length && !requests.length" class="fa-empty">
            <FaIcon name="folder" :size="28" /><strong>{{ flow.loading && !flow.loaded ? w.loading : search || filter !== 'all' ? w.noResult : w.empty }}</strong>
          </div>
        </div>

        <!-- 0074 item 5: comes up under the list while files are selected, or while a selection delete can be taken back
             (its numbered ↶ sits where [삭제] was). Kept in the DOM, closed = out of view and out of the tab order. -->
        <div class="fa-bar" :class="{ open: barOpen }" role="toolbar" :aria-label="w.barLabel" :aria-hidden="barOpen ? undefined : 'true'"
          data-test="flow-attachment-bar">
          <span class="fa-bar-count" data-test="flow-attachment-bar-count">{{ w.selected(visibleSelected.length) }}</span>
          <Button v-if="visibleSelected.length === 1" variant="secondary" size="sm" class="fa-bar-btn" :title="w.download"
            data-test="flow-attachment-download-selected" @click="download(visibleSelected[0])">
            <FaIcon name="download" :size="13" />{{ w.download }}
          </Button>
          <Button v-else variant="secondary" size="sm" class="fa-bar-btn" :disabled="!visibleSelected.length || zipping" :title="w.zipTitle"
            data-test="flow-attachment-zip" @click="zip">
            <FaIcon name="download" :size="13" />{{ zipping ? w.preparing : w.zip }}
          </Button>
          <Button v-if="bulk && !visibleSelected.length" variant="secondary" size="sm" class="fa-icon-btn undo" :title="w.undoMany(bulk.items.length)"
            :aria-label="w.undoMany(bulk.items.length)" data-test="flow-attachment-bulk-undo" @click="undoBulk">
            <svg class="fa-ring" viewBox="0 0 30 28" preserveAspectRatio="none" aria-hidden="true">
              <rect x="1" y="1" width="28" height="26" rx="6" pathLength="110" :style="ringDelay(bulk.start)" />
            </svg>
            <FaIcon name="undo" :size="13" /><span class="fa-undo-badge">{{ bulk.items.length }}</span>
          </Button>
          <Button v-else variant="danger" size="sm" class="fa-bar-btn" :disabled="readOnly || !visibleSelected.length" :title="w.removeSelected"
            data-test="flow-attachment-bulk-delete" @click="removeSelected">
            <FaIcon name="trash" :size="13" />{{ w.remove }}
          </Button>
          <span class="fa-spacer" />
          <Button variant="ghost" size="sm" class="fa-bar-btn" :disabled="!selected.size" data-test="flow-attachment-clear-selection" @click="clearSelection">
            {{ w.clearSelection }}
          </Button>
        </div>
      </div>

      <div v-if="collision" class="fa-conflict" data-test="flow-attachment-conflict">
        <div class="fa-conflict-card">
          <h3>{{ w.conflictTitle }}</h3>
          <p :title="title">{{ collision.file.name }}</p>
          <div class="fa-conflict-actions">
            <Button variant="secondary" size="sm" data-conflict="cancel" @click="resolveCollision('cancel')">{{ w.cancel }}</Button>
            <Button variant="secondary" size="sm" data-conflict="keep_both" @click="resolveCollision('keep_both')">{{ w.keep }}</Button>
            <Button variant="danger" size="sm" data-conflict="overwrite" class="replace" @click="resolveCollision('overwrite')">{{ w.overwrite }}</Button>
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
// 0074 (items 3·4·5, mockup mirageglass 21wdt1cz v2): newest first with an order menu, origin chips with counts that
// follow the search, the whole row selects (Shift+click = range over the shown rows), a bar under the list while files
// are selected (one file → [다운로드], more → [ZIP 다운로드], [삭제] with the same five-second undo, [선택 해제]; Esc
// clears), a first size of 720 × 600 (placement.ts) and focusFile() for focusAttachment() (focus.ts).
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useInspectorEvents } from '../inspector/useInspectorResource'
import { downloadFile, downloadZip, type FlowFile } from './api'
import * as ops from './operations'
import { UNDO_MS } from './undo'
import { wordsFor } from './words'
import { FaIcon } from './icons'
import { FOCUS_HIGHLIGHT_MS } from './focus'
import { ORIGIN_FILTERS, SORT_KEYS, countByOrigin, matchesOrigin, rangeKeys, readSort, sortFiles, writeSort, type OriginFilter, type SortKey } from './listView'
import { initialRect } from './placement'
import Button from '@/components/ui/Button.vue'
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
const list = ref<HTMLElement | null>(null)
const place = reactive({ x: 0, y: 0 })
// R5: the ring's duration is UNDO_MS itself (one number for the timer and the border).
const placeStyle = computed(() => ({ left: `${place.x}px`, top: `${place.y}px`, zIndex: props.zIndex, '--fa-undo-ms': `${UNDO_MS}ms` }))
// First place and size (0074 item 5-2): 720 × 600, or the viewport minus 25 px, kept fully on screen.
const first = initialRect(innerWidth, innerHeight, props.position)
place.x = first.x; place.y = first.y
onMounted(() => {
  const el = root.value
  if (!el) return
  el.style.width = `${first.width}px`
  el.style.height = `${first.height}px`
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
const filter = ref<OriginFilter>('all')
const sort = ref<SortKey>(readSort())
watch(sort, writeSort)
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
const matchesSearch = (file: FlowFile, q = search.value.trim().toLocaleLowerCase()) =>
  !q || file.display_name.toLocaleLowerCase().includes(q) || originLabel(file).toLocaleLowerCase().includes(q)
// The search result in the chosen order; the chips count it, the list shows the part the chosen chip keeps.
const searched = computed(() => {
  const q = search.value.trim().toLocaleLowerCase()
  return sortFiles(shown.value.filter(f => matchesSearch(f, q)), sort.value)
})
const visible = computed(() => searched.value.filter(f => matchesOrigin(f, filter.value)))
// Chip counts: files of the search result, without rows struck through for delete (the same rule as the count).
const chipCounts = computed(() => countByOrigin(searched.value.filter(f => !isGone(f))))
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
/* ── 0074 item 4: row click / Shift range ── */
// The row last pressed without Shift; a Shift press selects from it to the pressed row, over the rows shown now
// (after the chip and the search) and only rows that can be picked (not struck through). The anchor stays put, so
// Shift+clicks widen or narrow from the same row as in a file manager.
let anchor: string | null = null
let shiftBox = false
function pickRow(file: FlowFile, shift: boolean) {
  if (isGone(file)) return
  const range = shift ? rangeKeys(visibleLive.value.map(f => f.key), anchor, file.key) : null
  if (!range) { toggle(file); anchor = file.key; return }
  const next = new Set(selected.value)
  for (const key of range) next.add(key)
  selected.value = next
}
function rowClick(file: FlowFile, event: MouseEvent) {
  // The row's buttons (download, delete, ↶) keep their own action; the checkbox has its own handlers below.
  if ((event.target as Element | null)?.closest('button, input, a')) return
  pickRow(file, event.shiftKey)
}
// A checkbox press: click comes before change, so remember Shift from the click and act on the change.
function boxClick(event: MouseEvent) { shiftBox = event.shiftKey }
function boxChange(file: FlowFile, event: Event) {
  const shift = shiftBox
  shiftBox = false
  pickRow(file, shift)
  // A range keeps an already picked box checked: put the box back in line with the selection.
  const box = event.target as HTMLInputElement
  box.checked = selected.value.has(file.key)
}
// Shift+click would also select the row's text; keep the text as it is.
function holdTextSelection(event: MouseEvent) { if (event.shiftKey) event.preventDefault() }
/* ── 0074 item 5: the bar under the list ── */
const barOpen = computed(() => visibleSelected.value.length > 0 || !!bulk.value)
function clearSelection() { selected.value = new Set(); anchor = null }
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
// Esc (focus inside this window) clears the selection; with nothing selected it is left to whoever else listens.
function keyDown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (!selected.value.size || collision.value) return
    clearSelection(); event.preventDefault(); event.stopPropagation()
    return
  }
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

/* ── 0074 item 3: go to one file (focusAttachment) ── */
const flashKey = ref<string | null>(null)
let flashTimer: ReturnType<typeof setTimeout> | undefined
function flash(key: string) {
  clearTimeout(flashTimer)
  flashKey.value = key
  flashTimer = setTimeout(() => { if (flashKey.value === key) flashKey.value = null }, FOCUS_HIGHLIGHT_MS)
}
/**
 * Shows one file: reads the list again when the file is not in it yet (just approved, window just opened), drops the
 * chip / search that hides it, scrolls its row into view and lights it for FOCUS_HIGHLIGHT_MS. False (and nothing
 * changed) when the Flow has no such file.
 */
async function focusFile(key: string): Promise<boolean> {
  const find = () => shown.value.find(f => f.key === key)
  if (!find()) await ops.refresh(scope)
  const file = find()
  if (!alive || !file) return false
  if (!matchesOrigin(file, filter.value)) filter.value = 'all'
  if (!matchesSearch(file)) search.value = ''
  await nextTick()
  if (!alive) return false
  const row = Array.from(list.value?.querySelectorAll<HTMLElement>('[data-test="flow-attachment-row"]') ?? []).find(el => el.dataset.key === key)
  row?.scrollIntoView?.({ block: 'center' })
  flash(key)
  return true
}

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
  alive = false; ops.detachView(scope, viewId); endDrag(); clearTimeout(flashTimer)
  removeEventListener('resize', keepInView)
})
defineExpose({ add, reload, reveal, setGeometry, undoLast, focusFile })
</script>

<style scoped>
.fa-window { position: fixed; min-width: 430px; min-height: 310px; max-width: calc(100vw - 18px); max-height: calc(100vh - 12px);
  display: flex; flex-direction: column; overflow: hidden; resize: both; box-sizing: border-box; outline: none;
  border: 1px solid #4a88bf; border-radius: 9px; background: #0d2033; color: #d8e8f5; font-size: 12px;
  box-shadow: 0 18px 58px rgba(0, 0, 0, .62), 0 0 0 1px rgba(12, 33, 49, .6); transition: box-shadow .18s; }
.fa-window.focused { border-color: #66b6fc; box-shadow: 0 21px 65px rgba(0, 0, 0, .75), 0 0 0 1px #265a85; }
.fa-window :deep(.fa-icon) { flex-shrink: 0; }
/* Buttons are the shared ui Button (0074); only the font family follows the window, its size and weight stay the Button's. */
.fa-window button { font-family: inherit; }
.fa-window input:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 1px; }

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
.fa-window .fa-close { width: 27px; height: 25px; padding: 0; border: 1px solid var(--fg2-line-strong); border-radius: 5px; color: var(--fg2-control-fg); }
.fa-window .fa-close:hover { background: var(--fg2-status-danger-bg); border-color: var(--fg2-status-danger-border); color: var(--fg2-text); }

.fa-tools { display: flex; align-items: center; gap: 7px; padding: 10px 12px 8px; flex-shrink: 0; }
.fa-search { flex: 1; min-width: 80px; height: 31px; box-sizing: border-box; display: flex; align-items: center; gap: 7px; padding: 0 9px;
  border: 1px solid #355471; border-radius: 5px; background: #0b1b2c; color: #6e91ad; }
.fa-search input { width: 100%; min-width: 0; border: 0; outline: none; background: none; color: #deeffc; font: inherit; font-size: 11px; }
.fa-search input::placeholder { color: #6686a2; }
.fa-window .fa-add { height: 31px; padding: 0 11px; }
.fa-window .fa-add :deep(.ui-btn-content) { gap: 6px; }
.fa-count { color: var(--fg2-muted); font-size: 10px; flex-shrink: 0; white-space: nowrap; }
.fa-count b { color: var(--fg2-text-2); }
.fa-progress { padding: 0 12px 8px; }

/* 0074 item 3: origin chips, shown count, order menu */
.fa-filters { display: flex; align-items: center; gap: 8px; padding: 0 12px 8px; flex-shrink: 0; min-width: 0; }
.fa-chips { display: flex; align-items: center; gap: 5px; min-width: 0; flex-wrap: wrap; }
.fa-window .fa-chip { height: 24px; padding: 0 9px; font-size: 11px; font-weight: 600; border-radius: var(--fg2-control-radius-pill); color: var(--fg2-muted); }
.fa-window .fa-chip :deep(.ui-btn-content) { gap: 5px; }
.fa-window .fa-chip.active { border-color: var(--fg2-status-info-border); background: var(--fg2-status-info-bg); color: var(--fg2-text); }
.fa-chip-count { min-width: 16px; padding: 0 5px; box-sizing: border-box; border-radius: var(--fg2-control-radius-pill); background: var(--fg2-line);
  color: var(--fg2-text-2); font-size: 10px; line-height: 16px; text-align: center; }
.fa-chip.active .fa-chip-count { background: var(--fg2-status-info-border); color: var(--fg2-text); }
.fa-sort { height: 24px; padding: 0 6px; font: inherit; font-size: 11px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface-flat); color: var(--fg2-control-fg); cursor: pointer; }
.fa-sort:hover { border-color: var(--fg2-control-hover-border); }
.fa-sort:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: 1px; }

.fa-selection { height: 36px; flex-shrink: 0; display: flex; align-items: center; gap: 8px; padding: 0 12px; color: var(--fg2-text-2);
  background: var(--fg2-panel); border-top: 1px solid var(--fg2-line); border-bottom: 1px solid var(--fg2-line-strong); }
.fa-select-all { display: inline-flex; align-items: center; gap: 7px; cursor: pointer; white-space: nowrap; font-size: 11px; font-weight: 650; }
.fa-box { width: 15px; height: 15px; margin: 0; flex: none; accent-color: #379cf5; cursor: pointer; }
.fa-selected { font-size: 10px; color: var(--fg2-muted); white-space: nowrap; }

.fa-errors { flex-shrink: 0; max-height: 96px; overflow-y: auto; }
.fa-error { margin: 0; padding: 6px 8px 6px 12px; display: flex; align-items: flex-start; gap: 8px; font-size: 11px; color: #ffb3bc; background: #3a1f2b; border-bottom: 1px solid #6d3445; }
.fa-error-text { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.fa-window .fa-error-close { flex: none; width: 20px; height: 20px; padding: 0; border: 1px solid var(--fg2-status-danger-border); border-radius: 4px; color: var(--fg2-status-danger-fg); }
.fa-window .fa-error-close:hover:not(:disabled) { background: var(--fg2-status-danger-bg); color: var(--fg2-text); }

.fa-body { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; }
.fa-list { position: relative; flex: 1; min-height: 0; overflow-y: auto; background: #0c1d30; }
.fa-list::-webkit-scrollbar { width: 9px; }
.fa-list::-webkit-scrollbar-track { background: #14283e; }
.fa-list::-webkit-scrollbar-thumb { background: #3b6789; border: 2px solid #14283e; border-radius: 8px; }
/* 0074 item 4: the whole row is the press target (its buttons excepted). */
.fa-row { min-height: 52px; box-sizing: border-box; display: flex; align-items: center; gap: 11px; padding: 7px 12px; border-bottom: 1px solid #203a54; cursor: pointer; }
.fa-row:hover { background: #153049; }
.fa-row.selected { background: #143855; }
/* 0074 item 3: the row focusAttachment() went to, lit green for FOCUS_HIGHLIGHT_MS (2 s). */
.fa-row.flash { animation: fa-flash 2s ease-out; box-shadow: inset 3px 0 0 var(--fg2-status-success-fg); }
@keyframes fa-flash { 0%, 60% { background: var(--fg2-status-success-bg); } 100% { background: transparent; } }
.fa-row-select { display: flex; align-items: center; align-self: stretch; }
.fa-glyph { flex: 0 0 33px; width: 33px; height: 33px; box-sizing: border-box; display: grid; place-items: center;
  border: 1px solid #2b5273; border-radius: 7px; background: #18344f; color: #75b5e5; }
.fa-glyph.image { color: #74d6d5; background: #123f4b; border-color: #286a72; }
.fa-glyph.text { color: #b8a5ff; background: #292d55; border-color: #56548f; }
.fa-meta { flex: 1; min-width: 0; }
.fa-name { margin-bottom: 5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 650; color: #dbeaff; }
.fa-source { display: flex; align-items: center; gap: 7px; white-space: nowrap; font-size: 10px; color: #7698b3; }
.fa-tag { padding: 2px 6px; border-radius: 3px; background: #1a3e58; color: #9dcae9; }
.fa-size { min-width: 53px; text-align: right; font-size: 10px; color: #87a5be; }
.fa-actions { flex: 0 0 auto; display: flex; align-items: center; gap: 3px; cursor: default; }
.fa-window .fa-action { position: relative; width: 26px; height: 26px; padding: 0; border-radius: 5px; color: var(--fg2-control-fg); }
.fa-window .fa-action:hover:not(:disabled) { border-color: var(--fg2-control-hover-border); color: var(--fg2-text); }
.fa-window .fa-action.del:hover:not(:disabled) { color: var(--fg2-status-danger-fg); background: var(--fg2-status-danger-bg); border-color: var(--fg2-status-danger-border); }

.fa-row.gone, .fa-row.gone:hover { background: #0e1a27; }
.fa-row.gone .fa-name { color: #5f7489; text-decoration: line-through; }
.fa-row.gone .fa-glyph, .fa-row.gone .fa-source, .fa-row.gone .fa-size, .fa-row.gone .fa-row-select,
.fa-row.gone [data-test="flow-attachment-download"] { opacity: .3; pointer-events: none; }
.fa-window .undo { position: relative; color: var(--fg2-status-info-fg); background: var(--fg2-status-info-bg); border-color: var(--fg2-line-strong); opacity: 1; overflow: visible; }
.fa-window .undo:hover { color: var(--fg2-text); border-color: var(--fg2-status-info-border); }
.fa-window .fa-icon-btn { width: 30px; padding: 0; }
.fa-ring { position: absolute; inset: -1px; width: calc(100% + 2px); height: calc(100% + 2px); pointer-events: none; overflow: visible; }
.fa-ring rect { fill: none; stroke: var(--fg2-status-info-fg); stroke-width: 2; stroke-dasharray: 110; stroke-dashoffset: 0; animation: fa-ringdown var(--fa-undo-ms) linear forwards; }
@keyframes fa-ringdown { to { stroke-dashoffset: 110; } }
.fa-undo-badge { position: absolute; top: -6px; right: -6px; min-width: 14px; height: 14px; padding: 0 3px; box-sizing: border-box;
  border-radius: 8px; background: var(--fg2-status-info-fg); color: var(--fg2-bg); font-size: 9px; font-weight: 800; line-height: 14px; text-align: center; }

/* 0074 item 5: the bar under the list; slides up when it opens, and closed it is out of view and out of the tab order. */
.fa-bar { flex-shrink: 0; display: flex; align-items: center; gap: 6px; box-sizing: border-box; height: 44px; max-height: 0; padding: 0 12px;
  overflow: hidden; visibility: hidden; transform: translateY(100%); background: var(--fg2-panel); border-top: 1px solid transparent;
  transition: max-height .16s ease, transform .16s ease, visibility 0s linear .16s; }
.fa-bar.open { max-height: 44px; overflow: visible; visibility: visible; transform: none; border-top-color: var(--fg2-line-strong);
  box-shadow: 0 -8px 20px rgba(0, 0, 0, .25); transition: max-height .16s ease, transform .16s ease; }
.fa-bar-count { margin-right: 4px; font-size: 11px; font-weight: 700; color: var(--fg2-text); white-space: nowrap; }
.fa-window .fa-bar-btn { height: 28px; padding: 0 10px; font-size: 11px; }
.fa-window .fa-bar-btn :deep(.ui-btn-content) { gap: 5px; }

.fa-empty { height: 100%; min-height: 120px; box-sizing: border-box; display: flex; flex-direction: column; align-items: center; justify-content: center;
  gap: 9px; padding: 30px; text-align: center; color: #7596b4; }

.fa-conflict { position: absolute; inset: 0; z-index: 5; display: flex; align-items: center; justify-content: center; padding: 20px; background: rgba(3, 15, 28, .8); }
.fa-conflict-card { width: min(385px, 100%); box-sizing: border-box; padding: 17px; border: 1px solid #426c90; border-radius: 8px; background: #142c45; box-shadow: 0 16px 44px #0009; }
.fa-conflict-card h3 { margin: 0 0 8px; font-size: 13px; color: #eaf6ff; }
.fa-conflict-card p { margin: 0 0 16px; font-size: 11px; line-height: 1.55; color: #9db9d1; overflow-wrap: anywhere; }
.fa-conflict-actions { display: flex; justify-content: flex-end; gap: 7px; }


@media (max-width: 650px) {
  .fa-selection, .fa-bar { gap: 5px; padding: 0 8px; }
  .fa-filters { padding: 0 8px 8px; }
  .fa-row { padding: 7px 8px; gap: 7px; }
  .fa-size { display: none; }
}
</style>

