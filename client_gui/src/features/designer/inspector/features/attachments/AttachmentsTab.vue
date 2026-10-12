<template>
  <!-- flowgate.v02.0048 T#8 — attachments tab (D#1 §10.2 T#8 row): the attachments of the selected Node's
       document — open (download), ＋ add, delete with a confirm step — with the reason when a change is
       disabled and the refusal the server answered. A thin container: useAttachments reads and changes,
       attachmentsModel decides the target document, the rows and availability.
       flowgate.v02.0053 T#6 (0053 CH §3) — [+] alone adds files (the size limit is its tooltip, no
       '＋ 추가' / '파일당 최대 nMB' text), an empty document shows only '첨부 없음' (v7), and files dropped
       anywhere on the tab are uploaded the same way as picked ones (same API, limits and refusals); the
       tab is highlighted while files are dragged over it, only when adding is possible.
       flowgate.v02.0056 F1 (0056 CH §4) — the delete button of a row is the trash icon (tooltip 삭제); the
       confirm step is unchanged.
       In the Frame the drop is taken on the whole tab Content (INSPECTOR_BODY) — the free space under a
       short list and the body's padding too — and this box fills the body (`fill`), so the highlight
       covers the tab. Outside the Frame the box itself is the drop area. -->
  <div
    ref="root"
    class="inspector-attachments"
    :class="{ 'is-dropping': dropping }"
    data-test="inspector-attachments"
    :data-status="status"
    :data-drop="dropping ? 'active' : 'idle'"
  >
    <div class="inspector-attachments-bar">
      <input
        ref="picker"
        class="inspector-attachments-picker"
        type="file"
        multiple
        hidden
        data-test="attachments-picker"
        @change="onPick"
      />
      <InspectorAddButton
        :label="t(k('add'))"
        :title="t(k('add_tip'), { size: limits.maxUploadMb })"
        :disabled="!availability.add.enabled"
        :data-busy="busy?.operation === 'add' ? 'true' : 'false'"
        data-test="attachments-add"
        @click="pickFiles"
      />
    </div>
    <div v-if="availability.add.reason" data-test="attachments-reason">
      <InspectorReason :text="t(k(availability.add.reason), { limit: limits.maxCount })" />
    </div>
    <div v-if="failure && !rowFailure" data-test="attachments-failure">
      <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
    </div>

    <InspectorPaneState v-if="status === 'loading' && !rows" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !rows" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="rows">
      <InspectorPaneState v-if="!rows.length" state="empty" :message="t(k('empty'))" />
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <ul v-if="rows.length" class="inspector-attachments-list">
        <li v-for="row in rows" :key="row.filename" class="inspector-attachments-item" data-test="attachments-item" :data-name="row.filename">
          <div class="inspector-attachments-line">
            <button
              class="inspector-attachments-name"
              type="button"
              :title="row.filename"
              :disabled="opening !== null"
              data-test="attachments-open"
              @click="open(row.filename)"
            >{{ row.filename }}</button>
            <span class="inspector-attachments-sub" data-test="attachments-size">{{ t(k(row.size.key), { value: row.size.value }) }}</span>
            <!-- flowgate.v02.0056 F1 (0056 CH §4): the delete button is the trash icon, its name the tooltip; the
                 inline confirm step under the row stays as it was. -->
            <IconButton
              v-if="confirming !== row.filename"
              :label="t(k('delete'))"
              :title="t(k('delete'))"
              variant="ghost"
              size="sm"
              :disabled="!availability.delete.enabled || (busy?.operation === 'delete' && busy.filename === row.filename)"
              :data-busy="busy?.operation === 'delete' && busy.filename === row.filename ? 'true' : 'false'"
              data-icon="trash"
              data-test="attachments-delete"
              @click="confirming = row.filename"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                <path d="M2.8 4.4h10.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" />
                <path d="M6 4.4V2.9h4v1.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
                <path d="M4.3 4.4l.7 8.7h6l.7-8.7" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
                <path d="M6.7 7v4M9.3 7v4" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
              </svg>
            </IconButton>
          </div>
          <span v-if="row.originalFilename" class="inspector-attachments-sub" data-test="attachments-original">{{ t(k('original'), { name: row.originalFilename }) }}</span>
          <div v-if="confirming === row.filename" class="inspector-attachments-line" data-test="attachments-confirm">
            <span class="inspector-attachments-sub">{{ t(k('delete_confirm'), { name: row.filename }) }}</span>
            <Button size="sm" variant="danger" :disabled="!availability.delete.enabled" data-test="attachments-delete-yes" @click="confirmDelete(row.filename)">{{ t(k('delete_yes')) }}</Button>
            <Button size="sm" variant="secondary" data-test="attachments-delete-no" @click="confirming = null">{{ t(k('delete_no')) }}</Button>
          </div>
          <div v-if="failure && rowFailure && failure.filename === row.filename" data-test="attachments-item-failure">
            <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
          </div>
        </li>
      </ul>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import IconButton from '../../../../../components/ui/IconButton.vue'
