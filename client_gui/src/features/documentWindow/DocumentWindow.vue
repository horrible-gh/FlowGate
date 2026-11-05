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
    @update:minimized="(value: boolean) => setDocumentWindowMinimized(win.id, value)"
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
      <DocumentIoGroup v-if="!win.minimized" :doc-id="ioDocId" :project-id="record?.projectId ?? chain[0]?.projectId" :group-id="record?.groupId ?? chain[0]?.groupId" :card="ioCard" />
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

    <div
      class="document-window"
      data-test="document-window"
      :data-doc="win.docId"
      :data-shown="win.shown"
      :data-mode="win.mode"
      :data-exact="win.exact ? 'true' : undefined"
    >
      <div v-if="gone" class="document-window-note" data-test="document-window-gone">{{ t(k('error.gone')) }}</div>

      <!-- The body view depends on the document's type (T#4): it is chosen once the title line read answered. -->
      <div v-else-if="!editing && !recordSettled" class="document-window-note" data-test="document-window-loading">{{ t(k('viewer.loading')) }}</div>
      <DocumentWindowBody v-else-if="!editing" ref="body" :doc-id="win.shown" :type-code="record?.typeCode ?? null" :title="record?.title ?? null" :copy-target="copyTarget" :can-reenter="canReenter" />

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
        <!-- flowgate.v02.0127 T#5 (§2.12): a conflict keeps the draft and base; the latest document is shown apart and
             adopted only by the user's explicit [병합본으로 채택] of the text merged by hand. -->
        <div v-if="conflicted" class="document-window-conflict" data-test="document-window-conflict">
          <span class="document-window-conflict-title">{{ t(k('conflict.latest')) }}</span>
          <textarea class="document-window-latest" readonly :value="latestText" :aria-label="t(k('conflict.latest'))" data-test="document-window-latest" />
          <Button size="sm" variant="secondary" :disabled="session.latest === null" data-test="document-window-adopt-merge" @click="adoptMergedDraft">{{ t(k('conflict.adopt')) }}</Button>
        </div>
        <div v-if="blockedWithBase" class="document-window-conflict" data-test="document-window-blocked">
          <span class="document-window-conflict-title">{{ t(k('conflict.latest')) }}</span>
          <textarea v-if="latestReadable" class="document-window-latest" readonly :value="latestText" :aria-label="t(k('conflict.latest'))" data-test="document-window-blocked-latest" />
          <Button size="sm" variant="secondary" data-test="document-window-reread" @click="rereadLatest">{{ t(k('conflict.reread')) }}</Button>
          <Button size="sm" variant="secondary" :disabled="!latestReadable" data-test="document-window-adopt-blocked" @click="adoptMergedDraft">{{ t(k('conflict.adopt')) }}</Button>
        </div>
        <div v-if="needsRecovery" class="document-window-conflict" data-test="document-window-recovery">
          <Button v-if="session.configBlocked" size="sm" variant="secondary" data-test="document-window-config-corrected" @click="correctConfig">{{ t(k('conflict.config_corrected')) }}</Button>
          <Button v-if="session.needsNewIntent" size="sm" variant="secondary" data-test="document-window-new-intent" @click="startNewSaveIntent">{{ t(k('conflict.new_intent')) }}</Button>
        </div>
        <div v-if="remoteChanged && !conflicted" class="document-window-note document-window-remote" data-test="document-window-remote-changed">{{ t(k('remote_changed')) }}</div>
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
            :disabled="editLoading || editLoadErrorKey !== null || guardedHere || saveBlocked"
            :loading="saving"
            :title="guardedHere ? t('gui.document_io.disabled.uploading') : undefined"
            data-test="document-window-save"
            @click="save"
          >{{ t(k('save')) }}</Button>
          <Button size="sm" variant="secondary" :disabled="saving" data-test="document-window-close" @click="requestClose">{{ t(k('close')) }}</Button>
        </div>
      </div>
      <!-- flowgate.v02.0070 T#4 (B4): an exact window's part from its opener (the old document's Q&A history). -->
      <div v-if="win.aside && !editing && recordSettled && !gone" class="document-window-aside" data-test="document-window-aside">
        <component :is="win.aside.component" v-bind="win.aside.props ?? {}" />
      </div>
      <!-- flowgate.v02.0070 T#3 (B3): the view's one action group, by the body's state (documentViewerActions) —
           unreadable: [복구]? [본문 다시 입력] [닫기]; readable or empty: [편집] [닫기]; not editable: [닫기].
           flowgate.v02.0068 T#1: while an upload guards the document, [복구] · [본문 다시 입력] · [편집] are disabled. -->
      <div v-if="!editing" class="document-window-viewer-bar" data-test="document-window-viewer-bar">
        <Button
          v-if="viewerActions.includes('recover')"
          size="sm"
          variant="secondary"
          :loading="bodyStatus?.recovering === true"
          :disabled="guardedHere"
          :title="guardedHere ? t('gui.document_io.disabled.uploading') : undefined"
          data-test="document-window-recover"
          @click="recover"
        >{{ t('gui.document.recover.action') }}</Button>
        <Button
          v-if="viewerActions.includes('reenter')"
          size="sm"
          variant="primary"
          :disabled="guardedHere"
          :title="guardedHere ? t('gui.document_io.disabled.uploading') : undefined"
          data-test="document-window-reenter"
          @click="startEdit"
        >{{ t('gui.document.bodyState.reenter') }}</Button>
        <Button
          v-if="viewerActions.includes('edit')"
          size="sm"
          variant="primary"
          :disabled="guardedHere"
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
// flowgate.v02.0070 T#4 (B4): an exact window (`win.exact`, the 「전환 전 문서」 link) reads only its own document — no
// supersede chain, so the old id stays the shown one — and is read only: the footer has [닫기] alone (the T#3 rule
// for a person who may not edit), the body diagnoses nothing to recover, and the editor never opens. Its opener's
// part (`win.aside`) sits under the body.
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import {
  cardDocumentErrorOf,
  cardDocumentRead,
  cardDocumentRevise,
  documentErrorOf,
  documentRead,
  type DocumentRecord,
} from '@/features/document'
import {
  adoptMerge,
  baseRead,
  baseReadFailed,
  beginLoad,
  beginSave,
  configCorrected,
  conflictLatestRead,
  discard as discardSession,
  externalEvent,
  hasUnresolvedRequest,
  hasUnsaved,
  isEditBlocked,
  latestRead,
  newIdempotencyKey,
  newSession,
  requestOf,
  saveAnswered,
  saveFailed,
  startNewIntent as startNewIntentSession,
  typeDraft,
  type EditSession,
} from '@/features/document/cardEditSession'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import DocumentIoGroup from './DocumentIoGroup.vue'
import DocumentWindowBody from './DocumentWindowBody.vue'
import { documentIoGuarded, documentIoHasUnresolved, registerDocumentEditEntry, type DocumentEditorState, type DocumentReflectResult } from './documentEditRegistry'
import {
  documentDot,
  documentReadErrorKey,
  documentViewerActions,
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
    const read = props.win.exact ? [await documentRead(docId)] : await readDocumentChain(docId, documentRead)
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
const canReenter = computed(
  () => !props.win.exact && !!user.value && record.value?.isEditable === true && !record.value?.groupDisposed && record.value?.typeCode !== 'TR2',
)
const liveDocId = computed(() => chain.value[chain.value.length - 1]?.docId ?? null)

const title = computed(() => record.value?.title || t(k('untitled')))
const dot = computed(() => documentDot(record.value))

// --- Editing ---------------------------------------------------------------------------------------

const editing = computed(() => props.win.mode === 'edit')
// flowgate.v02.0127 T#5 (0009-L §2.12 / §3.1): one card edit session holds base / pending / latest / state. The
// textarea's `draft` ref is the typed text; the session never lets a read, an event or a conflict answer replace
// its base (only the ACK of its own pending SAVED/REPLAY advances it).
const session = shallowRef<EditSession>(newSession())
const draft = ref('')
const loadReadErrorKey = ref<string | null>(null)
/** The document the editor's text was read from (and is saved to). */
const editDocId = ref(props.win.shown)
/** An editor read was deferred for an upload guard: read once the guard ends (the editor shows "불러오는 중"). */
const needReloadFlag = ref(false)
let latestSequence = 0

const editLoading = computed(() => session.value.state === 'LOADING_BASE' || (needReloadFlag.value && editing.value))
const BLOCK_KEYS: Readonly<Record<string, string>> = {
  binding_required: 'binding_required',
  body_unreadable: 'body_unreadable',
}
const editLoadErrorKey = computed<string | null>(() => {
  if (loadReadErrorKey.value !== null) return loadReadErrorKey.value
  const current = session.value
  // Only an initial read that failed replaces the editor; a BLOCKED editor that has a base keeps its draft on screen.
  if (current.state === 'BLOCKED' && !current.base) return BLOCK_KEYS[current.blockReason ?? ''] ?? 'body_unreadable'
  return null
})
const saving = computed(() => session.value.state === 'SAVING')
const saveErrorKey = computed<string | null>(() => session.value.failure?.errorKey ?? (session.value.state === 'DETACHED' ? 'slot_document_changed' : null))
const conflicted = computed(() => session.value.state === 'CONFLICT')
/** A save answered body_unreadable (or similar): the draft and the rejected request are kept; the latest read is shown apart. */
const blockedWithBase = computed(() => isEditBlocked(session.value))
const latestReadable = computed(() => session.value.latest?.bodyState === 'readable' && typeof session.value.latest.content === 'string')
const needsRecovery = computed(() => session.value.configBlocked || session.value.needsNewIntent)
/** §2.12 explicit transitions: the call setting was corrected / a new save intent starts (draft kept, new key at the next send). */
function correctConfig(): void {
  session.value = configCorrected(session.value)
}
function startNewSaveIntent(): void {
  session.value = startNewIntentSession(session.value)
}
/** The latest document is read apart (latest only); the draft and base stay until the user adopts explicitly. */
function rereadLatest(): void {
  void readEditor(false, false)
}
const remoteChanged = computed(() => session.value.remoteChanged)
const latestText = computed(() => session.value.latest?.content ?? '')

/** What the upload buttons act on: the edited document while editing, else the one shown. */
const ioDocId = computed(() => (editing.value ? editDocId.value : props.win.shown))
/** The coordinates the header's upload uses: the active edit session's frozen ones, else the latest opener's. */
const ioCard = computed(() => (editing.value ? session.value.context : null) ?? props.win.card)
/** An upload guards this window's document (or a save the previous Designer sent has not answered). */
const guardedHere = computed(() => documentIoGuarded(ioDocId.value))

const dirty = computed(() => editing.value && editLoadErrorKey.value === null && session.value.state !== 'LOADING_BASE' && hasUnsaved(session.value))
/** No [저장]: conflict / detached / blocked / still loading, a 403 setting error or a key-reuse answer awaiting a new intent. */
const saveBlocked = computed(() => {
  const state = session.value.state
  return state === 'CONFLICT' || state === 'DETACHED' || state === 'BLOCKED' || state === 'LOADING_BASE' || session.value.configBlocked || session.value.needsNewIntent
})

// The textarea feeds the session (CLEAN <-> DIRTY against the base).
watch(draft, (value) => {
  session.value = typeDraft(session.value, value)
})

function resetEditor(): void {
  needReloadFlag.value = false
  latestSequence += 1
  session.value = discardSession(session.value)
  draft.value = ''
  loadReadErrorKey.value = null
}

/**
 * Reads the card's document fresh for the editor (every open reads it again). While the shown document is
 * guarded the read waits for the guard's end (the editor shows "불러오는 중").
 */
async function loadEditor(): Promise<void> {
  if (documentIoGuarded(props.win.shown)) {
    editDocId.value = props.win.shown
    needReloadFlag.value = true
    return
  }
  await readEditor(true)
}

/**
 * The editor read itself: the card's v2 GET with the frozen opener coordinates. Only a read that starts a session
 * (`adopt`) may fill the editor's base; any other read fills `latest` only (no automatic rebase).
 */
async function readEditor(focus: boolean, adopt = true): Promise<'ok' | 'failed' | 'superseded'> {
  const docId = props.win.shown
  needReloadFlag.value = false
  if (!adopt) {
    const ctx = session.value.context
    if (!ctx) return 'superseded'
    const seq = ++latestSequence
    try {
      const snapshot = await cardDocumentRead(ctx)
      if (seq !== latestSequence || session.value.context !== ctx) return 'superseded'
      const state = session.value.state
      session.value = state === 'CONFLICT' || state === 'DETACHED' ? conflictLatestRead(session.value, snapshot) : latestRead(session.value, snapshot)
      return 'ok'
    } catch {
      return 'failed'
    }
  }
  editDocId.value = docId
  loadReadErrorKey.value = null
  const started = beginLoad(session.value, props.win.card, docId)
  session.value = started.session
  draft.value = ''
  const ctx = started.session.context
  if (ctx === null) return 'failed'
  const seq = started.seq
  try {
    const snapshot = await cardDocumentRead(ctx)
    if (seq !== session.value.readSequence) return 'superseded'
    session.value = baseRead(session.value, seq, snapshot)
    if (session.value.state === 'CLEAN') draft.value = session.value.draft
    if (focus && session.value.state === 'CLEAN') {
      await nextTick()
      editorRef.value?.focus()
    }
    return session.value.state === 'CLEAN' ? 'ok' : 'failed'
  } catch (error) {
    if (seq !== session.value.readSequence) return 'superseded'
    loadReadErrorKey.value = documentReadErrorKey(documentErrorOf(error).kind)
    session.value = baseReadFailed(session.value, seq, loadReadErrorKey.value)
    return 'failed'
  }
}

/** The body's state as the MD Viewer reads it (null before the body view or for a special view). */
const bodyStatus = computed(() => body.value?.status ?? null)
const viewerActions = computed(() => documentViewerActions(bodyStatus.value, canReenter.value && !gone.value))

function startEdit(): void {
  if (gone.value || props.win.exact || guardedHere.value) return
  setDocumentWindowMode(props.win.id, 'edit')
}

/** [복구]: only on this click, with the existing recovery API and the diagnosed method (MdViewer.recover). */
function recover(): void {
  if (guardedHere.value) return
  void body.value?.recover()
}

function toView(): void {
  resetEditor()
  setDocumentWindowMode(props.win.id, 'view')
}

async function save(): Promise<void> {
  if (!editing.value || editLoading.value || editLoadErrorKey.value !== null || guardedHere.value) return
  if (documentIoHasUnresolved(ioDocId.value)) return // an uncertain upload of this document holds the one request
  const started = beginSave(session.value, newIdempotencyKey)
  if (started.request === null) return
  session.value = started.session
  const pending = started.request
  const context = pending.context
  let outcome
  try {
    const raw = await cardDocumentRevise(context, requestOf(pending))
    outcome = saveAnswered(session.value, pending.key, context, raw)
  } catch (error) {
    outcome = saveFailed(session.value, pending.key, context, cardDocumentErrorOf(error))
  }
  // An answer of an old window / another pending changes nothing (saveAnswered / saveFailed ignore it).
  if (outcome.ignored) return
  session.value = outcome.session
  if (outcome.confirmed !== null) {
    const savedDocId = outcome.confirmed.docId
    if (outcome.toView) toView()
    // The saved N+1 is an own, confirmed base; anything typed meanwhile stays in the draft (DIRTY).
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: savedDocId } }))
    void readRecord()
    return
  }
  if (outcome.session.state === 'CONFLICT' || outcome.session.state === 'DETACHED') {
    // The latest document is read separately; it only fills `latest`, never the base.
    void readEditor(false, false)
  }
}

