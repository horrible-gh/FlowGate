<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="Math.min(win.x, Math.max(0, viewportWidth - initialWidth - 16))"
    :initial-y="Math.min(win.y, Math.max(0, viewportHeight - initialHeight - 16))"
    :initial-width="initialWidth"
    :initial-height="initialHeight"
    :min-width="560"
    :min-height="320"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setWorkPlanEditWindowMinimized(win.docId, value)"
    @close="requestClose"
  >
    <template #header>
      <span class="wp-window-heading" data-test="wp-edit-heading">
        <span class="wp-window-tile" aria-hidden="true"><svg viewBox="0 0 24 24"><path :d="paletteIconPath('file')" /></svg></span>
        <span class="wp-window-kind" :title="docTitle" data-test="wp-edit-title">{{ t('title') }}</span>
        <span v-if="view?.docReviewStatus" class="wp-window-state" data-test="wp-edit-state">{{ stateLabel }}</span>
        <span v-if="dirty" class="wp-window-dirty" :title="t('dirty')" data-test="wp-edit-dirty">●</span>
      </span>
    </template>
    <template #actions>
      <span class="wp-window-json" data-test="wp-edit-json" @mousedown.stop>
        <IconButton
          size="sm"
          :label="t('json.import')"
          :title="importBlocked ? t(`json.blocked.${importBlocked}`) : t('json.import')"
          :disabled="importBlocked !== null"
          data-test="wp-edit-json-import"
          @click="openImport"
        ><svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path :d="UPLOAD_PATH" /></svg></IconButton>
        <IconButton size="sm" :label="t('json.export')" :title="t('json.export')" :disabled="!draft" data-test="wp-edit-json-export" @click="exportJson">
          <svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path :d="DOWNLOAD_PATH" /></svg>
        </IconButton>
        <IconButton size="sm" :label="t('json.raw')" :title="t('json.raw')" :disabled="!draft" data-test="wp-edit-json-raw" @click="jsonMode = 'raw'">
          <svg viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path :d="CODE_PATH" /></svg>
        </IconButton>
        <span class="wp-window-divider" role="separator" aria-orientation="vertical" />
      </span>
    </template>

    <div class="wp-window" data-test="wp-edit-window" :data-doc="win.docId">
      <div class="wp-window-scroll">
        <WorkPlanEditBody :editor="editor" :step-edit-available="workPlanStepEditAvailable()" :minimized="win.minimized" @edit-step="editStep" @reload="requestReload" />
      </div>
      <div class="wp-window-bar">
        <p
          v-if="notice"
          class="wp-window-notice"
          :class="`is-${notice.tone}`"
          role="status"
          data-test="wp-edit-notice"
          :data-key="notice.key"
        >{{ t(`notice.${notice.key}`, { ...(notice.params ?? {}) }) }}<template v-if="notice.detail"> — {{ t(notice.detail) }}</template></p>
        <p v-else-if="workPlanStepEditsPending(win.docId)" class="wp-window-notice" data-test="wp-edit-child-pending">{{ t('step_pending') }}</p>
        <p v-else-if="dirty" class="wp-window-notice is-unsaved" data-test="wp-edit-unsaved">{{ t('dirty') }}</p>
        <span v-else class="wp-window-notice" />
        <Button size="sm" variant="ghost" data-test="wp-edit-close" @click="requestClose">{{ t('close') }}</Button>
        <Button size="sm" variant="primary" :disabled="!canSave" :loading="saving" data-test="wp-edit-save" @click="editor.save()">
          <span class="wp-window-save"><svg v-if="!saving" viewBox="0 0 24 24" aria-hidden="true"><path :d="SAVE_PATH" /></svg>{{ saving ? t('saving') : t('save') }}</span>
        </Button>
      </div>
    </div>
  </ModelessShell>

  <ConfirmDialog
    :open="confirming === 'close'"
    :title="t('confirm.close_title')"
    :message="workPlanStepEditsPending(win.docId) ? t('confirm.close_message') : undefined"
    :confirm-label="t('confirm.close_ok')"
    :cancel-label="t('confirm.keep')"
    danger
    @confirm="close"
    @cancel="confirming = null"
  />
  <ConfirmDialog
    :open="confirming === 'reload'"
    :title="t('confirm.reload_title')"
    :confirm-label="t('confirm.reload_ok')"
    :cancel-label="t('confirm.keep')"
    danger
    @confirm="reload"
    @cancel="confirming = null"
  />
  <ConfirmDialog
    :open="pendingQuantity !== null"
    :title="t('confirm.quantity_title', { n: pendingQuantity?.valueSteps ?? 0 })"
    :message="t('confirm.quantity_message')"
    :confirm-label="t('confirm.quantity_ok')"
    :cancel-label="t('confirm.cancel')"
    danger
    @confirm="editor.confirmQuantity()"
    @cancel="editor.cancelQuantity()"
  />
  <WorkPlanJsonWindow
    v-if="jsonMode && draft"
    :key="jsonMode"
    :editor="editor"
    :mode="jsonMode"
    :doc-title="docTitle"
    :step-pending="workPlanStepEditsPending(win.docId)"
    :x="win.x + 40"
    :y="win.y + 40"
    @close="jsonMode = null"
  />
