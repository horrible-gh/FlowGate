<template>
  <ModelessShell
    ref="shell"
    :open="true"
    :initial-x="win.x"
    :initial-y="win.y"
    :initial-width="680"
    :initial-height="560"
    :min-width="360"
    :min-height="240"
    :minimized="win.minimized"
    @update:minimized="(value: boolean) => setDocumentWindowMinimized(win.docId, value)"
    @close="requestClose"
  >
    <template #header>
      <span class="document-window-heading" data-test="document-window-heading">
        <span class="document-window-title" :title="title" data-test="document-window-title">{{ title }}</span>
        <span
          class="document-window-dot"
          :class="`document-window-dot--${dot.tone}`"
          role="img"
          :aria-label="t(k(`state.${dot.key}`))"
          :title="t(k(`state.${dot.key}`))"
          data-test="document-window-dot"
          :data-tone="dot.tone"
        />
      </span>
    </template>
    <template #actions>
      <!-- flowgate.v02.0068 T#1 — [업로드][다운로드] │ before the copy / edit icon; hidden while minimized. -->
      <DocumentIoGroup v-if="!win.minimized" :doc-id="ioDocId" :project-id="record?.projectId ?? chain[0]?.projectId" :group-id="record?.groupId ?? chain[0]?.groupId" />
      <span v-if="!editing" ref="copyTarget" class="document-window-copy-action" />
      <IconButton
        v-else
        :label="t(k('edit'))"
        :title="t(k('edit'))"
        variant="ghost"
        size="sm"
        :disabled="editing || gone"
        :aria-pressed="editing"
        data-test="document-window-edit"
        @mousedown.stop
        @click="startEdit"
      >✎</IconButton>
    </template>

    <div class="document-window" data-test="document-window" :data-doc="win.docId" :data-shown="win.shown" :data-mode="win.mode">
      <div v-if="gone" class="document-window-note" data-test="document-window-gone">{{ t(k('error.gone')) }}</div>

      <!-- The body view depends on the document's type (T#4): it is chosen once the title line read answered. -->
      <div v-else-if="!editing && !recordSettled" class="document-window-note" data-test="document-window-loading">{{ t(k('viewer.loading')) }}</div>
      <DocumentWindowBody v-else-if="!editing" ref="body" :doc-id="win.shown" :type-code="record?.typeCode ?? null" :title="record?.title ?? null" :copy-target="copyTarget" :can-reenter="canReenter" @reenter="startEdit" />

      <div v-else class="document-window-editor" data-test="document-window-editor">
        <div v-if="editLoading" class="document-window-note" data-test="document-window-editor-loading">{{ t(k('viewer.loading')) }}</div>
        <div v-else-if="editLoadErrorKey" class="document-window-note document-window-note--danger" data-test="document-window-editor-error">
          <span>{{ t(k(`error.${editLoadErrorKey}`)) }}</span>
          <IconButton :label="t(k('viewer.retry'))" :title="t(k('viewer.retry'))" variant="secondary" size="sm" data-test="document-window-editor-retry" @click="loadEditor">↻</IconButton>
        </div>
        <textarea
          v-else
          ref="editorRef"
          v-model="draft"
          class="document-window-textarea"
          spellcheck="false"
          :aria-label="t(k('editor'))"
          :disabled="saving"
          :readonly="guardedHere"
          :aria-readonly="guardedHere ? 'true' : undefined"
          data-test="document-window-textarea"
          @keydown.ctrl.s.prevent="save"
          @keydown.meta.s.prevent="save"
        />
        <div class="document-window-editor-bar">
          <span
            v-if="saveErrorKey"
            class="document-window-save-error"
            :title="t(saveErrorKey === 'file_exists_unrecorded' ? 'gui.document.bodyState.file_exists_unrecorded' : k(`error.${saveErrorKey}`))"
            data-test="document-window-save-error"
            :data-key="saveErrorKey"
          >{{ t(saveErrorKey === 'file_exists_unrecorded' ? 'gui.document.bodyState.file_exists_unrecorded' : k(`error.${saveErrorKey}`)) }}</span>
          <!-- flowgate.v02.0056 F1 — [닫기] closes the window from the editor (the same path as × / Esc: unsaved
               text is asked about once). 0017-CH: [취소] · [저장] · [닫기] at the right end, the card memo window's
               buttons (bordered [취소] / [닫기], blue [저장]); [취소] takes a soft amber tone (it drops the edit). -->
          <Button
            class="document-window-cancel"
            size="sm"
            variant="secondary"
            :disabled="saving"
            data-test="document-window-cancel"
            @click="requestCancel"
          >{{ t(k('cancel')) }}</Button>
          <Button
            size="sm"
            variant="primary"
            :disabled="editLoading || editLoadErrorKey !== null || guardedHere"
            :loading="saving"
            :title="guardedHere ? t('gui.document_io.disabled.uploading') : undefined"
            data-test="document-window-save"
            @click="save"
          >{{ t(k('save')) }}</Button>
          <Button size="sm" variant="secondary" :disabled="saving" data-test="document-window-close" @click="requestClose">{{ t(k('close')) }}</Button>
        </div>
      </div>
      <div v-if="!editing" class="document-window-viewer-bar">
        <Button
          size="sm"
          variant="primary"
          :disabled="gone || guardedHere"
          :title="guardedHere ? t('gui.document_io.disabled.uploading') : undefined"
          data-test="document-window-edit"
          @click="startEdit"
        >{{ t(k('edit')) }}</Button>
        <Button size="sm" variant="secondary" data-test="document-window-close" @click="requestClose">{{ t(k('close')) }}</Button>
      </div>
    </div>
  </ModelessShell>

  <ConfirmDialog
    :open="confirming !== null"
    :title="t(k('discard.title'))"
    :confirm-label="t(k('discard.confirm'))"
    :cancel-label="t(k('discard.keep'))"
    danger
    @confirm="confirmDiscard"
    @cancel="confirming = null"
  />
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#3 — one document's modeless window (ModelessShell, the Q&A window's look): the
// title line is name · state dot · copy · minimize · close, the body is the document's view
// (DocumentWindowBody → MD Viewer; T#4: a work plan / test scenario / test report its own view). The viewer
// footer's edit button turns the same window to a text editor of the body (the
// frontmatter is kept aside and put back, as the v0.1 editor does); 저장 writes it with the v0.1
// content save and goes back to the view, 취소 drops it. Unsaved text is asked about once — on 취소,
// on close and on Esc. flowgate.v02.0056 F1: the editor bar also has [닫기] (0056 CH §5), the window's
// close by button — the same path as × and Esc, so the same question about unsaved text. A document whose file is not written yet opens empty and edits the same way.
// The window follows the server (T#3 rework): its document's events on the v0.2 project event hub
// (useDocumentWindowEvents) read the body and the title line again, a reconnect reads both, and a second
// open request (↗ / ✎ again) reads both as well; while open it also reads both on a bounded period, for
// writes that send no event (the v0.1 content save from another tab). Unsaved text in the editor is never
// replaced.
// A run result is a new document that supersedes the card's empty report (rework 2): every title line read
// follows the opened document's supersede chain (readDocumentChain) and the window then shows, edits and
// saves the end of it (`win.shown`) — unless its editor holds unsaved text, which keeps its document until
// it is saved or dropped. Two windows never show the same document (followDocumentWindow).
// flowgate.v02.0068 T#1 — the header's [업로드][다운로드] (DocumentIoGroup) act on the document shown, or the one the
// editor edits. The window registers its edit state with the registry (documentEditRegistry, L §2.11 / §2.13):
// while its document is guarded by an upload, [편집] and [저장] are disabled and an open editor is read only; an
// editor read pending when the guard starts is a late answer, and an editor read asked for during the guard waits
// for its end; the upload's reflect reads the view's body, or a clean editor's frontmatter, base and text, from
// the new stored body. The read starts in the same turn and the editor shows "불러오는 중" (no input, no save)
// until it answers — also when the upload stopped waiting for it.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { normalizeApiInput } from '@shared/apiErrors'
import { useCurrentUser } from '@/core/auth/currentUser'
import {
  documentContentRead,
  documentContentSave,
  documentErrorOf,
  documentRead,
  type DocumentRecord,
} from '@/features/document'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DocumentIoGroup from './DocumentIoGroup.vue'
import DocumentWindowBody from './DocumentWindowBody.vue'
import { documentIoGuarded, registerDocumentEditEntry, type DocumentEditorState, type DocumentReflectResult } from './documentEditRegistry'
import {
  documentDot,
  documentReadErrorKey,
  documentSaveErrorKey,
  joinDocumentSource,
  splitDocumentSource,
} from './documentWindowModel'
import {
  closeDocumentWindow,
  followDocumentWindow,
  setDocumentWindowMinimized,
  setDocumentWindowMode,
  type DocumentWindowState,
} from './documentWindows'
import { readDocumentChain } from './documentChain'
import { useDocumentWindowEvents, type DocumentContentChange } from './useDocumentWindowEvents'

