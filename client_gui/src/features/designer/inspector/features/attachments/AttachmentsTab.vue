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
       flowgate.v02.0056 (0017-CH) — drawn after the 시안 flowgate-attachments-drag-over.html: an empty document shows
       the stacked-documents illustration, "첨부된 파일이 없습니다" and "파일을 추가해 주세요."; each file is a card line
       (document icon · name · size · [제거]); the blue [+ 파일 추가] sits under them (the top [+] is gone); files
       dragged over the tab raise the dashed blue drop area with "여기에 파일을 놓으세요". The confirm step stays.
       [+ 파일 추가] follows what it adds to: right under the two lines (the three centred together in the tab) or right
       under the last card — never held at the bottom. The cards take the tab's full width.
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
    <input
      ref="picker"
      class="inspector-attachments-picker"
      type="file"
      multiple
      hidden
      data-test="attachments-picker"
      @change="onPick"
    />
    <div v-if="availability.add.reason && availability.add.reason !== 'reason.read_only_revision'" data-test="attachments-reason">
      <InspectorReason :text="t(k(availability.add.reason), { limit: limits.maxCount })" />
    </div>
    <div v-if="failure && !rowFailure" data-test="attachments-failure">
      <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
    </div>

    <InspectorPaneState v-if="status === 'loading' && !rows" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !rows" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="rows">
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <!-- 0017-CH (시안): no attachment — the stacked documents with a blue [+] and two lines. -->
      <div v-if="!rows.length" class="inspector-attachments-empty" data-test="attachments-empty">
        <svg class="inspector-attachments-illustration" viewBox="0 0 128 100" aria-hidden="true" focusable="false">
          <rect x="36" y="12" width="58" height="70" rx="9" fill="#172f4a" stroke="#385a7e" stroke-width="1.5" transform="rotate(11 65 47)" />
          <rect x="23" y="18" width="62" height="72" rx="9" fill="#223f60" stroke="#5b82ad" stroke-width="1.5" transform="rotate(-9 54 54)" />
          <rect x="40" y="16" width="57" height="71" rx="9" fill="#18304c" stroke="#73a8de" stroke-width="1.5" />
          <path d="M54 36H81 M54 45H79 M54 54H72" fill="none" stroke="#6d99c3" stroke-width="3" stroke-linecap="round" />
          <circle cx="91" cy="75" r="19" fill="#327ddb" stroke="#8abcf4" stroke-width="1.5" />
          <path d="M91 67V83 M83 75H99" stroke="white" stroke-width="2.5" stroke-linecap="round" />
        </svg>
        <p class="inspector-attachments-empty-title">{{ t(k('empty')) }}</p>
        <p class="inspector-attachments-empty-hint">{{ t(k('empty_hint')) }}</p>
      </div>
      <ul v-else class="inspector-attachments-list">
        <li v-for="row in rows" :key="row.filename" class="inspector-attachments-item" data-test="attachments-item" :data-name="row.filename">
          <div class="inspector-attachments-line">
            <svg class="inspector-attachments-file-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
              <path d="M4 1.75h5.1l3.15 3.15v9.35H4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round" />
              <path d="M9 1.75V5h3.25M6.1 8.1h4.1M6.1 10.6h4.1" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" />
            </svg>
            <span class="inspector-attachments-text">
              <button
                class="inspector-attachments-name"
                type="button"
                :title="row.filename"
                :disabled="opening !== null"
                data-test="attachments-open"
                @click="open(row.filename)"
              >{{ row.filename }}</button>
              <span class="inspector-attachments-sub">
                <span data-test="attachments-size">{{ t(k(row.size.key), { value: row.size.value }) }}</span>
                <template v-if="row.originalFilename">
                  <span aria-hidden="true"> · </span>
                  <span data-test="attachments-original">{{ t(k('original'), { name: row.originalFilename }) }}</span>
                </template>
              </span>
            </span>
            <button
              v-if="confirming !== row.filename"
              type="button"
              class="inspector-attachments-remove"
              :disabled="!availability.delete.enabled || (busy?.operation === 'delete' && busy.filename === row.filename)"
              :data-busy="busy?.operation === 'delete' && busy.filename === row.filename ? 'true' : 'false'"
              data-test="attachments-delete"
              @click="confirming = row.filename"
            >{{ t(k('remove')) }}</button>
          </div>
          <div v-if="confirming === row.filename" class="inspector-attachments-confirm" data-test="attachments-confirm">
            <span class="inspector-attachments-confirm-text">{{ t(k('delete_confirm'), { name: row.filename }) }}</span>
            <Button size="sm" variant="secondary" data-test="attachments-delete-no" @click="confirming = null">{{ t(k('delete_no')) }}</Button>
            <Button size="sm" variant="danger" :disabled="!availability.delete.enabled" data-test="attachments-delete-yes" @click="confirmDelete(row.filename)">{{ t(k('delete_yes')) }}</Button>
          </div>
          <div v-if="failure && rowFailure && failure.filename === row.filename" data-test="attachments-item-failure">
            <InspectorReason tone="danger" :text="t(k(failure.key), failure.params)" />
          </div>
        </li>
      </ul>
    </template>
    <div class="inspector-attachments-actions">
      <button
        type="button"
        class="inspector-attachments-add"
        :title="t(k('add_tip'), { size: limits.maxUploadMb })"
        :disabled="!availability.add.enabled"
        :data-busy="busy?.operation === 'add' ? 'true' : 'false'"
        data-test="attachments-add"
        @click="pickFiles"
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" aria-hidden="true" focusable="false"><path d="M12 5v14M5 12h14" /></svg>
        {{ t(k('add')) }}
      </button>
    </div>

    <!-- 0017-CH: files dragged over the tab (adding possible) — the dashed blue drop area over the whole tab. -->
    <div v-if="dropping" class="inspector-attachments-drop" data-test="attachments-drop" aria-live="polite">
      <svg class="inspector-attachments-drop-icon" viewBox="0 0 100 94" fill="none" aria-hidden="true" focusable="false">
        <rect x="19" y="16" width="53" height="66" rx="8" fill="#21496F" stroke="#7BB8EE" stroke-width="1.7" transform="rotate(-10 45 49)" />
        <rect x="31" y="10" width="51" height="69" rx="8" fill="#17395C" stroke="#91C6F5" stroke-width="1.7" />
        <path d="M43 29H70M43 39H63M43 49H56" stroke="#80AFDB" stroke-width="3" stroke-linecap="round" />
        <circle cx="72" cy="69" r="18" fill="#388CE6" stroke="#A7D0FF" stroke-width="1.5" />
        <path d="M72 78V60M72 60L65 67M72 60L79 67" stroke="white" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
      <p class="inspector-attachments-drop-title">{{ t(k('drop')) }}</p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, inject, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
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
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 8px;
  box-sizing: border-box;
  min-width: 0;
  /* The drop area: the whole tab (it fills the Frame body), high enough for the empty illustration. */
  flex: 1 0 auto;
  min-height: 220px;
  padding: 4px 0;
  font-size: 12px;
}