import InspectorAddButton from '../../parts/InspectorAddButton.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import { INSPECTOR_BODY } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { useAttachments } from './useAttachments'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.attachments.${key}`

const { rows, availability, status, errorKey, busy, opening, failure, limits, add, remove, open, reload } = useAttachments(() => props.target)

const picker = ref<HTMLInputElement | null>(null)
// A refusal about one attachment shows on its row; an add refusal, or one whose row is gone after the
// read again (the file was deleted elsewhere), shows above the list.
const rowFailure = computed(() => {
  const current = failure.value
  return !!current && current.filename !== null && !!rows.value?.some((row) => row.filename === current.filename)
})

/** The row whose delete waits for the confirm step, or null. */
const confirming = ref<string | null>(null)

watch(
  () => props.target.docId,
  () => {
    confirming.value = null
    endDrag()
  },
)

function pickFiles() {
  if (availability.value.add.enabled) picker.value?.click()
}

function onPick(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files ?? [])
  input.value = ''
  if (files.length) void add(files)
}

async function confirmDelete(filename: string) {
  confirming.value = null
  await remove(filename)
}

// --- Drag and drop (0053 T#6) ----------------------------------------------------------------------

/** Files are being dragged over the tab and adding is possible: the tab is highlighted. */
const dropping = ref(false)
// dragenter / dragleave fire for every child the pointer crosses; the tab is left when the count is 0.
let dragDepth = 0

/** The drag carries files (not text or a link dragged from the page). */
function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

function endDrag() {
  dragDepth = 0
  dropping.value = false
}

function onDragEnter(event: DragEvent) {
  if (!carriesFiles(event)) return
  event.preventDefault()
  dragDepth += 1
  dropping.value = availability.value.add.enabled
}

function onDragOver(event: DragEvent) {
  if (!carriesFiles(event)) return
  // Without preventDefault the browser would open the dropped file instead of handing it to the tab.
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = availability.value.add.enabled ? 'copy' : 'none'
  dropping.value = availability.value.add.enabled
}

function onDragLeave(event: DragEvent) {
  if (!carriesFiles(event)) return
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dropping.value = false
}

/** Dropped files go the same way as picked ones: the client limits, then the upload and the read again. */
function onDrop(event: DragEvent) {
  if (!carriesFiles(event)) return
  event.preventDefault()
  endDrag()
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (files.length && availability.value.add.enabled) void add(files)
}

const root = ref<HTMLElement | null>(null)
const body = inject(INSPECTOR_BODY, null)
/** Where files are taken: the Frame's whole tab Content, or this box outside the Frame. */
const dropZone = computed(() => body?.element.value ?? root.value)

// The listeners follow the drop area and leave it when the tab goes (the body stays for the other tabs).
watch(
  dropZone,
  (zone, _previous, onCleanup) => {
    endDrag()
    if (!zone) return
    zone.addEventListener('dragenter', onDragEnter)
    zone.addEventListener('dragover', onDragOver)
    zone.addEventListener('dragleave', onDragLeave)
    zone.addEventListener('drop', onDrop)
    onCleanup(() => {
      zone.removeEventListener('dragenter', onDragEnter)
      zone.removeEventListener('dragover', onDragOver)
      zone.removeEventListener('dragleave', onDragLeave)
      zone.removeEventListener('drop', onDrop)
    })
  },
  { immediate: true, flush: 'post' },
)
</script>

<style scoped>
.inspector-attachments {
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-sizing: border-box;
  min-width: 0;
  /* The drop area: the whole tab (it fills the Frame body), at least a few rows high even with no attachment. */
  flex: 1 0 auto;
  min-height: 96px;
  padding: 4px;
  border: 1px dashed transparent;
  border-radius: 8px;
  font-size: 12px;
  transition: border-color 0.12s, background-color 0.12s;
}

/* Files dragged over the tab (adding possible). */
.inspector-attachments.is-dropping {
  border-color: var(--fg2-focus, #4c9aff);
  background: rgba(76, 154, 255, 0.08);
}

.inspector-attachments-bar,
.inspector-attachments-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-attachments-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-attachments-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-bottom: 6px;
  border-bottom: 1px solid var(--fg2-line);
}

.inspector-attachments-name {
  flex: 1 1 auto;
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: var(--fg2-link, inherit);
  font: inherit;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-attachments-name:hover {
  text-decoration: underline;
}

.inspector-attachments-name:disabled {
  cursor: progress;
}

.inspector-attachments-sub {
  color: var(--fg2-muted);
  font-size: 11.5px;
}
</style>