const props = defineProps<{
  win: DocumentWindowState
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.${key}`

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const editorRef = ref<HTMLTextAreaElement | null>(null)
const copyTarget = ref<HTMLElement | null>(null)
const body = ref<InstanceType<typeof DocumentWindowBody> | null>(null)

// --- The document (title line) ---------------------------------------------------------------------

/** The opened document and the documents its supersede chain reached (the last is the live one). */
const chain = ref<DocumentRecord[]>([])
const gone = ref(false)
/** The first title line read answered (either way): the body can pick its view. */
const recordSettled = ref(false)
// Request order: only the latest read of the title line may set it.
let recordSequence = 0

async function readRecord(): Promise<void> {
  const docId = props.win.docId
  const seq = ++recordSequence
  try {
    const read = await readDocumentChain(docId, documentRead)
    if (seq !== recordSequence) return
    chain.value = read
    gone.value = false
    recordSettled.value = true
  } catch (error) {
    if (seq !== recordSequence) return
    // Only a document that no longer exists replaces the body; any other failure keeps the window.
    gone.value = documentReadErrorKey(documentErrorOf(error).kind) === 'gone'
    recordSettled.value = true
  }
}

/** The title line is the shown document's. */
const record = computed(() => chain.value.find((item) => item.docId === props.win.shown) ?? chain.value[0] ?? null)
const { user } = useCurrentUser()
const canReenter = computed(() => !!user.value && record.value?.isEditable === true && !record.value?.groupDisposed && record.value?.typeCode !== 'TR2')
const liveDocId = computed(() => chain.value[chain.value.length - 1]?.docId ?? null)

const title = computed(() => record.value?.title || t(k('untitled')))
const dot = computed(() => documentDot(record.value))

// --- Editing ---------------------------------------------------------------------------------------

const editing = computed(() => props.win.mode === 'edit')
const frontmatter = ref('')
const loadedBody = ref('')
const draft = ref('')
const editLoading = ref(false)
const editLoadErrorKey = ref<string | null>(null)
const saving = ref(false)
const saveErrorKey = ref<string | null>(null)
/** The document the editor's text was read from (and is saved to). */
const editDocId = ref(props.win.shown)
let editSequence = 0
/** An editor read was deferred or dropped for an upload guard: read once the guard ends. */
let needReload = false

/** What the upload buttons act on: the edited document while editing, else the one shown. */
const ioDocId = computed(() => (editing.value ? editDocId.value : props.win.shown))
/** An upload guards this window's document (or a save the previous Designer sent has not answered). */
const guardedHere = computed(() => documentIoGuarded(ioDocId.value))

const dirty = computed(() => editing.value && !editLoading.value && editLoadErrorKey.value === null && draft.value !== loadedBody.value)

function resetEditor(): void {
  editSequence += 1
  needReload = false
  frontmatter.value = ''
  loadedBody.value = ''
  draft.value = ''
  editLoading.value = false
  editLoadErrorKey.value = null
  saving.value = false
  saveErrorKey.value = null
}

/**
 * Reads the stored body fresh for the editor (the v0.1 editor reads it again on every open). While the shown
 * document is guarded the read waits for the guard's end (the editor shows "불러오는 중").
 */
async function loadEditor(): Promise<void> {
  if (documentIoGuarded(props.win.shown)) {
    editSequence += 1
    editDocId.value = props.win.shown
    editLoading.value = true
    needReload = true
    return
  }
  await readEditor(true)
}

/**
 * The editor read itself. From its first line the editor shows "불러오는 중" (no input, save disabled, not
 * dirty); only the latest read may fill it: frontmatter, base and text together, clean.
 */
async function readEditor(focus: boolean): Promise<'ok' | 'failed' | 'superseded'> {
  const seq = ++editSequence
  const docId = props.win.shown
  editDocId.value = docId
  editLoading.value = true
  editLoadErrorKey.value = null
  saveErrorKey.value = null
  try {
    const read = await documentContentRead(docId)
    if (seq !== editSequence) return 'superseded'
    const source = splitDocumentSource(read.content)
    frontmatter.value = source.frontmatter
    loadedBody.value = source.body
    draft.value = source.body
    editLoading.value = false
    if (focus) {
      await nextTick()
      editorRef.value?.focus()
    }
    return 'ok'
  } catch (error) {
    if (seq !== editSequence) return 'superseded'
    editLoading.value = false
    editLoadErrorKey.value = documentReadErrorKey(documentErrorOf(error).kind)
    return 'failed'
  }
}

function startEdit(): void {
  if (gone.value || guardedHere.value) return
  setDocumentWindowMode(props.win.docId, 'edit')
}

function toView(): void {
  resetEditor()
  setDocumentWindowMode(props.win.docId, 'view')
}

async function save(): Promise<void> {
  if (!editing.value || saving.value || editLoading.value || editLoadErrorKey.value !== null || guardedHere.value) return
  const docId = editDocId.value
  saving.value = true
  saveErrorKey.value = null
  try {
    await documentContentSave(docId, joinDocumentSource({ frontmatter: frontmatter.value, body: draft.value }))
    saving.value = false
    toView()
    // The content save sends no server event; notify the Inspector using the document actually saved.
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId } }))
    void readRecord()
  } catch (error) {
    saving.value = false
    const read = documentErrorOf(error)
    saveErrorKey.value = read.status === 409 && normalizeApiInput(error).data.detail === 'file_exists_unrecorded'
      ? 'file_exists_unrecorded'
      : documentSaveErrorKey(read.kind, read.status)
  }
}

// --- Leaving with unsaved text ---------------------------------------------------------------------

/** What the open discard question was asked for, or null when none is open. */
const confirming = ref<'cancel' | 'close' | null>(null)

function requestCancel(): void {
  if (saving.value) return
  if (dirty.value) confirming.value = 'cancel'
  else toView()
}

function requestClose(): void {
  if (saving.value || confirming.value !== null) return
  if (dirty.value) confirming.value = 'close'
  else closeDocumentWindow(props.win.docId)
}

function confirmDiscard(): void {
  const asked = confirming.value
  confirming.value = null
  if (asked === 'close') closeDocumentWindow(props.win.docId)
  else if (asked === 'cancel') toView()
}

// --- Following the server ---------------------------------------------------------------------------

/** The view reads the body again; an open editor keeps its text (it is read fresh on the next ✎). */
function refreshBody(change: DocumentContentChange): void {
  if (!editing.value) body.value?.refresh(change)
}

useDocumentWindowEvents(
  {
    docIds: () => [...new Set([props.win.docId, props.win.shown, ...chain.value.map((item) => item.docId)])],
    shownId: () => props.win.shown,
    groupId: () => chain.value[0]?.groupId ?? null,
    projectId: () => chain.value[0]?.projectId ?? null,
    paused: () => props.win.minimized,
  },
  {
    content: refreshBody,
    record: () => void readRecord(),
  },
)

// Unsaved (or saving) editor text keeps the document it was read from; anything else follows the chain.
const holdShown = computed(() => editing.value && (dirty.value || saving.value))

// The chain reached another document: show it (the body reads it as its id changes), or give way to the
// window that already has it.
watch([liveDocId, holdShown], ([live, hold]) => {
  if (live === null || hold || live === props.win.shown) return
  followDocumentWindow(props.win.docId, live, props.win.mode)
})

// A clean editor reads the document it now stands for.
watch(
  () => props.win.shown,
  () => {
    if (editing.value && !holdShown.value) void loadEditor()
  },
)

// --- Upload guard and reflect (flowgate.v02.0068 T#1) -------------------------------------------------

function editorState(): DocumentEditorState {
  if (!editing.value) return 'none'
  if (editLoading.value) return 'loading'
  return editLoadErrorKey.value !== null ? 'error' : 'ready'
}

/** The upload asks this window to show the new stored body (its read starts before this returns). */
function reflect(docId: string): Promise<DocumentReflectResult> {
  if (editing.value && (dirty.value || saving.value)) {
    // The guard keeps a window of the document clean; unsaved text is never replaced.
    console.warn('[documentIo] reflect skipped: the editor holds unsaved text', docId)
    return Promise.resolve('skipped')
  }
  if (editing.value) {
    needReload = false
    return readEditor(false).then((result) => (result === 'superseded' ? 'skipped' : result))
  }
  body.value?.refresh({ docId: props.win.shown, revisionNo: null })
  void readRecord()
  return Promise.resolve('ok')
}

const unregisterEdit = registerDocumentEditEntry({
  related: () => (editing.value ? [...new Set([props.win.shown, editDocId.value])] : [props.win.shown]),
  editor: editorState,
  dirty: () => dirty.value,
  saving: () => saving.value,
  onGuard: () => {
    // A surviving clean editor must read after release even if the Designer leaves during saving.
    // Its old text stays read-only until release starts the deferred read synchronously.
    if (!editing.value || dirty.value || saving.value) return
    if (editLoading.value) editSequence += 1
    needReload = true
  },
  onRelease: () => {
    if (!needReload || documentIoGuarded(ioDocId.value)) return
    needReload = false
    if (!editing.value || dirty.value || saving.value) return
    void loadEditor()
  },
  reflect,
})

onBeforeUnmount(unregisterEdit)

// --- Lifecycle -------------------------------------------------------------------------------------

watch(
  () => props.win.mode,
  (mode, previous) => {
    if (mode === 'edit' && previous !== 'edit') void loadEditor()
  },
)

// Every open request for this document (a second ↗ / ✎) brings the window back to the front and reads
// the document again (what it shows may be older than the server).
watch(
  () => props.win.raise,
  () => {
    shell.value?.bringToFront()
    void readRecord()
    refreshBody({ docId: props.win.shown, revisionNo: null })
  },
)

onMounted(() => {
  void readRecord()
  if (editing.value) void loadEditor()
})

defineExpose({ dirty })
</script>

<style scoped>
.document-window-heading {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  max-width: 100%;
}

.document-window-copy-action {
  display: inline-flex;
  align-items: center;
}

.document-window-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.document-window-dot {
  flex: 0 0 auto;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--fg2-muted-2);
}

.document-window-dot--info {
  background: var(--fg2-status-info-fg);
}

.document-window-dot--success {
  background: var(--fg2-status-success-fg);
}

.document-window-dot--warning {
  background: var(--fg2-status-warning-fg);
}

.document-window-dot--danger {
  background: var(--fg2-status-danger-fg);
}

/* The shell body pads and scrolls; the window fills it so the viewer and the editor own their scroll.
   The height adds back the body's 12px top and bottom padding the margins cancel, so the window ends at
   the shell's bottom edge (the editor bar's space below the buttons equals the space above them). */
.document-window {
  display: flex;
  flex-direction: column;
  height: calc(100% + 24px);
  min-height: 0;
  margin: -12px -14px;
}

.document-window-note {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 16px 18px;
  color: var(--fg2-muted);
  font-size: 13px;
}

.document-window-note--danger {
  color: var(--fg2-status-danger-fg);
}

.document-window-editor {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.document-window-textarea {
  flex: 1;
  min-height: 0;
  margin: 0;
  padding: 14px 18px;
  border: none;
  outline: none;
  resize: none;
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12.5px;
  line-height: 1.6;
  tab-size: 2;
}

.document-window-textarea:focus-visible {
  box-shadow: inset 0 0 0 1px var(--fg2-focus);
}

.document-window-editor-bar,
.document-window-viewer-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 12px;
  border-top: 1px solid var(--fg2-line);
}

.document-window-viewer-bar {
  margin-top: auto;
}

.document-window-editor-bar .document-window-cancel {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.document-window-editor-bar .document-window-cancel:hover:not(:disabled) {
  border-color: var(--fg2-status-warning-fg);
  color: var(--fg2-status-warning-fg);
}

.document-window-save-error {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  color: var(--fg2-status-danger-fg);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
