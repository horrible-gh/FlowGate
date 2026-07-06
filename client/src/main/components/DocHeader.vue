<template>
  <div v-if="doc" class="doc-header">
    <div class="doc-meta">
      <span class="doc-chip" :class="`c-${headerTypeCode}`">
        <i :class="typeIcon"></i> {{ typeLabel }}
      </span>
      <!-- R↔B root-type conversion (TR0066.0006 — UI for the TR0066.0005 backend endpoint).
           Shown only on a pristine workflow root (R/B) before the workflow decision; the
           server enforces the same gate (409) and we surface its message on failure. -->
      <button
        v-if="canConvertRootType"
        class="doc-convert-btn"
        type="button"
        :disabled="converting"
        :title="convertButtonTitle"
        @click.stop="openConvertConfirm"
      >
        <i class="fa-solid fa-right-left"></i> {{ convertTargetType }}
      </button>
      <!-- R0001 group 0132: click the document-ID badge to copy the canonical doc ID. -->
      <button
        type="button"
        class="doc-id-badge"
        :title="t('main.doc_header.copy_doc_id_title')"
        @click.stop="copyDocId"
      >
        {{ docFullPath }}
        <i class="fa-solid fa-copy doc-id-copy-icon"></i>
      </button>
      <span class="doc-status" :class="statusCls">{{ statusLabel }}</span>
      <!-- Mention-copied badge (R0001 group 0015 / NR0003 rev4): shown ONLY when this user has
           copied the document's mention. Absence == not copied; there is no 'before copy' state. -->
      <span v-if="mentionCopy" class="doc-mc-badge" :title="mentionCopyTooltip">
        <i class="fa-solid fa-clipboard-check"></i>
        {{ mentionCopyLabel }} · {{ t('main.doc_header.mention_copied_at', { time: mentionCopyTime }) }}
      </span>
      <div v-if="hasGroup && headerTypeCode !== 'DC'" class="doc-hdr-more">
        <button
          class="doc-hdr-more-btn"
          :class="{ open: showGroupMenu }"
          type="button"
          :title="t('main.group_actions.more_title')"
          @click.stop="openGroupMenu"
        >
          <i class="fa-solid fa-ellipsis"></i>
        </button>
      </div>
    </div>
    <div class="doc-title-row">
      <template v-if="editingTitle">
        <input
          ref="titleInputRef"
          v-model="editTitleValue"
          class="doc-title-input"
          @keydown.esc="cancelEditTitle"
          @keydown.enter.prevent="saveTitle"
        />
        <button class="doc-title-btn doc-title-btn--save" :disabled="savingTitle" @click="saveTitle">
          <i class="fa-solid fa-check"></i>
        </button>
        <button class="doc-title-btn doc-title-btn--cancel" @click="cancelEditTitle">
          <i class="fa-solid fa-xmark"></i>
        </button>
        <button
          v-if="groupTitle"
          class="doc-title-btn doc-title-btn--group"
          type="button"
          :title="t('main.doc_header.use_group_name')"
          @click="applyGroupNameToTitle"
        >
          <i class="fa-solid fa-wand-magic-sparkles"></i>
        </button>
      </template>
      <template v-else>
        <span class="doc-title">{{ doc.title }}</span>
        <button
          v-if="canEditDocument && headerTypeCode !== 'DC'"
          class="doc-title-pencil"
          :title="t('main.doc_header.edit_title')"
          @click="startEditTitle"
        >
          <i class="fa-solid fa-pencil"></i>
        </button>
        <button
          v-if="canEditDocument && headerTypeCode !== 'DC' && groupTitle"
          class="doc-title-btn doc-title-btn--group"
          type="button"
          :title="t('main.doc_header.use_group_name')"
          @click="applyGroupNameToTitle"
        >
          <i class="fa-solid fa-wand-magic-sparkles"></i>
        </button>
      </template>
    </div>
    <div class="doc-mg">
      <div class="doc-meta-item">
        <label>{{ t('main.doc_header.label_created_date') }}</label>
        <span>{{ createdDate || '—' }}</span>
      </div>
      <div class="doc-meta-item">
        <label>{{ t('main.doc_header.label_author') }}</label>
        <span>{{ ownerName || doc.owner_id || '—' }}</span>
      </div>
      <div class="doc-meta-item">
        <label>{{ t('main.doc_header.label_group') }}</label>
        <span>{{ groupLabel || doc.group_id || '—' }}</span>
      </div>
      <div class="doc-meta-item">
        <label>{{ t('main.doc_header.label_file') }}</label>
        <span class="mono" style="font-size:.75rem;">{{ fileName || '—' }}</span>
      </div>
    </div>
    <FileUploadModal
      v-if="doc"
      :tab="tab"
      :visible="showUploadModal"
      @update:visible="showUploadModal = $event"
      @uploaded="onFileUploaded"
    />
    <NewRelatedDocModal
      v-if="showRelatedDocModal"
      :tab="tab"
      @close="showRelatedDocModal = false"
      @created="onRelatedDocCreated"
    />
    <!-- rejection reason banner -->
    <div v-if="showRejectionBanner" class="rejection-banner">
      <i class="fa-solid fa-circle-exclamation"></i>
      <span class="rejection-banner-text">{{ rejectionBannerText }}</span>
    </div>
  </div>
  <WorkflowDecisionModal
    mode="create"
    v-model:visible="showWorkflowDecisionModal"
    :doc-class="docClass"
    :submitting="deciding"
    @confirmed="onWorkflowConfirmed"
  />
  <ConfirmModal
    :visible="showConvertConfirm"
    :title="t('main.doc_header.convert_confirm_title')"
    :message="convertConfirmMessage"
    :confirm-label="t('main.doc_header.convert_confirm_action')"
    @update:visible="showConvertConfirm = $event"
    @confirm="doConvertRootType"
  />
  <ContextMenu v-model:visible="showGroupMenu" :x="menuX" :y="menuY">
    <div class="dgm-cap">{{ t('main.group_actions.menu_caption') }}</div>
    <ContextMenuItem icon="fa-solid fa-circle-info" @click="openGroupInfo">
      {{ t('main.group_actions.group_info') }}
    </ContextMenuItem>
    <ContextMenuItem icon="fa-solid fa-pen" @click="openRename">
      {{ t('main.group_actions.rename_group') }}
    </ContextMenuItem>
    <!-- R↔B root-type conversion (TR0066.0006 rev1): the reviewer asked for the same action
         exposed in the group menu (it is also a group-relevant action), keeping the type-chip
         pill button too. Shown by the SAME gate as the pill (canConvertRootType): only on a
         pristine root before the workflow decision; absent otherwise. -->
    <template v-if="canConvertRootType">
      <div class="dgm-sep" role="separator"></div>
      <ContextMenuItem icon="fa-solid fa-right-left" @click="openConvertFromMenu">
        {{ t('main.group_actions.convert_root_type', { to: convertTargetLabel }) }}
      </ContextMenuItem>
    </template>
    <div class="dgm-sep" role="separator"></div>
    <ContextMenuItem icon="fa-solid fa-ban" :danger="true" @click="openDiscard">
      {{ t('main.group_actions.dispose_group') }}
    </ContextMenuItem>
  </ContextMenu>
  <GroupInfoModal
    v-model:visible="showGroupInfo"
    :group-id="doc?.group_id ?? ''"
    :group-name="groupName"
    :documents="groupDocuments"
    @rename="openRename"
  />
  <GroupDiscardModal
    v-model:visible="showGroupDiscard"
    :group-title="groupName"
    :documents="groupDocuments"
    :submitting="disposing"
    @confirm="onConfirmDiscard"
  />
  <CreateEditGroupModal
    v-model:visible="showRenameModal"
    mode="edit"
    dialog-mode="group"
    :project-id="doc?.project_id ?? ''"
    :group="renameGroupData"
    @saved="onGroupRenamed"
  />
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, patchRequest, postRequest } from '@shared/api'
import { qApiPath } from '@shared/utils/docIdFormatter'
import FileUploadModal from './FileUploadModal.vue'
import NewRelatedDocModal from './NewRelatedDocModal.vue'
import WorkflowDecisionModal from './WorkflowDecisionModal.vue'
import type { WfdConfirmPayload } from './WorkflowDecisionModal.vue'
import ContextMenu from './common/ContextMenu.vue'
import ContextMenuItem from './common/ContextMenuItem.vue'
import GroupInfoModal from './GroupInfoModal.vue'
import type { GroupInfoDoc } from './GroupInfoModal.vue'
import GroupDiscardModal from './GroupDiscardModal.vue'
import CreateEditGroupModal from './CreateEditGroupModal.vue'
import ConfirmModal from './ConfirmModal.vue'
import { useToast } from './common/useToast'
import { MENTION_COPIED_EVENT, type MentionCopiedDetail } from '../composables/useMentionCopy'
import { copyToClipboard } from '../utils/clipboard'
import type { Tab } from '../stores/tabs'
import { useTabsStore } from '../stores/tabs'
import { useExplorerStore } from '../stores/explorer'
import { useDocTypeStore } from '../stores/docTypeStore'
import type { AiReview } from '../types/aiReview'
import type { TestRun } from '../types/testRun'
import type { RejectionHistoryItem } from '../composables/useFlowGateToken'