</template>

<script setup lang="ts">
// flowgate.v02.0072 T#1 — the v0.2 work plan edit window: one ModelessShell per work plan document (moves, resizes,
// minimizes and closes like every v0.2 modeless window; no backdrop), the editing body (WorkPlanEditBody) and the bar
// [닫기] [저장]. New v0.2 parts: the v0.1 WorkPlanEditor is neither mounted nor copied — it was the reference for
// what the plan editor does (quantities, Steps by card and their order, per-Step AI / review / note, defaults and
// [apply to all], save and lock). Not connected to the WP card here: the opener is T#3 (openWorkPlanEditWindow).
// - [저장] sends the whole body once (useWorkPlanEditor.save); closing or reading again with unsaved edits asks first,
//   and a quantity decrease that removes Steps with values asks first.
// - The window hears its document's project events and polls like the document windows (useDocumentWindowEvents);
//   a clean window takes a newer plan, a dirty one says that a newer revision exists. The polling and the Flow /
//   record events read the Flow's live AI run again too (the lock follows a run started elsewhere).
// - ✎ of a row hands the row to the Step window (T#2) with a session on this window's editor; the Step window is a
//   separate ModelessShell, never a drawer here.
// - flowgate.v02.0073 T#4: the title bar's JSON buttons (client only, no server API): [JSON 가져오기] opens the JSON
//   window in import mode (off while locked or while a Step window holds edits), [JSON 내보내기] saves the draft as a
//   .json file (the body the save would send), [JSON 원문 보기] opens the JSON window read only. One JSON window per
//   plan window; it closes with it. Not the Inspector's document upload / download (DocumentIoGroup, 0068).
// - flowgate.v02.0074 T#1 (R §2.2): the title bar shows no totals and no r{n}; the totals are the 작업 순서 line's
//   (wp-edit-step-totals) only. The revision still drives the save (base_revision_no, 409) and the newer-version banner.
// - flowgate.v02.0074 T#2: the document [업로드] of the plan (any DocumentIoGroup) is this window's [JSON 가져오기]: a request
//   with initialMode 'import' opens the import window once the plan is read; the window tells its dirty state for the
//   plan's .json download.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { documentIoSaveFile } from '@/features/documentWindow/documentIo'
import { useDocumentWindowEvents } from '@/features/documentWindow/useDocumentWindowEvents'
import WorkPlanEditBody from './WorkPlanEditBody.vue'
import WorkPlanJsonWindow from './WorkPlanJsonWindow.vue'
import { workPlanWindowMessages } from './messages'
import { useWorkPlanEditor } from './useWorkPlanEditor'
import { workPlanJsonFileName } from './workPlanJson'
import {
  closeWorkPlanEditWindow,
  discardWorkPlanStepEdits,
  requestWorkPlanStepEdit,
  setWorkPlanEditWindowDirty,
  setWorkPlanEditWindowMinimized,
  workPlanGroupOf,
  workPlanStepEditAvailable,
  workPlanStepEditOpen,
  workPlanStepEditsPending,
  type WorkPlanEditWindowState,
} from './workPlanEditWindows'

const props = defineProps<{
  win: WorkPlanEditWindowState
}>()

const { t } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
const { t: gt, te: gte } = useI18n({ useScope: 'global' })