/* No attachment: the illustration, two lines and [+ 파일 추가] centred together in the tab (the free height is
   split above them and under the button, so the button stays right under the lines). */
.inspector-attachments-empty {
  display: flex;
  flex-direction: column;
  margin-top: auto;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px 10px 4px;
  text-align: center;
}

.inspector-attachments-illustration {
  display: block;
  width: 110px;
  height: 92px;
}

.inspector-attachments-empty-title {
  margin: 0;
  color: #e6f1ff;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.3px;
}

.inspector-attachments-empty-hint {
  margin: 0;
  color: #8ba3bd;
  font-size: 12.5px;
}

.inspector-attachments-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-attachments-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 11px 12px;
  border: 1px solid #2c4561;
  border-radius: 9px;
  background: #12263b;
}

.inspector-attachments-line {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.inspector-attachments-file-icon {
  flex: none;
  width: 18px;
  height: 18px;
  color: #6ca7e3;
}

.inspector-attachments-text {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.inspector-attachments-name {
  min-width: 0;
  padding: 0;
  overflow: hidden;
  border: 0;
  background: none;
  color: #e6f1ff;
  font: inherit;
  font-size: 13px;
  text-align: left;
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.inspector-attachments-name:hover {
  text-decoration: underline;
  text-underline-offset: 3px;
}

.inspector-attachments-name:disabled {
  cursor: progress;
}

.inspector-attachments-sub {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* [제거]: a quiet outlined button (시안 `.remove`). */
.inspector-attachments-remove {
  flex: none;
  padding: 5px 11px;
  border: 1px solid #3e5974;
  border-radius: 7px;
  background: transparent;
  color: #b9cfe1;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.inspector-attachments-remove:hover:not(:disabled) {
  border-color: #6b8db0;
  color: #e6f1ff;
}

.inspector-attachments-remove:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.inspector-attachments-confirm {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid #2c4561;
}

.inspector-attachments-confirm-text {
  flex: 1 1 auto;
  min-width: 0;
  color: #c7d6e6;
  font-size: 12px;
}

.inspector-attachments-actions {
  display: flex;
  justify-content: center;
  padding: 6px 0 4px;
}

.inspector-attachments-empty + .inspector-attachments-actions {
  margin-bottom: auto;
}

/* [+ 파일 추가]: the blue button of the 시안 (`.add`). */
.inspector-attachments-add {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 10px 18px;
  border: 0;
  border-radius: 9px;
  background: #2d75d2;
  color: #fff;
  font: inherit;
  font-size: 13px;
  font-weight: 600;
  line-height: 1.1;
  cursor: pointer;
}

.inspector-attachments-add svg {
  width: 15px;
  height: 15px;
}

.inspector-attachments-add:hover:not(:disabled) {
  background: #3887ee;
}

.inspector-attachments-add:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.inspector-attachments-add:focus-visible,
.inspector-attachments-remove:focus-visible,
.inspector-attachments-name:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 2px;
}

/* Files dragged over the tab: the dashed blue drop area over everything (the drop itself goes to the tab). */
.inspector-attachments-drop {
  position: absolute;
  inset: 0;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  border: 2px dashed #4f9cf0;
  border-radius: 13px;
  background: radial-gradient(ellipse at 50% 45%, #16395b 0, #102a46 55%, #0b2036 100%);
  box-shadow: inset 0 0 0 1px #173c67, 0 0 30px rgba(47, 124, 224, 0.11);
  pointer-events: none;
}

.inspector-attachments-drop-icon {
  width: 88px;
  height: 83px;
  filter: drop-shadow(0 5px 15px rgba(28, 99, 174, 0.2));
}

.inspector-attachments-drop-title {
  margin: 0;
  color: #f0f7ff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.3px;
}
</style>