const props = defineProps<{ tab: Tab }>()

const emit = defineEmits<{
  'related-doc-created': [payload: { docId: string; openAfter: boolean; projectId: string }]
  'doc-updated': [payload: { docId: string }]
  // NR0003 (group 0064) §6.2 — hand the confirmed workflow decision to MainPanel so it
  // owns the per-tab workflow display state. The action bar flips workflow → next from
  // THIS event (the POST 201 head + the dialog sequence), with no dependency on this
  // DocHeader instance's lifetime, exposed refs, headerRevision, or a successful detail GET.
  'workflow-decided': [payload: {
    docId: string
    reviewStatus: string
    steps: string[]
    headType: string | null
    headLabel: string | null
  }]
}>()

const { t } = useI18n()
const { showToast } = useToast()
const tabsStore = useTabsStore()
const explorerStore = useExplorerStore()
interface DocDetail {
  doc_id: string
  title: string
  status: string
  doc_review_status?: string | null
  is_final_approved?: boolean
  // TR0079.0003: true when this doc's group has been discarded (a file-less DC doc
  // exists). Drives the action-bar gate so a disposed group exposes no actions.
  group_disposed?: boolean
  is_editable?: boolean
  rejection_reason?: string | null
  rejection_history?: RejectionHistoryItem[] | null
  ai_review?: AiReview | null
  ai_review_history?: AiReview[] | null
  direction?: string | null
  type_code?: string | null
  created_at?: string | null
  owner_id?: string | null
  group_id?: string | null
  project_id?: string | null
  module?: string | null
  target_id?: string | null
  file_path?: string | null
  workflow_steps?: string[] | null
  parent_r_doc_id?: string | null
  workflow_root_type?: string | null
  workflow_head_type?: string | null
  workflow_head_index?: number | null
  workflow_self_index?: number | null
  workflow_head_status?: string | null
  workflow_head_doc_id?: string | null
  workflow_head_doc_review_status?: string | null
  workflow_head_doc_title?: string | null
  workflow_head_doc_number?: string | null
  test_run?: TestRun | null
  next_step_exists?: boolean
}

const doc = ref<DocDetail | null>(null)
const ownerName = ref<string | null>(null)
const groupLabel = ref<string | null>(null)
// Pure group title (no "(#num)" prefix) for the "use group name" title button.
// Pre-loaded in fetchGroup so it is always available — unlike `groupName`, which only
// fills when the ⋯ menu opens (loadGroupContext). NR0003 §5.
const groupTitle = ref('')
// R0001 group 0015 / NR0003 rev4 — persistent "mention copied" badge. null == not copied
// (no badge renders; there is no 'before copy' state). Hydrated from server user-state on open,
// updated live via the fg:mention_copied window bridge when this user copies a mention.
const mentionCopy = ref<{ kind: string; copiedAt: string } | null>(null)
const showUploadModal = ref(false)
const showRelatedDocModal = ref(false)
const showWorkflowDecisionModal = ref(false)

// ── Group actions (⋯ header menu): info / rename / discard — R0029.0001 / NR0029.0006 ──
const showGroupMenu = ref(false)
const menuX = ref(0)
const menuY = ref(0)
const showGroupInfo = ref(false)
const showGroupDiscard = ref(false)
const showRenameModal = ref(false)
const disposing = ref(false)
// In-flight guard for a workflow decision. NR0003 item 2: onWorkflowConfirmed had no
// progress flag, so during the async POST→refetch window the action could fire again
// (the R0001 "the button could be pressed repeatedly" 409 burst). Block re-entry until it settles
// and disable the modal's confirm button while it's set.
const deciding = ref(false)
const groupName = ref('')
const groupDocuments = ref<GroupInfoDoc[]>([])
const renameGroupData = ref<{ group_id: string; title: string; module: string; priority: string | null } | undefined>(undefined)
const GROUP_MENU_WIDTH = 200

const hasGroup = computed(() => !!doc.value?.group_id)

const mentionText = ref('')
const workflowSteps = ref<string[] | null>(null)
const qAnswerStatus = ref<string | null>(null)

const editingTitle = ref(false)
const editTitleValue = ref('')
const savingTitle = ref(false)
const titleInputRef = ref<HTMLInputElement | null>(null)

function startEditTitle() {
  if (!doc.value) return
  editTitleValue.value = doc.value.title
  editingTitle.value = true
  nextTick(() => titleInputRef.value?.focus())
}

function cancelEditTitle() {
  editingTitle.value = false
  editTitleValue.value = ''
}

async function saveTitle() {
  if (!doc.value || savingTitle.value) return
  const newTitle = editTitleValue.value.trim()
  if (!newTitle) return
  savingTitle.value = true
  try {
    await patchRequest<any>(`/api/v1/documents/${encodeURIComponent(doc.value.doc_id)}`, { title: newTitle })
    doc.value.title = newTitle
    // Keep the open tab (tab bar, document preview) in sync with the saved title —
    // the PATCH only updates doc.title locally, so without this the tab keeps the old
    // label. emit('doc-updated') bumps MainPanel's headerRevision so derived header
    // views re-resolve. (Markdown frontmatter / filename / cross-client SSE sync are
    // out of scope here — see TR.)
    tabsStore.setTabTitle(props.tab.id, newTitle)
    emit('doc-updated', { docId: props.tab.id })
    editingTitle.value = false
    showToast(t('main.doc_header.toast_title_saved'), 'success')
  } catch (e: any) {
    const msg = e?.response?.data?.detail ?? t('main.doc_header.toast_title_save_failed')
    showToast(msg, 'error')
  } finally {
    savingTitle.value = false
  }
}