const viewportWidth = window.innerWidth
const viewportHeight = window.innerHeight
const initialWidth = Math.min(1100, Math.max(320, viewportWidth - 32))
const initialHeight = Math.min(840, Math.max(280, viewportHeight - 64))
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const confirming = ref<'close' | 'reload' | null>(null)

const groupId = () => props.win.request.groupId ?? workPlanGroupOf(props.win.docId)

const editor = useWorkPlanEditor({
  docId: () => props.win.docId,
  groupId,
  stepPending: () => workPlanStepEditsPending(props.win.docId),
  external: () => props.win.request.external?.() ?? null,
  saved: (revisionNo) => props.win.request.saved?.(revisionNo),
  stepOpen: (rowId) => workPlanStepEditOpen(props.win.docId, rowId),
})
const { view, draft, dirty, saving, canSave, notice, pendingQuantity } = editor

/** Phosphor Icons `upload-simple` / `download-simple` / `code` (regular, 256 viewBox; MIT — client/shared/iconData). */
const UPLOAD_PATH =
  'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0ZM93.66,77.66,120,51.31V144a8,8,0,0,0,16,0V51.31l26.34,26.35a8,8,0,0,0,11.32-11.32l-40-40a8,8,0,0,0-11.32,0l-40,40A8,8,0,0,0,93.66,77.66Z'
const DOWNLOAD_PATH =
  'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0Zm-101.66,5.66a8,8,0,0,0,11.32,0l40-40a8,8,0,0,0-11.32-11.32L136,124.69V32a8,8,0,0,0-16,0v92.69L93.66,98.34a8,8,0,0,0-11.32,11.32Z'
const CODE_PATH =
  'M69.12,94.15,28.5,128l40.62,33.85a8,8,0,1,1-10.24,12.29l-48-40a8,8,0,0,1,0-12.29l48-40a8,8,0,0,1,10.24,12.3Zm176,27.7-48-40a8,8,0,1,0-10.24,12.3L227.5,128l-40.62,33.85a8,8,0,1,0,10.24,12.29l48-40a8,8,0,0,0,0-12.29ZM162.73,32.48a8,8,0,0,0-10.25,4.79l-64,176a8,8,0,0,0,4.79,10.26A8.14,8.14,0,0,0,96,224a8,8,0,0,0,7.52-5.27l64-176A8,8,0,0,0,162.73,32.48Z'

/** Lucide `save` (ISC) — [저장]'s mark, as in the confirmed mockup. */
const SAVE_PATH = 'M5 3h11l5 5v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z M7 3v5h8 M7 21v-7h10v7'

/** The JSON window open over this one (import / raw), or null. */
const jsonMode = ref<'import' | 'raw' | null>(null)

/** Why [JSON 가져오기] is off: the plan is locked (or not read), or a Step window holds edits. */
const importBlocked = computed<'locked' | 'step_pending' | null>(() => {
  if (editor.inputsLocked.value) return 'locked'
  return workPlanStepEditsPending(props.win.docId) ? 'step_pending' : null
})

function openImport(): void {
  if (importBlocked.value === null) jsonMode.value = 'import'
}

// flowgate.v02.0074 T#2 (D#1 §6.3): a request with initialMode 'import' (the document [업로드] of this plan) opens [JSON
// 가져오기] once the plan is read (over the raw view too) — once per request, the raise it came with. Blocked then, the
// window stays as it is and its [JSON 가져오기] tooltip says why; a read that failed opens nothing.
let importAsked: number | null = null
watch(
  () => [props.win.request.initialMode, props.win.raise, editor.phase.value, draft.value] as const,
  () => {
    if (props.win.request.initialMode !== 'import' || importAsked === props.win.raise) return
    if (!draft.value) {
      if (editor.phase.value === 'error' || editor.phase.value === 'unreadable') importAsked = props.win.raise
      return
    }
    importAsked = props.win.raise
    if (importBlocked.value === null) jsonMode.value = 'import'
  },
  { immediate: true },
)

// flowgate.v02.0074 T#2: the plan's download (documentIo) says unsaved edits here are not in its file.
watch(dirty, (value) => setWorkPlanEditWindowDirty(props.win.docId, value), { immediate: true })

