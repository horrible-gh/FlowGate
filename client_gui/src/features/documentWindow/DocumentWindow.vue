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
      <IconButton
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

      <DocumentWindowBody v-else-if="!editing" ref="body" :doc-id="win.shown" :type-code="record?.typeCode ?? null" />

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
          data-test="document-window-textarea"
          @keydown.ctrl.s.prevent="save"
          @keydown.meta.s.prevent="save"
        />
        <div class="document-window-editor-bar">
          <span
            v-if="saveErrorKey"
            class="document-window-save-error"
            :title="t(k(`error.${saveErrorKey}`))"
            data-test="document-window-save-error"
            :data-key="saveErrorKey"
          >{{ t(k(`error.${saveErrorKey}`)) }}</span>
          <Button size="sm" variant="ghost" :disabled="saving" data-test="document-window-cancel" @click="requestCancel">{{ t(k('cancel')) }}</Button>
          <Button
            size="sm"
            variant="primary"
            :disabled="editLoading || editLoadErrorKey !== null"
            :loading="saving"
            data-test="document-window-save"
            @click="save"
          >{{ t(k('save')) }}</Button>
        </div>
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
// title line is name · state dot · ✎ · minimize · close, the body is the document's view
// (DocumentWindowBody → MD Viewer). ✎ turns the same window to a text editor of the body (the
// frontmatter is kept aside and put back, as the v0.1 editor does); 저장 writes it with the v0.1
// content save and goes back to the view, 취소 drops it. Unsaved text is asked about once — on 취소,
// on close and on Esc. A document whose file is not written yet opens empty and edits the same way.
// The window follows the server (T#3 rework): its document's events on the v0.2 project event hub
// (useDocumentWindowEvents) read the body and the title line again, a reconnect reads both, and a second
// open request (↗ / ✎ again) reads both as well; while open it also reads both on a bounded period, for
// writes that send no event (the v0.1 content save from another tab). Unsaved text in the editor is never
// replaced.
// A run result is a new document that supersedes the card's empty report (rework 2): every title line read
// follows the opened document's supersede chain (readDocumentChain) and the window then shows, edits and
// saves the end of it (`win.shown`) — unless its editor holds unsaved text, which keeps its document until
// it is saved or dropped. Two windows never show the same document (followDocumentWindow).
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
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
import DocumentWindowBody from './DocumentWindowBody.vue'
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
const body = ref<InstanceType<typeof DocumentWindowBody> | null>(null)

// --- The document (title line) ---------------------------------------------------------------------

/** The opened document and the documents its supersede chain reached (the last is the live one). */
const chain = ref<DocumentRecord[]>([])
const gone = ref(false)
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
  } catch (error) {
    if (seq !== recordSequence) return
    // Only a document that no longer exists replaces the body; any other failure keeps the window.
    gone.value = documentReadErrorKey(documentErrorOf(error).kind) === 'gone'
  }
}

/** The title line is the shown document's. */
const record = computed(() => chain.value.find((item) => item.docId === props.win.shown) ?? chain.value[0] ?? null)
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
let editDocId = props.win.shown
let editSequence = 0

const dirty = computed(() => editing.value && !editLoading.value && editLoadErrorKey.value === null && draft.value !== loadedBody.value)

function resetEditor(): void {
  editSequence += 1
  frontmatter.value = ''
  loadedBody.value = ''
  draft.value = ''
  editLoading.value = false
  editLoadErrorKey.value = null
  saving.value = false
  saveErrorKey.value = null
}

/** Reads the stored body fresh for the editor (the v0.1 editor reads it again on every open). */
async function loadEditor(): Promise<void> {
  const seq = ++editSequence
  const docId = props.win.shown
  editDocId = docId
  editLoading.value = true
  editLoadErrorKey.value = null
  saveErrorKey.value = null
  try {
    const read = await documentContentRead(docId)
    if (seq !== editSequence) return
    const source = splitDocumentSource(read.content)
    frontmatter.value = source.frontmatter
    loadedBody.value = source.body
    draft.value = source.body
    editLoading.value = false
    await nextTick()
    editorRef.value?.focus()
  } catch (error) {
    if (seq !== editSequence) return
    editLoading.value = false
    editLoadErrorKey.value = documentReadErrorKey(documentErrorOf(error).kind)
  }
}

function startEdit(): void {
  if (gone.value) return
  setDocumentWindowMode(props.win.docId, 'edit')
}

function toView(): void {
  resetEditor()
  setDocumentWindowMode(props.win.docId, 'view')
}

async function save(): Promise<void> {
  if (!editing.value || saving.value || editLoading.value || editLoadErrorKey.value !== null) return
  const docId = editDocId
  saving.value = true
  saveErrorKey.value = null
  try {
    await documentContentSave(docId, joinDocumentSource({ frontmatter: frontmatter.value, body: draft.value }))
    saving.value = false
    toView()
    void readRecord()
  } catch (error) {
    saving.value = false
    const read = documentErrorOf(error)
    saveErrorKey.value = documentSaveErrorKey(read.kind, read.status)
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

/* The shell body pads and scrolls; the window fills it so the viewer and the editor own their scroll. */
.document-window {
  display: flex;
  flex-direction: column;
  height: 100%;
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
  border-bottom: 1px solid var(--fg2-line);
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

.document-window-editor-bar {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 8px 12px;
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