const createdDate = computed(() => {
  const raw = doc.value?.created_at
  if (!raw) return null
  return raw.slice(0, 10)
})

const fileName = computed(() => {
  const fp = doc.value?.file_path
  if (fp) {
    const name = fp.split(/[\\/]/).pop() ?? fp
    return name
  }
  return null
})

const docFullPath = computed(() => {
  const docId = doc.value?.doc_id ?? ''
  const type = doc.value?.type_code ?? props.tab.typeCode ?? ''
  if (docId) return docId
  const group = doc.value?.group_id ?? ''
  return `${group}/${type}`
})

async function fetchOwner(ownerId: string) {
  try {
    const res = await getRequest<any>(`/api/v1/users/${encodeURIComponent(ownerId)}`)
    const user = (res.data as any)?.data ?? res.data
    ownerName.value = user?.username ?? user?.display_name ?? null
  } catch {
    ownerName.value = null
  }
}

function shortGroupId(groupId: string | null | undefined): string {
  if (!groupId) return ''
  const segs = groupId.split('-').filter((s) => s)
  return segs[segs.length - 1] || groupId
}

async function fetchGroup(projectId: string, groupId: string) {
  try {
    const res = await getRequest<any>('/api/v1/groups', { project_id: projectId })
    const groups: any[] = (res.data as any)?.groups ?? []
    const found = groups.find((g: any) => g.group_id === groupId)
    if (found) {
      const groupNum = shortGroupId(found.group_id)
      groupLabel.value = groupNum && found.title
        ? `(#${groupNum}) ${found.title}`
        : found.title ?? groupNum ?? null
      groupTitle.value = found.title ?? ''
    }
  } catch {
    groupLabel.value = null
    groupTitle.value = ''
  }
}

// A doc counts as "workflow-decided" by the SAME two-signal test the action-bar
// predicate uses (workflowViewState.ts L250): a wf_* review status OR a materialized
// workflow_head_type (the server only populates head_type once a decision exists).
// Kept here so fetchDoc can refuse to downgrade a decided doc (see below).
function _isDecided(d: any): boolean {
  if (!d) return false
  const s = typeof d.doc_review_status === 'string' ? d.doc_review_status : ''
  return s.startsWith('wf_') || d.workflow_head_type != null
}

// Same-document detail requests can complete out of order. Only the newest generation
// may commit state; confirmed local/SSE transitions also advance the generation so a GET
// that started before the transition cannot overwrite the newer state when it arrives.
let docFetchGeneration = 0

function invalidatePendingDocFetches(): void {
  docFetchGeneration += 1
}