/** [JSON 내보내기]: the draft as the save would send it, unsaved edits included. */
function exportJson(): void {
  const text = editor.exportJson()
  if (text === null) return
  documentIoSaveFile(new Blob([text], { type: 'application/json;charset=utf-8' }), workPlanJsonFileName(props.win.docId))
}

const docTitle = computed(() => view.value?.title || props.win.request.title || t('title'))
const stateLabel = computed(() => {
  const state = view.value?.docReviewStatus ?? ''
  const key = `gui.document_window.state.${state}`
  return gte(key) ? gt(key) : state
})

function close(): void {
  confirming.value = null
  editor.dispose()
  closeWorkPlanEditWindow(props.win.docId)
}

/** [닫기], × and ESC: unsaved edits (here or in its Step windows) are asked about first. */
function requestClose(): void {
  if (dirty.value || workPlanStepEditsPending(props.win.docId)) confirming.value = 'close'
  else close()
}

function reload(): void {
  confirming.value = null
  discardWorkPlanStepEdits(props.win.docId)
  void editor.load()
}

function requestReload(): void {
  if (dirty.value || workPlanStepEditsPending(props.win.docId)) confirming.value = 'reload'
  else reload()
}

function editStep(rowId: string): void {
  const target = editor.stepTarget(rowId)
  if (!target) return
  requestWorkPlanStepEdit(target, editor.stepSession(target.rowId))
}

useDocumentWindowEvents(
  {
    docIds: () => [props.win.docId],
    shownId: () => props.win.docId,
    groupId,
    projectId: () => props.win.request.projectId,
    paused: () => props.win.minimized,
  },
  {
    content: (change) => {
      // The window's own save already holds that revision.
      if (change.revisionNo !== null && change.revisionNo === view.value?.revisionNo) return
      void editor.refresh()
    },
    // The Flow's refresh, a record change and the polling: the Flow's AI run may have started or ended.
    record: () => editor.refreshActivity(),
  },
)

// A second request for the same document brings the window back to the front.
watch(
  () => props.win.raise,
  () => shell.value?.bringToFront(),
)

// The v0.2 document window's save (✎ text edit) and upload say so on the window event.
function onContentChanged(event: Event): void {
  const docId = (event as CustomEvent<{ doc_id?: string }>).detail?.doc_id
  if (!docId || docId === props.win.docId) void editor.refresh()
}

onMounted(() => {
  void editor.load()
  window.addEventListener('fg:document_content_changed', onContentChanged)
})

onBeforeUnmount(() => window.removeEventListener('fg:document_content_changed', onContentChanged))
</script>

<style scoped>
.wp-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.wp-window-tile {
  display: grid;
  flex: none;
  place-items: center;
  width: 25px;
  height: 25px;
  border-radius: var(--fg2-control-radius-sm);
  background: color-mix(in srgb, var(--fg2-cyan) 22%, transparent);
  color: var(--fg2-cyan);
}

.wp-window-tile svg,
.wp-window-save svg {
  width: 15px;
  height: 15px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.7;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.wp-window-save {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.wp-window-kind {
  font-weight: 700;
}

.wp-window-kind,
.wp-window-state,
.wp-window-dirty {
  flex: 0 0 auto;
}

.wp-window-state {
  color: var(--fg2-muted);
  font-size: 11px;
  font-weight: 500;
}

.wp-window-dirty {
  color: var(--fg2-status-warning-fg);
  font-size: 10px;
}

.wp-window-json {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}

.wp-window-json svg {
  width: 15px;
  height: 15px;
}

.wp-window-divider {
  width: 1px;
  height: 16px;
  margin: 0 4px;
  background: var(--fg2-line);
}

.wp-window {
  display: flex;
  flex-direction: column;
  gap: 10px;
  height: 100%;
  min-height: 0;
}

.wp-window-scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
}

.wp-window-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--fg2-line);
}

.wp-window-notice {
  flex: 1;
  min-width: 0;
  margin: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wp-window-notice.is-unsaved {
  display: flex;
  align-items: center;
  gap: 7px;
}

.wp-window-notice.is-unsaved::before {
  content: '';
  flex: none;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--fg2-status-warning-fg);
}

.wp-window-notice.is-success {
  color: var(--fg2-status-success-fg);
}

.wp-window-notice.is-warning {
  color: var(--fg2-status-warning-fg);
}
</style>
