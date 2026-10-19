<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="820"
    :initial-height="640"
    :min-width="560"
    :min-height="320"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setWorkPlanEditWindowMinimized(win.docId, value)"
    @close="requestClose"
  >
    <template #header>
      <span class="wp-window-heading" data-test="wp-edit-heading">
        <span class="wp-window-kind">{{ t('title') }}</span>
        <span class="wp-window-doc" :title="docTitle">{{ docTitle }}</span>
        <span v-if="view" class="wp-window-rev" data-test="wp-edit-revision">{{ t('revision', { n: view.revisionNo }) }}</span>
        <span v-if="view?.docReviewStatus" class="wp-window-state" data-test="wp-edit-state">{{ stateLabel }}</span>
        <span v-if="dirty" class="wp-window-dirty" :title="t('dirty')" data-test="wp-edit-dirty">●</span>
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
        >{{ t(`notice.${notice.key}`, { ...(notice.params ?? {}) }) }}</p>
        <p v-else-if="workPlanStepEditsPending(win.docId)" class="wp-window-notice" data-test="wp-edit-child-pending">{{ t('step_pending') }}</p>
        <p v-else-if="dirty" class="wp-window-notice" data-test="wp-edit-unsaved">{{ t('dirty') }}</p>
        <Button size="sm" variant="ghost" data-test="wp-edit-close" @click="requestClose">{{ t('close') }}</Button>
        <Button size="sm" variant="primary" :disabled="!canSave" :loading="saving" data-test="wp-edit-save" @click="editor.save()">
          {{ saving ? t('saving') : t('save') }}
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
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { useDocumentWindowEvents } from '@/features/documentWindow/useDocumentWindowEvents'
import WorkPlanEditBody from './WorkPlanEditBody.vue'
import { workPlanWindowMessages } from './messages'
import { useWorkPlanEditor } from './useWorkPlanEditor'
import {
  closeWorkPlanEditWindow,
  discardWorkPlanStepEdits,
  requestWorkPlanStepEdit,
  setWorkPlanEditWindowMinimized,
  workPlanGroupOf,
  workPlanStepEditAvailable,
  workPlanStepEditsPending,
  type WorkPlanEditWindowState,
} from './workPlanEditWindows'

const props = defineProps<{
  win: WorkPlanEditWindowState
}>()

const { t } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
const { t: gt, te: gte } = useI18n({ useScope: 'global' })

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const confirming = ref<'close' | 'reload' | null>(null)

const groupId = () => props.win.request.groupId ?? workPlanGroupOf(props.win.docId)

const editor = useWorkPlanEditor({
  docId: () => props.win.docId,
  groupId,
  stepPending: () => workPlanStepEditsPending(props.win.docId),
  external: () => props.win.request.external?.() ?? null,
  saved: (revisionNo) => props.win.request.saved?.(revisionNo),
})
const { view, dirty, saving, canSave, notice, pendingQuantity } = editor

const docTitle = computed(() => view.value?.title || props.win.request.title || props.win.docId)
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

.wp-window-kind,
.wp-window-rev,
.wp-window-state,
.wp-window-dirty {
  flex: 0 0 auto;
}

.wp-window-doc {
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.wp-window-rev,
.wp-window-state {
  color: var(--fg2-muted);
  font-size: 11px;
  font-weight: 500;
}

.wp-window-dirty {
  color: var(--fg2-status-warning-fg);
  font-size: 10px;
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

.wp-window-notice.is-success {
  color: var(--fg2-status-success-fg);
}

.wp-window-notice.is-warning {
  color: var(--fg2-status-warning-fg);
}
</style>