// `silent` (SSE-driven refresh): refetch this tab's detail WITHOUT blanking the
// current state first, so an out-of-band sibling change (e.g. an AI worker creating
// the next-step doc via the inbox API) refreshes workflow_head_* — and thus the
// action bar's navigate-vs-create state — with no loading flicker. The default path
// (tab open / switch) still resets state up front.
async function fetchDoc(id: string, opts?: { silent?: boolean }): Promise<boolean> {
  const fetchGeneration = ++docFetchGeneration
  const silent = opts?.silent === true
  if (!silent) {
    doc.value = null
    workflowSteps.value = null
    ownerName.value = null
    groupLabel.value = null
    groupTitle.value = ''
    qAnswerStatus.value = null
    mentionCopy.value = null
    emit('doc-updated', { docId: id })
  }
  let res
  try {
    res = await getRequest<DocDetail>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(id)}`)
  } catch {
    // A newer request or confirmed transition superseded this request. Its owner is
    // responsible for the current state, so do not retry this obsolete generation.
    if (fetchGeneration !== docFetchGeneration) return true
    // Request failed. In silent mode keep whatever state we already hold (e.g. the
    // optimistic post-decision state) rather than blanking the header — a transient
    // detail-GET failure must not make a decided doc look undecided again.
    // (group 0021 / NR0003 item 4)
    if (!silent) doc.value = null
    return false
  }
  // Request-order guard (group 0040 / NR0003 rev6): a background GET can read the
  // pre-decision document and arrive after the decision POST plus optimistic transition.
  // The tab id still matches in that race, so the former tab-only guard was insufficient.
  if (fetchGeneration !== docFetchGeneration) return true
  // The active tab may also have changed while this request was in flight.
  if (props.tab.id !== id) return false
  const incoming = (res.data as any)?.data ?? res.data
  // Defense in depth: request generations reject known stale responses. Also preserve
  // decision markers if a current silent response would still downgrade a decided view,
  // while allowing deliberate non-silent loads to honor server truth.
  if (silent && _isDecided(doc.value) && !_isDecided(incoming)) {
    if (incoming && typeof incoming === 'object') {
      incoming.doc_review_status = doc.value!.doc_review_status
      if (doc.value!.workflow_head_type != null) {
        incoming.workflow_head_type = doc.value!.workflow_head_type
      }
      if (doc.value!.workflow_steps != null && incoming.workflow_steps == null) {
        incoming.workflow_steps = doc.value!.workflow_steps
      }
      doc.value = incoming
    }
    // else: empty body — keep the decided state we already hold, don't blank it.
  } else {
    doc.value = incoming
  }
  workflowSteps.value = doc.value?.workflow_steps ?? null
  if (doc.value?.owner_id) fetchOwner(doc.value.owner_id)
  if (doc.value?.project_id && doc.value?.group_id) {
    fetchGroup(doc.value.project_id, doc.value.group_id)
  }
  const typeCode = doc.value?.type_code ?? props.tab.typeCode
  const docId = doc.value?.doc_id
  if (typeCode === 'Q' && docId) {
    fetchQStatus(docId)
  }
  // Refresh the mention-copied badge from server user-state. Runs on silent focus/visibility
  // pulls too, so a copy made in another tab converges here when this tab regains focus.
  void fetchMentionCopy(id)
  emit('doc-updated', { docId: id })
  return true
}

// ── Mention-copied badge (R0001 group 0015 / NR0003 rev4) ──────────────────────────────
async function fetchMentionCopy(id: string): Promise<void> {
  try {
    const res = await getRequest<any>(`/api/v1/documents/mention-copy?doc_id=${encodeURIComponent(id)}`)
    if (props.tab.id !== id) return
    const d = (res.data as any) ?? {}
    mentionCopy.value = d.copied ? { kind: d.mention_kind, copiedAt: d.copied_at } : null
  } catch {
    // Best-effort hydration: a failed GET must not blank an already-shown badge.
  }
}

function _onMentionCopied(e: Event): void {
  const detail = (e as CustomEvent<MentionCopiedDetail>).detail
  if (!detail || detail.docId !== props.tab.id) return
  mentionCopy.value = { kind: detail.kind, copiedAt: detail.copiedAt }
}

const mentionCopyLabel = computed(() =>
  mentionCopy.value ? t(`main.doc_header.mention_kinds.${mentionCopy.value.kind}`) : '',
)
const mentionCopyTime = computed(() => {
  if (!mentionCopy.value) return ''
  const d = new Date(mentionCopy.value.copiedAt)
  if (isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
})
const mentionCopyTooltip = computed(() =>
  mentionCopy.value
    ? t('main.doc_header.mention_copy_tooltip', { label: mentionCopyLabel.value, time: mentionCopyTime.value })
    : '',
)

async function fetchQStatus(qId: string) {
  try {
    const res = await getRequest<any>(`/api/v1/q/${qApiPath(qId)}`)
    const detail = (res.data as any)?.q ?? res.data
    qAnswerStatus.value = detail?.status ?? 'pending'
  } catch {
    qAnswerStatus.value = null
  }
}

const approving = ref(false)

const canApproveReject = computed(() => doc.value?.status === 'in_review')
const canEditDocument = computed(() => {
  if (typeof doc.value?.is_editable === 'boolean') return doc.value.is_editable
  const status = doc.value?.status
  return !!status && !['approved', 'closed', 'cancelled', 'archived'].includes(status)
})

// (NR0003 §6 item 4) The former approveDoc()/rejectDoc() lived here but were never
// reachable: the live action bar approves/rejects via ReviewActionBar → MainPanel, not
// through DocHeader (grep: zero call sites). Worse, they mutated doc_review_status
// WITHOUT emit('doc-updated'), so even if reached they'd have left the derived action
// bar stuck. Removed to kill the confusion; the live action paths now go through
// applyReviewTransition() below, which mirrors the workflow-decision path.

async function copyMention() {
  if (!doc.value) return

  const typeName = docTypeStore.getLabel(props.tab.typeCode ?? '')
  const docId = doc.value.doc_id
  const title = doc.value.title

  const text = `[${typeName}] ${docId} — ${title}\nID: ${docId}`

  // B0001 group 0134: same non-secure-context defect as copyDocId — go through copyToClipboard()
  // (guarded async API + execCommand fallback) instead of the raw navigator.clipboard call.
  const ok = await copyToClipboard(text)
  showToast(
    ok ? t('main.doc_header.toast_mention_copied') : t('main.doc_header.toast_copy_failed'),
    ok ? 'success' : 'error',
  )
}

// R0001 group 0132: copy just the canonical document ID (the badge text) to the clipboard.
// B0001 group 0134: route through copyToClipboard() instead of the raw navigator.clipboard API.
// FlowGate is served over plain HTTP on LAN IPs (http://192.168.0.250:8089), a NON-secure
// context where `navigator.clipboard` is `undefined` — the old raw call threw a synchronous
// TypeError that the .then().catch() chain never caught, so the copy silently no-op'd with no
// toast. copyToClipboard() guards the async API and falls back to execCommand('copy') (which
// works in non-secure contexts), returning an honest boolean we branch the toast on.
async function copyDocId() {
  const docId = docFullPath.value
  if (!docId) return
  const ok = await copyToClipboard(docId)
  showToast(
    ok ? t('main.doc_header.toast_doc_id_copied') : t('main.doc_header.toast_copy_failed'),
    ok ? 'success' : 'error',
  )
}

// R0001 group 0111: fill the title with the group name in one click. If the title is
// not yet in edit mode, enter it (and focus) so the filled value can be reviewed and
// saved; if already editing, just replace the in-progress value.
function applyGroupNameToTitle() {
  if (!groupTitle.value) return
  if (!editingTitle.value) {
    editingTitle.value = true
    nextTick(() => titleInputRef.value?.focus())
  }
  editTitleValue.value = groupTitle.value
}

function onFileUploaded(_result: any) {
  fetchDoc(props.tab.id)
}

function onRelatedDocCreated(payload: { docId: string; openAfter: boolean; projectId: string }) {
  showRelatedDocModal.value = false
  showToast(t('main.doc_header.toast_related_doc_created'), 'success')
  emit('related-doc-created', payload)
}

async function onWorkflowConfirmed(payload: WfdConfirmPayload) {
  // NR0003 item 2 — in-flight guard. Without it, a second confirm during the async
  // POST→refetch window re-posts /workflow/decide and floods 409 already_decided
  // (the R0001 repeated-click symptom). Re-entry is blocked until this settles.
  if (!doc.value || deciding.value) return
  deciding.value = true
  try {
    await _runWorkflowConfirmed(payload)
  } finally {
    deciding.value = false
  }
}

async function _runWorkflowConfirmed(payload: WfdConfirmPayload) {
  if (!doc.value) return
  const steps = payload.sequence.map((it) => it.type)
  // NR0003 (group 0064): a workflow decision is a direct user command. The POST 201
  // ALREADY returns the confirmed head, and the dialog payload ALREADY holds the
  // sequence (type + label). Capture both and drive the action-bar transition from
  // them — instead of discarding the response and re-deriving the state indirectly via
  // a DocHeader-internal flip + parent exposed-ref re-read + detail refetch (§3/§4).
  let head: { type?: string | null; label?: string | null } | null = null
  try {
    const res = await postRequest<any>(
      `/api/v1/workflow/decide`,
      { doc_id: doc.value.doc_id, doc_class: payload.docClass, sequence: payload.sequence },
    )
    head = (res?.data as any)?.head ?? null
    showToast(t('main.main_panel.workflow_save_success'), 'success')
  } catch (e: any) {
    const errCode = e?.response?.data?.error
    if (errCode !== 'already_decided') {
      showToast(t('main.main_panel.workflow_save_failed'), 'error')
      return
    }
    // already_decided still means the workflow IS decided server-side; fall through and
    // transition the UI using the dialog sequence (the 409 body carries no head).
    showToast(t('main.main_panel.workflow_save_success'), 'success')
  }
  // Head: prefer the server-confirmed head from the 201 body; fall back to the dialog's
  // first step (BE get_effective_head = first pending step, so sequence[0] matches).
  const headType = head?.type ?? payload.sequence[0]?.type ?? null
  const headLabel = head?.label ?? payload.sequence[0]?.label ?? null

  // (1) Authoritative transition — hand the decision to MainPanel. The action bar flips
  //     workflow → next from this event alone (NR0003 §6.1/§6.2). It does NOT wait for,
  //     and is not reverted by, the detail backfill below.
  emit('workflow-decided', {
    docId: props.tab.id,
    reviewStatus: 'wf_in_progress',
    steps,
    headType,
    headLabel,
  })

  // (2) Local optimistic flip — kept ONLY for this tab's own DocWorkflow strip / info
  //     panel; it is no longer the authority for the action-bar transition. BE mirrors
  //     this exactly (doc_review_status='wf_in_progress', workflow_steps=[item.type ...]).
  if (doc.value) {
    // Invalidate detail GETs that began before the confirmed write so an old pre-decision
    // 200 cannot arrive late and restore the pre-decision strip (group 0040 / NR0003 rev6).
    invalidatePendingDocFetches()
    doc.value.doc_review_status = 'wf_in_progress'
    doc.value.workflow_steps = steps
    if (doc.value.workflow_head_type == null && headType != null) {
      doc.value.workflow_head_type = headType
    }
    workflowSteps.value = steps
    emit('doc-updated', { docId: props.tab.id })
  }

  // (3) Detail backfill — decoupled from the transition (NR0003 §6.3). It only refreshes
  //     OTHER detail fields (the head type/label needed for the action bar already came
  //     from the response). A failed/slow GET can never undo the decided action bar: that
  //     state now lives in MainPanel, and the silent refetch never blanks a decided doc.
  //     On success the live header reports decided and MainPanel drops its override.
  lastPullAt = Date.now()
  const refreshed = await silentRefetchWithRetry()
  if (!refreshed) {
    showToast(t('main.main_panel.workflow_refresh_failed'), 'warning')
  }
}

function openWorkflowDecisionModal() {
  showWorkflowDecisionModal.value = true
}

// ── R↔B root-type conversion (TR0066.0006 — UI for the TR0066.0005 backend) ──────────
// The conversion is allowed ONLY on a pristine workflow root (R or B) before its workflow
// decision: once decided, a workflow_sequence and child documents reference this root and
// the server gate rejects the change (409). We mirror that gate for visibility — root type,
// editable, undisposed group, not yet decided — and still rely on the server as the
// authority (a 409 from a not-quite-pristine root is surfaced as an error toast).
const showConvertConfirm = ref(false)
const converting = ref(false)

const convertTargetType = computed(() => (headerTypeCode.value === 'R' ? 'B' : 'R'))
const convertTargetLabel = computed(() => docTypeStore.getLabel(convertTargetType.value))
const currentTypeLabel = computed(() => docTypeStore.getLabel(headerTypeCode.value))
const convertButtonTitle = computed(() =>
  t('main.doc_header.convert_title', { to: convertTargetLabel.value }),
)
const convertConfirmMessage = computed(() =>
  t('main.doc_header.convert_confirm_message', {
    from: currentTypeLabel.value,
    to: convertTargetLabel.value,
  }),
)
const canConvertRootType = computed(() => {
  if (!doc.value) return false
  const tc = headerTypeCode.value
  if (tc !== 'R' && tc !== 'B') return false
  if (groupDisposed.value) return false
  if (!canEditDocument.value) return false
  // Pristine = no workflow decision yet. _isDecided() is the same two-signal test the
  // action bar uses (wf_* review status OR a materialized workflow_head_type).
  if (_isDecided(doc.value)) return false
  return true
})

function openConvertConfirm() {
  if (!canConvertRootType.value) return
  showConvertConfirm.value = true
}

// Same conversion, reached from the group menu (TR0066.0006 rev1). Close the menu first,
// then open the same confirm modal the pill button uses — one code path, two entry points.
function openConvertFromMenu() {
  showGroupMenu.value = false
  openConvertConfirm()
}

async function doConvertRootType() {
  if (!doc.value || converting.value) return
  const oldId = doc.value.doc_id
  const target = convertTargetType.value
  converting.value = true
  try {
    const res = await patchRequest<any>(
      `/api/v1/documents/${encodeURIComponent(oldId)}/root-type`,
      { new_type: target },
    )
    const newDoc = (res.data as any)?.data ?? res.data
    showToast(t('main.doc_header.toast_converted'), 'success')
    if (newDoc?.doc_id && newDoc.doc_id !== oldId) {
      // The conversion rewrote the identity (…-R ↔ …-B), so the open tab's id is now
      // stale. Open the new identity (which becomes active) BEFORE dropping the old one
      // so closeTab's active-fallback never lands on an unrelated tab, then refresh the
      // sidebar tree so the renamed node appears without a manual reload.
      const pid = newDoc.project_id ?? doc.value.project_id ?? null
      tabsStore.openTab({
        id: newDoc.doc_id,
        title: newDoc.title ?? doc.value.title,
        path: '',
        type: 'md',
        typeCode: newDoc.type_code ?? target,
        projectId: pid,
      })
      tabsStore.closeTab(oldId)
      const gid = newDoc.group_id ?? doc.value.group_id
      if (gid) {
        if (pid) explorerStore.invalidateProject(pid)
        window.dispatchEvent(new CustomEvent('fg:group_tree_changed', { detail: { groupId: gid } }))
      }
    } else {
      // Idempotent no-op (server returned the same id, e.g. already the target type) —
      // just refresh this view in place.
      await fetchDoc(props.tab.id, { silent: true })
    }
  } catch (e: any) {
    const msg = e?.response?.data?.detail ?? t('main.doc_header.toast_convert_failed')
    showToast(msg, 'error')
  } finally {
    converting.value = false
  }
}

// The group tree's doc node label is "[type label]: title" — strip the bracketed
// type prefix so the modals show just the title.
function _cleanTitle(label: string): string {
  const m = label.match(/^\[[^\]]*\]:\s*(.*)$/)
  return m ? m[1] : label
}

// Pull the group's name + member documents from the explorer group tree (session-auth,
// already cached). The bearer-only /list endpoints aren't reachable from the browser,
// so the tree the sidebar already loads is the single source of truth here (NR0006 §②).
async function loadGroupContext() {
  const pid = doc.value?.project_id
  const gid = doc.value?.group_id
  if (!pid || !gid) return
  try {
    const nodes = await explorerStore.fetchGroupTree(pid)
    const groupNode = nodes.find((n) => n.id === gid && n.node_type === 'group')
    groupName.value = groupNode?.label ?? groupName.value ?? gid
    groupDocuments.value = nodes
      .filter((n) => n.node_type === 'document' && n.parent_id === gid)
      .map((n) => {
        const tc = n.type_code ?? ''
        const seq = (n.number ?? '').split('-')[0]
        return { id: n.id, typeCode: tc, shortId: `${tc}${seq}`, title: _cleanTitle(n.label) }
      })
  } catch {
    // Tree fetch failed — keep any name we already have so the menu actions still work.
    if (!groupName.value) groupName.value = gid
  }
}

function openGroupMenu(e: MouseEvent) {
  const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
  // Right-align the menu under the ⋯ button (ContextMenu treats x/y as its top-left).
  menuX.value = Math.max(8, rect.right - GROUP_MENU_WIDTH)
  menuY.value = rect.bottom + 4
  showGroupMenu.value = true
  void loadGroupContext()
}

function openGroupInfo() {
  showGroupMenu.value = false
  void loadGroupContext()
  showGroupInfo.value = true
}

function openRename() {
  showGroupMenu.value = false
  showGroupInfo.value = false
  const gid = doc.value?.group_id
  if (!gid) return
  renameGroupData.value = {
    group_id: gid,
    title: groupName.value || '',
    module: doc.value?.module || 'none',
    priority: null,
  }
  showRenameModal.value = true
}

function openDiscard() {
  showGroupMenu.value = false
  void loadGroupContext()
  showGroupDiscard.value = true
}

function refreshGroupTree(gid: string) {
  const pid = doc.value?.project_id
  if (pid) explorerStore.invalidateProject(pid)
  // The DocHeader lives outside the sidebar; signal the explorer to reload its tree
  // so a disposed/renamed group is reflected without a manual refresh.
  window.dispatchEvent(new CustomEvent('fg:group_tree_changed', { detail: { groupId: gid } }))
}

async function onConfirmDiscard(reason: string) {
  const gid = doc.value?.group_id
  if (!gid || disposing.value) return
  disposing.value = true
  try {
    // The dispose endpoint returns HTTP 200 even for handled failures (e.g. already
    // disposed) with { status: 'error', message }. Treat that as an error toast rather
    // than a false success.
    const res = await postRequest<any>(`/api/v1/groups/${encodeURIComponent(gid)}/dispose`, { reason_detail: reason })
    if ((res.data as any)?.status === 'error') {
      showToast((res.data as any)?.message ?? t('main.group_actions.discard_error'), 'error')
      return
    }
    showGroupDiscard.value = false
    showToast(t('main.group_actions.discard_success'), 'success')
    refreshGroupTree(gid)
  } catch (e: any) {
    const msg = e?.response?.data?.detail ?? e?.response?.data?.message ?? t('main.group_actions.discard_error')
    showToast(msg, 'error')
  } finally {
    disposing.value = false
  }
}

function onGroupRenamed() {
  showRenameModal.value = false
  const gid = doc.value?.group_id
  if (gid) refreshGroupTree(gid)
  groupName.value = ''
  void loadGroupContext()
  // Re-resolve the header's own group label (the "(#num) title" badge in the meta grid).
  if (doc.value?.project_id && doc.value?.group_id) {
    fetchGroup(doc.value.project_id, doc.value.group_id)
  }
}

onMounted(() => {
  fetchDoc(props.tab.id)
  window.addEventListener('fg:doc_review_status_changed', _onReviewStatusChanged)
  window.addEventListener('fg:q_status_changed', _onQStatusChanged)
  window.addEventListener('fg:open_docs_refresh', _onOpenDocsRefresh)
  window.addEventListener(MENTION_COPIED_EVENT, _onMentionCopied)
  // Gap C pull fallback: refetch on foreground-regain, independent of SSE delivery.
  window.addEventListener('focus', _onActivePull)
  window.addEventListener('online', _onActivePull)
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', _onVisibilityPull)
  }
})
watch(() => props.tab.id, (id) => fetchDoc(id))

onBeforeUnmount(() => {
  window.removeEventListener('fg:doc_review_status_changed', _onReviewStatusChanged)
  window.removeEventListener('fg:q_status_changed', _onQStatusChanged)
  window.removeEventListener('fg:open_docs_refresh', _onOpenDocsRefresh)
  window.removeEventListener(MENTION_COPIED_EVENT, _onMentionCopied)
  window.removeEventListener('focus', _onActivePull)
  window.removeEventListener('online', _onActivePull)
  if (typeof document !== 'undefined') {
    document.removeEventListener('visibilitychange', _onVisibilityPull)
  }
})

// Active-document pull fallback. Foreground/SSE pulls keep long-lived tabs fresh, but
// they also create concurrent detail requests. fetchDoc's generation guard above makes
// these triggers safe when their responses arrive out of order (group 0040 / NR0003 rev6).
let pulling = false
let lastPullAt = 0
const PULL_COOLDOWN_MS = 1500
const PULL_RETRY_DELAY_MS = 500

// fetchDoc(silent) + one retry for transient transport/auth failures. Silent throughout
// so a failed refresh never blanks an already-decided header.
async function silentRefetchWithRetry(): Promise<boolean> {
  if (await fetchDoc(props.tab.id, { silent: true })) return true
  await new Promise((resolve) => setTimeout(resolve, PULL_RETRY_DELAY_MS))
  return fetchDoc(props.tab.id, { silent: true })
}

// ── Gap D — symmetric action-path refresh (NR0003 §2 / §6 item 2) ──
// Approve / reject / mark-revised used to refresh through the NON-silent fetchDoc, which
// blanks doc.value (and emits a loading state) BEFORE the GET. In the "always-open idle"
// window that single GET can be slow or 401, leaving the header stuck blank with only the
// synchronous success toast showing — exactly the 6/13 symptom (NR0003 §2.2). Unlike the
// workflow-decision path, these actions had no optimistic flip and no retry. This applies
// the same treatment as onWorkflowConfirmed: (1) optimistically reflect the server-
// confirmed status so the strip + action bar flip immediately, independent of the
// round-trip, then (2) backfill the rest with a SILENT, retrying refetch so a transient
// GET failure can never blank an already-transitioned header. Called with the
// doc_review_status the server returned (no guessing); called with no status (e.g. the
// reject dialog merely closing) it just does the silent backfill.
async function applyReviewTransition(nextStatus?: string | null): Promise<void> {
  if (!doc.value) return
  if (nextStatus) {
    invalidatePendingDocFetches()
    doc.value.doc_review_status = nextStatus
    // Bump MainPanel's headerRevision now so its derived action-bar/strip view
    // re-resolves immediately, independent of the refetch below.
    emit('doc-updated', { docId: props.tab.id })
  }
  lastPullAt = Date.now()
  await silentRefetchWithRetry()
}

// Foreground-regain pull. A short cooldown collapses the focus + visibilitychange +
// online burst that fires together on wake into a single fetch; the SSE-driven path
// (_onOpenDocsRefresh) stays event-driven with no cooldown so real server changes are
// never skipped.
async function pullActiveDoc(): Promise<void> {
  if (!doc.value || pulling) return
  // No point pulling a tab the user can't see; the handler fires again on return.
  if (typeof document !== 'undefined' && document.visibilityState === 'hidden') return
  const now = Date.now()
  if (now - lastPullAt < PULL_COOLDOWN_MS) return
  lastPullAt = now
  pulling = true
  try {
    await silentRefetchWithRetry()
  } finally {
    pulling = false
  }
}

function _onActivePull() {
  void pullActiveDoc()
}

function _onVisibilityPull() {
  if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return
  void pullActiveDoc()
}

function _onOpenDocsRefresh(e: Event) {
  // SSE signalled a doc/group change in the project. This tab's workflow-head state
  // is derived from a one-shot fetch on mount, so a sibling doc created/changed
  // out-of-band (e.g. an AI worker registering the next-step doc) would otherwise
  // leave the action bar stale — offering "proceed to next step" (create) instead of
  // navigating to the doc that now exists. Refetch silently (with one retry) to keep
  // it live. Stamp lastPullAt so a focus pull landing right after doesn't double-fetch.
  const current = doc.value
  if (!current) return
  const payload = (e as CustomEvent).detail as { project?: string | null } | undefined
  if (payload?.project && current.project_id && payload.project !== current.project_id) return
  lastPullAt = Date.now()
  void silentRefetchWithRetry()
}

function _onReviewStatusChanged(e: Event) {
  const payload = (e as CustomEvent).detail as { doc_id?: string; next_status?: string; rejection_reason?: string | null; rejection_history?: Array<{ reason: string; rejected_at: string; rejected_by: string | null }> | null }
  if (doc.value && payload.doc_id === doc.value.doc_id && payload.next_status) {
    invalidatePendingDocFetches()
    doc.value.doc_review_status = payload.next_status
    if (payload.rejection_reason !== undefined) {
      doc.value.rejection_reason = payload.rejection_reason
    }
    if (payload.rejection_history !== undefined) {
      doc.value.rejection_history = payload.rejection_history
    }
    // SSE delivered a review-status change for the doc this tab is showing (the
    // common case: a workflow decision made by someone other than the viewer).
    // We've mutated doc state in place, but MainPanel derives the action-bar
    // workflow view (e.g. the [Workflow Decision] button) from headerRevision — and
    // only emit('doc-updated') bumps it. Unlike onWorkflowConfirmed, this path
    // had no emit, so the derived view never re-resolved and the button stayed
    // stuck until a manual refresh (R0001 / NR0003). Emit so it flips now,
    // independent of whether the silent refetch in _onOpenDocsRefresh succeeds.
    emit('doc-updated', { docId: props.tab.id })
  }
}

function _onQStatusChanged(e: Event) {
  const payload = (e as CustomEvent).detail as { qId?: string; status?: string }
  if (doc.value && payload.qId === doc.value.doc_id && payload.status) {
    qAnswerStatus.value = payload.status
  }
}

const groupId = computed(() => doc.value?.group_id ?? null)
const docProjectId = computed(() => doc.value?.project_id ?? null)
const docModule = computed(() => doc.value?.module ?? 'none')
const docLoaded = computed(() => doc.value != null)
// TR0079.0003: the doc belongs to a discarded group. Exposed so MainPanel can gate
// the action bar — a disposed group is terminal and must offer no document actions.
const groupDisposed = computed(() => doc.value?.group_disposed === true)
const docReviewStatus = computed(() => doc.value?.doc_review_status ?? null)
const rejectionReason = computed(() => doc.value?.rejection_reason ?? null)
const rejectionHistory = computed(() => doc.value?.rejection_history ?? [])
const showRejectionBanner = computed(() => (
  ['rejected', 'revised'].includes(docReviewStatus.value ?? '')
  && !['R', 'B'].includes(props.tab.typeCode ?? '')
  && !['R', 'B'].includes(doc.value?.type_code ?? '')
  && props.tab.typeCode !== 'Q'
  && doc.value?.type_code !== 'Q'
  // TR0044.0010 rev8: a conversation (CH) is a chat surface, not a reviewed
  // artifact — its rejection-reason banner is hidden here just like Q (reviewer: "only for
  // chat docs ... hide the rejection reason"). The DocHeader itself stays visible (rev6).
  && props.tab.typeCode !== 'CH'
  && doc.value?.type_code !== 'CH'
))
const rejectionBannerText = computed(() => {
  if (docReviewStatus.value === 'revised') {
    return t('main.doc_header.document_revised')
  }
  if (rejectionHistory.value.length > 1) {
    return t('main.doc_header.document_rejected_count', { count: rejectionHistory.value.length })
  }
  return t('main.doc_header.document_rejected')
})
const aiReview = computed(() => doc.value?.ai_review ?? null)
const aiReviewHistory = computed(() => doc.value?.ai_review_history ?? [])
// 0155: latest test run (with failing-case detail) for the design-B fail strip. null on
// every non-failing doc, since the embed only binds to a doc that has a bound run.
const testRun = computed(() => doc.value?.test_run ?? null)

const docClass = computed((): string => {
  const tc = doc.value?.type_code ?? props.tab.typeCode ?? 'R'
  if (tc === 'Q') return 'Q'
  if (tc === 'B') return 'B'
  return 'R'
})

const parentRDocId = computed(() => doc.value?.parent_r_doc_id ?? null)
const workflowRootType = computed(() => doc.value?.workflow_root_type ?? null)
const docTypeCode = computed(() => doc.value?.type_code ?? null)
const workflowHeadType = computed(() => doc.value?.workflow_head_type ?? null)
// Identity-resolved head position in the sequence (disambiguates repeated types).
const workflowHeadIndex = computed(() => doc.value?.workflow_head_index ?? null)
// Identity-resolved slot index of THIS viewed doc (disambiguates repeated types
// for the DocInfoPanel "next" box). null when not a realized step slot.
const workflowSelfIndex = computed(() => doc.value?.workflow_self_index ?? null)
// D030 §3.5: derived from doc chain (not a direct workflow_sequence_items.status reference).
const headStatus = computed(() => doc.value?.workflow_head_status ?? null)
// T812: group head document ID and its review status (null when no head doc object exists).
const headDocId = computed(() => doc.value?.workflow_head_doc_id ?? null)
const headDocReviewStatus = computed(() => doc.value?.workflow_head_doc_review_status ?? null)
// T813: head doc title for ActionBar 2-line label.
const headDocTitle = computed(() => doc.value?.workflow_head_doc_title ?? null)
// T816: head doc canonical number (full doc_id string, e.g. "test.test2.0001.0004-DS").
const headDocNumber = computed(() => doc.value?.workflow_head_doc_number ?? null)
// T813: viewed doc title for ActionBar 2-line label.
const docTitle = computed(() => doc.value?.title ?? null)
// D030 §3.5: true when this doc belongs to an R workflow that has active sequence steps.
// Uses API value when available (T811); falls back to local approximation for orphan/no-sequence rows.
const nextStepExists = computed((): boolean => {
  if (doc.value?.next_step_exists !== undefined) return doc.value.next_step_exists === true
  return (
    parentRDocId.value != null &&
    Array.isArray(workflowSteps.value) &&
    workflowSteps.value.length > 0
  )
})

defineExpose({
  canApproveReject,
  canEditDocument,
  approving,
  groupId,
  docProjectId,
  docModule,
  docLoaded,
  groupDisposed,
  docReviewStatus,
  rejectionReason,
  rejectionHistory,
  aiReview,
  aiReviewHistory,
  testRun,
  fetchDoc,
  applyReviewTransition,
  onWorkflowConfirmed,
  copyMention,
  copyDocId,
  docFullPath,
  showUploadModal,
  showRelatedDocModal,
  showWorkflowDecisionModal,
  deciding,
  workflowSteps,
  openWorkflowDecisionModal,
  mentionText,
  parentRDocId,
  workflowRootType,
  docTypeCode,
  workflowHeadType,
  workflowHeadIndex,
  workflowSelfIndex,
  headStatus,
  headDocId,
  headDocReviewStatus,
  headDocTitle,
  headDocNumber,
  docTitle,
  nextStepExists,
  canConvertRootType,
  convertTargetType,
  doConvertRootType,
  openConvertConfirm,
  openConvertFromMenu,
})

const docTypeStore = useDocTypeStore()

const TYPE_ICONS: Record<string, string> = {
  R: 'fa-solid fa-file-lines',
  DS: 'fa-solid fa-pen-ruler',
  D: 'fa-solid fa-drafting-compass',
  T: 'fa-solid fa-list-check',
  TR: 'fa-solid fa-file-circle-check',
  DC: 'fa-solid fa-trash-can',
}

const STATUS_MAP: Record<string, { cls: string }> = {
  draft:          { cls: 's-draft' },
  open:           { cls: 's-open' },
  in_review:      { cls: 's-pending' },
  approved:       { cls: 's-approved' },
  rejected:       { cls: 's-rejected' },
  cancelled:      { cls: 's-rejected' },
  closed:         { cls: 's-approved' },
  archived:       { cls: '' },
  pending_review: { cls: 's-pending' },
  revised:        { cls: 's-open' },
}

const STATUS_LABEL_KEY: Record<string, string> = {
  draft:          'main.doc_header.status_draft',
  open:           'main.doc_header.status_open',
  in_review:      'main.doc_header.status_in_review',
  approved:       'main.doc_header.status_approved',
  rejected:       'main.doc_header.status_rejected',
  cancelled:      'main.doc_header.status_cancelled',
  closed:         'main.doc_header.status_closed',
  archived:       'main.doc_header.status_archived',
  pending_review: 'main.doc_header.status_pending_review',
  revised:        'main.doc_header.status_revised',
}

// The chip label/icon/colour must use the document's own type_code, not just the
// tab's. A file-less DC tab reaches the header with an empty tab.typeCode, so the
// chip rendered blank while the status badge (which already reads doc.type_code)
// showed "disposed" — the r5/r6 "chip still empty" symptom. Resolve from the doc first,
// exactly like statusCls/statusLabel do.
const headerTypeCode = computed(() => doc.value?.type_code ?? props.tab.typeCode ?? '')

const typeLabel = computed(() => {
  const tc = headerTypeCode.value
  // DC (group discard) is a registry type the FE label store may not carry, so the
  // chip rendered blank (review r5). Fall back to a fixed Discard label.
  if (tc === 'DC') return t('main.doc_header.type_discard')
  return docTypeStore.getLabel(tc)
})

const typeIcon = computed(() =>
  TYPE_ICONS[headerTypeCode.value] ?? 'fa-solid fa-file'
)

const statusCls = computed(() => {
  const typeCode = doc.value?.type_code ?? props.tab.typeCode
  if (typeCode === 'Q') return qAnswerStatus.value === 'done' ? 's-approved' : 's-open'
  if (typeCode === 'DC') return 's-rejected'
  const reviewStatus = doc.value?.doc_review_status
  if (['R', 'B'].includes(typeCode ?? '') && (reviewStatus?.startsWith('wf_') ?? false)) return 's-approved'
  if (reviewStatus && STATUS_MAP[reviewStatus]) return STATUS_MAP[reviewStatus].cls
  return STATUS_MAP[doc.value?.status ?? '']?.cls ?? ''
})

const statusLabel = computed(() => {
  const typeCode = doc.value?.type_code ?? props.tab.typeCode
  if (typeCode === 'Q') {
    return qAnswerStatus.value === 'done'
      ? t('main.doc_info_panel.status_q_done')
      : t('main.doc_info_panel.status_q_answering')
  }
  // DC (group discard) is terminal: always "disposed", never the review-pending default
  // that leaked through before (review r2 #3).
  if (typeCode === 'DC') return t('main.doc_header.status_discarded')
  const reviewStatus = doc.value?.doc_review_status
  if (['R', 'B'].includes(typeCode ?? '') && (reviewStatus?.startsWith('wf_') ?? false)) return t('main.doc_info_panel.status_wf_done')
  if (reviewStatus && STATUS_LABEL_KEY[reviewStatus]) return t(STATUS_LABEL_KEY[reviewStatus])
  const status = doc.value?.status ?? ''
  if (STATUS_LABEL_KEY[status]) return t(STATUS_LABEL_KEY[status])
  return status
})
</script>

<style scoped>
.s-draft {
  background: var(--surface-h);
  color: var(--text-m);
  border: 1px solid var(--border);
}

/* "Use group name" title button (group 0111 / R0001): fills the document title with the
   group name in one click, so it no longer has to be retyped after creation. */
.doc-title-btn--group {
  color: var(--primary);
  border-color: #bfdbfe;
}

.doc-title-btn--group:hover {
  background: var(--surface-h);
}

/* Mention-copied badge (R0001 group 0015 / NR0003 rev4): green "copied" pill in the meta row,
   rendered only when the user has copied this document's mention. */
.doc-mc-badge {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
  line-height: 1.5;
  white-space: nowrap;
  color: #15803d;
  background: #dcfce7;
  border: 1px solid #16a34a40;
  cursor: default;
}

.doc-mc-badge i {
  font-size: 0.72rem;
}

/* R↔B root-type conversion pill (TR0066.0006): compact chip next to the type chip,
   shown only on a pristine root. The bare target letter (R/B) keeps it locale-free; the
   tooltip carries the full localized label. */
.doc-convert-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 700;
  line-height: 1.5;
  white-space: nowrap;
  color: var(--text-s);
  background: var(--surface);
  border: 1px solid var(--border);
  cursor: pointer;
  transition: all 0.15s;
}

.doc-convert-btn:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-l);
}

.doc-convert-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.doc-convert-btn i {
  font-size: 0.68rem;
}

.doc-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.doc-title {
  flex: 1;
}

.doc-title-pencil {
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  padding: 2px 4px;
  color: var(--text-m);
  opacity: 0.5;
  font-size: 0.75rem;
  line-height: 1;
  border-radius: 4px;
  transition: opacity 0.15s;
}

.doc-title-pencil:hover {
  opacity: 1;
  background: var(--surface-h);
}

.doc-title-input {
  flex: 1;
  font-size: inherit;
  font-weight: inherit;
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 4px;
  background: var(--surface);
  color: var(--text);
  outline: none;
}

.doc-title-input:focus {
  border-color: var(--primary, #3b82f6);
}

.doc-title-btn {
  flex-shrink: 0;
  background: none;
  border: 1px solid var(--border);
  cursor: pointer;
  padding: 3px 7px;
  border-radius: 4px;
  font-size: 0.75rem;
  line-height: 1;
  transition: background 0.15s;
}

.doc-title-btn--save {
  color: #16a34a;
  border-color: #16a34a40;
}

.doc-title-btn--save:hover:not(:disabled) {
  background: #dcfce7;
}

.doc-title-btn--save:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.doc-title-btn--cancel {
  color: var(--text-m);
}

.doc-title-btn--cancel:hover {
  background: var(--surface-h);
}

.rejection-banner {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
  padding: 10px 14px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  font-size: 0.8125rem;
  color: #991b1b;
  line-height: 1.5;
}

.rejection-banner i {
  flex-shrink: 0;
  margin-top: 2px;
  color: #dc2626;
}

.rejection-banner-text {
  min-width: 0;
  overflow-wrap: anywhere;
  font-weight: 600;
}

/* ── Group disposal entry point — the more (⋯) menu at the far right of the document header's first row (TR0029.0004 approved location) ── */
.doc-hdr-more {
  position: relative;
  margin-left: auto;
}
.doc-hdr-more-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  color: var(--text-s);
  background: var(--surface);
  cursor: pointer;
  transition: all 0.15s;
}
.doc-hdr-more-btn:hover,
.doc-hdr-more-btn.open {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-l);
}

/* Slotted into ContextMenu (parent-scoped) — caption + danger separator. */
.dgm-cap {
  padding: 6px 14px 4px;
  color: var(--text-m);
  font-size: .6rem;
  font-weight: 700;
  letter-spacing: .08em;
  text-transform: uppercase;
}
.dgm-sep {
  height: 1px;
  margin: 4px 8px;
  background: var(--border);
}
</style>