/** CONFLICT: the user compared and merged by hand; the current text is adopted on the latest document's base. */
function adoptMergedDraft(): void {
  if (!conflicted.value && !blockedWithBase.value) return
  session.value = adoptMerge(session.value, draft.value)
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
  else closeDocumentWindow(props.win.id)
}

function confirmDiscard(): void {
  const asked = confirming.value
  confirming.value = null
  if (asked === 'close') closeDocumentWindow(props.win.id)
  else if (asked === 'cancel') toView()
}

// --- Following the server ---------------------------------------------------------------------------

/** The view reads the body again; an open editor keeps its text (it is read fresh on the next ✎). */
function refreshBody(change: DocumentContentChange): void {
  if (!editing.value) body.value?.refresh(change)
  else session.value = externalEvent(session.value)
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
const holdShown = computed(() => editing.value && (dirty.value || hasUnresolvedRequest(session.value)))

// The chain reached another document: show it (the body reads it as its id changes), or give way to the
// window that already has it.
watch([liveDocId, holdShown], ([live, hold]) => {
  if (live === null || hold || live === props.win.shown || props.win.exact) return
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
  if (editing.value && (dirty.value || hasUnresolvedRequest(session.value))) {
    // The guard keeps a window of the document clean; unsaved text is never replaced.
    console.warn('[documentIo] reflect skipped: the editor holds unsaved text', docId)
    return Promise.resolve('skipped')
  }
  if (editing.value) {
    needReloadFlag.value = false
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
  // A request in flight or uncertain keeps the document's save lock (single_inflight across the IO session).
  saving: () => hasUnresolvedRequest(session.value),
  onGuard: () => {
    // A surviving clean editor must read after release even if the Designer leaves during saving.
    // Its old text stays read-only until release starts the deferred read synchronously.
    if (!editing.value || dirty.value || hasUnresolvedRequest(session.value)) return
    needReloadFlag.value = true
  },
  onRelease: () => {
    if (!needReloadFlag.value || documentIoGuarded(ioDocId.value)) return
    needReloadFlag.value = false
    if (!editing.value || dirty.value || hasUnresolvedRequest(session.value)) return
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

/* flowgate.v02.0070 T#4: the exact window's part under the body (its own scroll, at most half the window). */
.document-window-aside {
  flex: none;
  max-height: 45%;
  overflow: auto;
  padding: 10px 12px;
  border-top: 1px solid var(--fg2-line);
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

/* A narrow window (360px) wraps whole buttons, still at the right end; the button text never wraps (ui-btn). */
.document-window-viewer-bar {
  flex-wrap: wrap;
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

.document-window-conflict {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 12px;
  border-top: 1px solid var(--fg2-line);
}

.document-window-latest {
  min-height: 80px;
  max-height: 160px;
  resize: vertical;
  background: var(--fg2-bg);
  color: var(--fg2-muted);
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 12px;
}

.document-window-remote {
  padding: 6px 12px;
  font-size: 12px;
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

