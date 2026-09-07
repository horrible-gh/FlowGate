<template>
  <!-- flowgate.default.0481 T0008 (D0006 §6.3 / L0007 §2.11) — the human approval
       gate's own screen: a general merge's resolved conflict stops HERE, not at an
       immediate commit. Mirrors GroupChangesDialog's modal shell/diff engine, but
       adds the review-gate-specific parts mockup v13 화면 2 asks for: a read-only
       resolver-provider badge, a conflict-origin overlay on the real diff, and the
       conversation/approve/reject footer. -->
  <teleport to="body">
    <div class="modal-bg">
      <div
        class="modal-box document-modal document-modal--edit gmr-modal"
        role="dialog"
        aria-modal="true"
        :aria-label="t('main.git_review.title')"
      >
        <div class="modal-hd">
          <div class="gmr-hd-text">
            <h2 class="modal-title"><AppIcon name="clock" /> {{ headerTitle }}</h2>
            <p>
              <span class="gcd-mono">{{ branch || '-' }}</span>
              →
              <span class="gcd-mono">{{ baseBranch || 'main' }}</span>
              <span class="gcd-dot">·</span>
              {{ t('main.git_review.file_count', { n: changes.length }) }}
            </p>
          </div>
          <div class="modal-hd-actions">
            <button class="modal-close" type="button" :title="t('common.close')" :aria-label="t('common.close')" @click="emit('close')">
              <AppIcon name="x" />
            </button>
          </div>
        </div>

        <div v-if="loading" class="gmr-state">
          <AppIcon name="spinner" spin /> {{ t('common.loading') }}
        </div>
        <div v-else-if="loadError" class="gmr-state gmr-state-error">
          <span>{{ loadError }}</span>
          <button type="button" class="btn btn-secondary" @click="loadReview">
            <AppIcon name="arrows-clockwise" /> {{ t('main.group_changes.retry') }}
          </button>
        </div>
        <template v-else>
          <div class="gmr-provider-badge">
            <AppIcon name="robot" />
            <span>{{ t('main.git_review.resolved_by', { provider: review?.resolver_provider || t('main.git_review.unknown_provider') }) }}</span>
            <small>{{ t('main.git_review.provider_badge_note') }}</small>
          </div>

          <p class="gmr-warning" :class="warningClass">
            <AppIcon :name="warningIcon" />
            <span>{{ warningText }}</span>
          </p>

          <div class="modal-bd gmr-modal-body">
            <div class="gcd-bd gmr-bd">
              <aside class="gcd-filelist" :aria-label="t('main.git_review.file_list')">
                <p v-if="!changes.length" class="gcd-nomatch">{{ t('main.group_changes.no_match') }}</p>
                <button
                  v-for="change in changes"
                  :key="change.path"
                  type="button"
                  class="gcd-file gmr-file"
                  :class="{ active: change.path === selectedPath }"
                  @click="select(change.path)"
                >
                  <span class="gcd-file-top">
                    <span class="gcd-badge" :class="`gcd-badge-${statusKind(change.status)}`">{{ statusBadge(change.status) }}</span>
                    <span class="gcd-file-name">{{ baseName(change.path) }}</span>
                  </span>
                  <span class="gcd-file-dir">{{ dirName(change.path) }}</span>
                  <span v-if="conflictCountOf(change.path)" class="gmr-conflict-count-badge">
                    <AppIcon name="lightning" /> {{ t('main.git_review.conflict_chunks', { n: conflictCountOf(change.path) }) }}
                  </span>
                </button>
              </aside>

              <section class="gcd-diffwrap">
                <div class="gcd-diff-hd">
                  <span class="gcd-diff-path" :title="selectedPath || ''">{{ selectedPath || '-' }}</span>
                </div>
                <div v-if="selectedOrigins.length" class="gmr-origin-tags">
                  <span v-for="origin in selectedOrigins" :key="origin.chunk_id" class="gmr-origin-tag" :class="`gmr-sel-${origin.selection}`">
                    {{ t('main.git_review.chunk_selection', { selection: selectionLabel(origin.selection) }) }}
                    <template v-if="origin.start_line != null">({{ origin.start_line }}–{{ origin.end_line }}{{ t('main.git_review.line_suffix') }})</template>
                  </span>
                </div>

                <div v-if="diffLoading" class="gcd-diff-state">
                  <AppIcon name="spinner" spin /> {{ t('common.loading') }}
                </div>
                <div v-else-if="diffError" class="gcd-diff-state gcd-diff-error">
                  <span>{{ t('main.group_changes.diff_failed') }}</span>
                  <button type="button" class="gcd-retry" @click="loadDiff(selectedPath)">
                    <AppIcon name="arrows-clockwise" /> {{ t('main.group_changes.retry') }}
                  </button>
                </div>
                <div v-else-if="diffBinary" class="gcd-diff-state">{{ t('main.group_changes.binary') }}</div>
                <div v-else-if="diff && !hasDiffChanges" class="gcd-diff-state">{{ t('main.group_changes.no_diff') }}</div>
                <template v-else-if="diff">
                  <div class="gcd-diff gcd-diff-unified">
                    <template v-for="(section, sIdx) in unifiedDiffSections" :key="`u${sIdx}`">
                      <div v-if="section.kind === 'gap'" class="gcd-gap">
                        {{ t('main.file_diff.skipped_lines', { n: section.count }) }}
                      </div>
                      <template v-else>
                        <div
                          v-for="(row, rIdx) in section.rows"
                          :key="`u${sIdx}-${rIdx}`"
                          class="gcd-line"
                          :class="[unifiedClass(row.status), { 'gmr-origin-row': isOriginRow(row.rightNumber) }]"
                        >
                          <span v-if="isOriginRow(row.rightNumber)" class="gmr-origin-flag" :title="t('main.git_review.origin_flag_title')">⚡</span>
                          <span class="gcd-ln">{{ row.leftNumber ?? '' }}</span>
                          <span class="gcd-ln">{{ row.rightNumber ?? '' }}</span>
                          <span class="gcd-sign">{{ row.sign }}</span>
                          <span class="gcd-text">{{ row.line.line }}</span>
                        </div>
                      </template>
                    </template>
                  </div>
                </template>
              </section>

              <section class="gmr-conversation" :aria-label="t('main.git_review.conversation_title')">
                <div class="gmr-conv-hd">
                  <strong>{{ t('main.git_review.conversation_title') }}</strong>
                  <span v-if="awaitingReply" class="badge badge-yellow">{{ t('main.git_review.awaiting_reply') }}</span>
                </div>
                <div class="gmr-conv-log">
                  <div v-if="!conversation.length" class="gmr-conv-empty">{{ t('main.git_review.conversation_empty') }}</div>
                  <div v-for="turn in conversation" :key="turn.turn_id" class="gmr-turn" :class="`gmr-turn-${turn.role}`">
                    <strong>{{ turn.role === 'human' ? t('main.git_review.turn_human') : t('main.git_review.turn_ai') }}:</strong>
                    <span>{{ turn.message }}</span>
                  </div>
                </div>
                <div v-if="heldTestOperations.length" class="gmr-held-tests">
                  <strong>{{ t('main.git_review.held_test_operations_title') }}</strong>
                  <p class="gmr-held-note">{{ t('main.git_review.held_test_operations_note') }}</p>
                  <ul>
                    <li v-for="op in heldTestOperations" :key="op.operation_id">
                      <span class="gcd-mono">{{ op.path }}</span> — {{ op.purpose }}
                    </li>
                  </ul>
                </div>
                <div class="gmr-conv-compose">
                  <label class="gmr-conv-label">{{ t('main.git_review.next_provider_label') }}</label>
                  <AiProviderSelect
                    :providers="providers"
                    :model-value="selectedProvider"
                    :loading="providerLoading"
                    :errored="providerErrored"
                    hide-label
                    @update:model-value="(v) => emit('update:provider', v)"
                  />
                  <label class="gmr-apply-toggle">
                    <input type="checkbox" v-model="applyRequested" :disabled="sending" />
                    <span>{{ t('main.git_review.apply_requested_label') }}</span>
                  </label>
                  <textarea
                    v-model="messageDraft"
                    rows="2"
                    :disabled="sending || !canSend"
                    :placeholder="t('main.git_review.message_placeholder')"
                  ></textarea>
                  <div class="gmr-conv-buttons">
                    <button
                      type="button"
                      class="btn btn-secondary gmr-send-btn"
                      :disabled="sending || !canSend || !messageDraft.trim() || !selectedProvider"
                      @click="sendMessage(false)"
                    >
                      <AppIcon name="paper-plane-tilt" /> {{ t('main.git_review.send') }}
                    </button>
                    <button
                      v-if="heldTestOperations.length"
                      type="button"
                      class="btn btn-secondary gmr-allow-test-edits-btn"
                      :disabled="sending || !canSend || !messageDraft.trim() || !selectedProvider"
                      @click="sendMessage(true)"
                    >
                      <AppIcon name="flask" /> {{ t('main.git_review.allow_test_edits') }}
                    </button>
                  </div>
                </div>
              </section>
            </div>
          </div>

          <div class="modal-ft gmr-ft">
            <p class="gmr-ft-note">{{ t('main.git_review.apply_safety_note') }}</p>
            <div class="gmr-ft-actions">
              <button type="button" class="btn btn-danger-ol" :disabled="busy || !canReject" @click="openRejectPrompt">
                <AppIcon name="prohibit" /> {{ t('main.git_review.reject') }}
              </button>
              <button type="button" class="btn btn-primary" :disabled="busy || !canApprove" @click="approve">
                <AppIcon name="check" /> {{ t('main.git_review.approve') }}
              </button>
            </div>
          </div>

          <div v-if="rejectPromptOpen" class="gmr-reject-overlay">
            <div class="gmr-reject-box">
              <h3>{{ t('main.git_review.reject') }}</h3>
              <label>
                {{ t('main.git_review.reject_reason_label') }}
                <textarea v-model="rejectReason" rows="3" maxlength="4000"></textarea>
              </label>
              <label>
                {{ t('main.git_review.next_provider_label') }}
                <AiProviderSelect
                  :providers="providers"
                  :model-value="selectedProvider"
                  :loading="providerLoading"
                  :errored="providerErrored"
                  hide-label
                  @update:model-value="(v) => emit('update:provider', v)"
                />
              </label>
              <div class="gmr-reject-actions">
                <button type="button" class="btn btn-secondary" @click="rejectPromptOpen = false">{{ t('common.cancel') }}</button>
                <button type="button" class="btn btn-danger-ol" :disabled="busy || !rejectReason.trim() || !selectedProvider" @click="reject">
                  {{ t('main.git_review.reject_confirm') }}
                </button>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest, postRequest } from '@shared/api'
import AiProviderSelect from './AiProviderSelect.vue'
import { useToast } from './common/useToast'
import {
  buildDiffRows,
  collapseCommonRows,
  splitTextLines,
  toUnifiedRows,
  type DiffSection,
  type UnifiedRow,
} from '../composables/useFileDiff'

const { t } = useI18n()
const { showToast } = useToast()

const props = defineProps<{
  groupId: string
  mergeId: number
  branch?: string | null
  baseBranch?: string | null
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
}>()

const emit = defineEmits<{ close: []; resolved: []; 'update:provider': [value: string] }>()

interface ReviewChange {
  path: string
  status: string
  old_path: string | null
}
interface ConflictOrigin {
  path: string
  chunk_id: string
  selection: 'ours' | 'theirs' | 'both' | 'manual'
  start_line: number | null
  end_line: number | null
  range_ambiguous: boolean
}
interface ConversationTurn {
  turn_id: string
  role: 'human' | 'ai'
  message: string
  provider_id: string | null
  status: string
  created_at: string
}
interface HeldTestOperation {
  operation_id: string
  kind: string
  path: string
  purpose: string
}
interface ReviewPayload {
  group_id: string
  merge_id: number
  review_state: string
  review_fingerprint: string
  instruction_generation: number
  base_head: string | null
  merge_head: string | null
  changes: ReviewChange[]
  conflict_origins: ConflictOrigin[]
  conversation: ConversationTurn[]
  held_test_operations: HeldTestOperation[]
  resolver_provider: string | null
  reconciliation_kind: string | null
  last_error: { code?: string } | null
  can_approve: boolean
  can_reject: boolean
  can_send: boolean
}
interface ReviewDiffData {
  path: string
  status: string
  old: { content: string | null; binary: boolean; truncated: boolean }
  new: { content: string | null; binary: boolean; truncated: boolean }
}

const loading = ref(true)
const loadError = ref('')
const review = ref<ReviewPayload | null>(null)
const busy = ref(false)
const sending = ref(false)
const selectedPath = ref('')
const diff = ref<ReviewDiffData | null>(null)
const diffLoading = ref(false)
const diffError = ref(false)
const messageDraft = ref('')
const applyRequested = ref(false)
const rejectPromptOpen = ref(false)
const rejectReason = ref('')
const attemptId = ref('')
const awaitingReply = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | null = null

const changes = computed(() => review.value?.changes ?? [])
const conversation = computed(() => review.value?.conversation ?? [])
const heldTestOperations = computed(() => review.value?.held_test_operations ?? [])
const canApprove = computed(() => !!review.value?.can_approve)
const canReject = computed(() => !!review.value?.can_reject)
const canSend = computed(() => !!review.value?.can_send)

const REVIEW_STATE_LABELS: Record<string, string> = {
  resolved_pending_review: 'pending',
  re_review: 're_review',
  applying: 'applying',
  reconciling: 'reconciling',
}
const headerTitle = computed(() => {
  const key = REVIEW_STATE_LABELS[review.value?.review_state || ''] || 'pending'
  return t(`main.git_review.header.${key}`)
})
const warningClass = computed(() => {
  const state = review.value?.review_state
  if (state === 'reconciling') return 'gmr-warning-blocked'
  if (state === 're_review') return 'gmr-warning-attention'
  return ''
})
const warningIcon = computed(() => {
  const state = review.value?.review_state
  if (state === 'reconciling') return 'spinner'
  if (state === 're_review') return 'warning'
  return 'warning'
})
const warningText = computed(() => {
  const state = review.value?.review_state
  if (state === 'reconciling') return t('main.git_review.warning.reconciling')
  if (state === 're_review') return t('main.git_review.warning.re_review')
  return t('main.git_review.warning.pending')
})

function newAttemptId(): string {
  return typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`
}

function statusKind(status: string): 'added' | 'modified' | 'deleted' {
  if (status === 'D') return 'deleted'
  if (status === 'A') return 'added'
  return 'modified'
}
function statusBadge(status: string): string {
  return status.slice(0, 1)
}
function baseName(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx === -1 ? path : path.slice(idx + 1)
}
function dirName(path: string): string {
  const idx = path.lastIndexOf('/')
  return idx === -1 ? '' : path.slice(0, idx)
}
function conflictCountOf(path: string): number {
  return (review.value?.conflict_origins ?? []).filter((o) => o.path === path).length
}
const selectedOrigins = computed(() =>
  (review.value?.conflict_origins ?? []).filter((o) => o.path === selectedPath.value),
)
function selectionLabel(selection: string): string {
  return t(`main.git_finalize.${selection === 'ours' ? 'current' : selection === 'theirs' ? 'incoming' : selection === 'both' ? 'both' : 'direct_edit'}`)
}
function isOriginRow(rightNumber: number | null): boolean {
  if (rightNumber == null) return false
  return selectedOrigins.value.some(
    (o) => !o.range_ambiguous && o.start_line != null && o.end_line != null
      && rightNumber >= o.start_line && rightNumber <= o.end_line,
  )
}

const diffBinary = computed(() => !!diff.value?.old.binary || !!diff.value?.new.binary)
const diffRows = computed(() => {
  if (!diff.value || diffBinary.value) return { rows: [], approximate: false }
  return buildDiffRows(splitTextLines(diff.value.old.content ?? ''), splitTextLines(diff.value.new.content ?? ''))
})
const hasDiffChanges = computed(() => diffRows.value.rows.some((row) => row.status !== 'common'))
const diffSections = computed<DiffSection[]>(() => collapseCommonRows(diffRows.value.rows))
const unifiedDiffSections = computed(() =>
  diffSections.value.map((section) =>
    section.kind === 'gap'
      ? section
      : { kind: 'rows' as const, rows: toUnifiedRows(section.rows) as UnifiedRow[] },
  ),
)
function unifiedClass(rowStatus: string): string {
  if (rowStatus === 'removed') return 'gcd-line-del'
  if (rowStatus === 'added') return 'gcd-line-add'
  return ''
}

function select(path: string) {
  if (path === selectedPath.value) return
  selectedPath.value = path
  void loadDiff(path)
}

async function loadDiff(path: string) {
  if (!path) return
  diffLoading.value = true
  diffError.value = false
  try {
    const { data } = await getRequest<{ ok: boolean; data: ReviewDiffData }>(
      `/api/v1/groups/${props.groupId}/git/merge/${props.mergeId}/review-diff`,
      { path },
    )
    if (selectedPath.value === path) diff.value = data.data
  } catch {
    if (selectedPath.value === path) {
      diff.value = null
      diffError.value = true
    }
  } finally {
    if (selectedPath.value === path) diffLoading.value = false
  }
}

async function loadReview() {
  loading.value = true
  loadError.value = ''
  try {
    const { data } = await getRequest<{ ok: boolean; result: ReviewPayload }>(
      `/api/v1/groups/${props.groupId}/git/merge/${props.mergeId}/review`,
    )
    const prevConvLen = review.value?.conversation.length ?? -1
    review.value = data.result
    if (!attemptId.value) attemptId.value = newAttemptId()
    if (!selectedPath.value && data.result.changes.length) {
      selectedPath.value = data.result.changes[0].path
      void loadDiff(selectedPath.value)
    }
    if (prevConvLen >= 0 && data.result.conversation.length > prevConvLen) {
      awaitingReply.value = false
      stopPolling()
    }
  } catch (e: any) {
    loadError.value = e?.response?.data?.error?.message || t('main.git_finalize.failed')
  } finally {
    loading.value = false
  }
}

function stopPolling() {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
}
function startPolling() {
  stopPolling()
  let attempts = 0
  const tick = () => {
    attempts += 1
    if (attempts > 40 || !awaitingReply.value) return
    void loadReview().finally(() => {
      if (awaitingReply.value && attempts <= 40) pollTimer = setTimeout(tick, 2000)
    })
  }
  pollTimer = setTimeout(tick, 2000)
}

async function sendMessage(allowTestEdits = false) {
  const message = messageDraft.value.trim()
  if (!message || !props.selectedProvider || sending.value) return
  sending.value = true
  try {
    // The [테스트 편집 포함 재지시] button is the human's second, EXPLICIT
    // action (L0007 §2.7) — allow_test_edits is never carried by the ordinary
    // apply-requested toggle above, only by this dedicated action.
    await postRequest(`/api/v1/groups/${props.groupId}/git/merge/${props.mergeId}/review-message`, {
      message, provider_id: props.selectedProvider, provider_pinned: true,
      apply_requested: allowTestEdits ? true : applyRequested.value,
      allow_test_edits: allowTestEdits,
    })
    messageDraft.value = ''
    awaitingReply.value = true
    startPolling()
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    sending.value = false
  }
}

async function approve() {
  if (!review.value || busy.value) return
  busy.value = true
  try {
    const { data } = await postRequest<{ ok: boolean; result?: any; error?: any }>(
      `/api/v1/groups/${props.groupId}/git/merge/${props.mergeId}/approve`,
      { attempt_id: attemptId.value, review_fingerprint: review.value.review_fingerprint },
    )
    const status = data.result?.status
    if (status === 'completed' || status === 'merged' || status === 'already_applied') {
      showToast(t('main.git_review.approved_toast'), 'success')
      emit('resolved')
      emit('close')
      return
    }
    if (status === 're_review') {
      showToast(t('main.git_review.re_review_toast'), 'warning')
    } else if (status === 'reconciling') {
      showToast(t('main.git_review.reconciling_toast'), 'warning')
    } else {
      showToast(t('main.git_review.approve_failed_toast'), 'danger')
    }
    attemptId.value = newAttemptId()
    await loadReview()
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
  }
}

function openRejectPrompt() {
  rejectReason.value = ''
  rejectPromptOpen.value = true
}

async function reject() {
  const reason = rejectReason.value.trim()
  if (!reason || !props.selectedProvider || busy.value) return
  busy.value = true
  try {
    await postRequest(`/api/v1/groups/${props.groupId}/git/merge/${props.mergeId}/reject`, {
      reason, provider_id: props.selectedProvider, provider_pinned: true,
    })
    showToast(t('main.git_review.rejected_toast'), 'success')
    rejectPromptOpen.value = false
    emit('resolved')
    emit('close')
  } catch (e: any) {
    showToast(e?.response?.data?.error?.message || t('main.git_finalize.failed'), 'danger')
  } finally {
    busy.value = false
  }
}

onMounted(loadReview)
onBeforeUnmount(stopPolling)
</script>

<style scoped>
/* Shared with GroupChangesDialog.vue (styles are `scoped`, so duplicated rather
   than imported — same class names, two independent components). */
.gcd-mono { font-family: var(--mono, ui-monospace, monospace); }
.gcd-dot { margin: 0 5px; }
.gcd-retry {
  flex: 0 0 auto; display: inline-flex; align-items: center; gap: 6px; padding: 7px 11px;
  font-size: 0.74rem; border: 1px solid var(--border, #e2e8f0); border-radius: 8px;
  background: var(--bg, #fff); color: inherit; cursor: pointer;
}
.gcd-bd { flex: 1 1 auto; min-height: 0; display: grid; grid-template-columns: minmax(240px, 320px) minmax(0, 1fr); }
.gcd-filelist { min-height: 0; overflow: auto; padding: 8px; border-right: 1px solid var(--border, #e2e8f0); background: #f8fafc; }
.gcd-nomatch { margin: 12px 6px; font-size: 0.74rem; color: var(--text-m, #64748b); }
.gcd-file {
  width: 100%; display: flex; flex-direction: column; gap: 3px; padding: 8px 9px; margin-bottom: 5px;
  border: 1px solid transparent; border-radius: 8px; background: transparent; color: inherit; text-align: left; cursor: pointer;
}
.gcd-file:hover, .gcd-file.active { border-color: #bfdbfe; background: #fff; }
.gcd-file-top { display: flex; align-items: center; gap: 6px; min-width: 0; }
.gcd-file-name { overflow-wrap: anywhere; font: 600 0.75rem var(--mono, ui-monospace, monospace); }
.gcd-file-dir { font: 0.66rem var(--mono, ui-monospace, monospace); color: var(--text-m, #64748b); overflow-wrap: anywhere; }
.gcd-badge {
  flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center;
  width: 16px; height: 16px; border-radius: 4px; font-size: 0.62rem; font-weight: 700;
}
.gcd-badge-added { background: var(--success-bg, #dcfce7); color: var(--success, #15803d); }
.gcd-badge-modified { background: var(--warning-bg, #fef3c7); color: var(--warning, #b45309); }
.gcd-badge-deleted { background: var(--danger-bg, #fee2e2); color: var(--danger, #b91c1c); }
.gcd-diffwrap { min-width: 0; min-height: 0; display: flex; flex-direction: column; }
.gcd-diff-hd { flex: 0 0 auto; display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid var(--border, #e2e8f0); }
.gcd-diff-path {
  flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font: 700 0.76rem var(--mono, ui-monospace, monospace);
}
.gcd-diff-state { flex: 1 1 auto; display: flex; align-items: center; justify-content: center; gap: 10px; font-size: 0.82rem; color: var(--text-m, #64748b); }
.gcd-diff-error { flex-direction: column; }
.gcd-diff { flex: 1 1 auto; min-height: 0; overflow: auto; font: 0.76rem/1.55 var(--mono, ui-monospace, monospace); tab-size: 2; background: #fff; }
.gcd-gap {
  padding: 3px 12px; color: var(--text-m, #64748b); background: #f1f5f9;
  border-top: 1px solid var(--border, #e2e8f0); border-bottom: 1px solid var(--border, #e2e8f0); font-size: 0.71rem;
}
.gcd-line { display: grid; grid-template-columns: 46px 46px 14px minmax(0, 1fr); }
.gcd-ln { padding: 0 6px; text-align: right; color: var(--text-m, #94a3b8); background: #f8fafc; user-select: none; font-size: 0.7rem; }
.gcd-sign { text-align: center; color: var(--text-m, #94a3b8); }
.gcd-text { padding: 0 8px; white-space: pre-wrap; overflow-wrap: anywhere; }
.gcd-line-add, .gcd-text.gcd-line-add { background: #ecfdf5; }
.gcd-line-del, .gcd-text.gcd-line-del { background: #fef2f2; }

.gmr-hd-text p { margin: 3px 0 0; font-size: 0.78rem; color: var(--text-m); display: flex; align-items: center; gap: 6px; }
.gmr-state { flex: 1; display: flex; align-items: center; justify-content: center; gap: 10px; padding: 40px; color: var(--text-m); }
.gmr-state-error { flex-direction: column; }
.gmr-provider-badge {
  display: flex; align-items: center; gap: 8px; padding: 8px 18px;
  border-bottom: 1px solid var(--border, #e2e8f0); font-size: 0.82rem; color: var(--text);
}
.gmr-provider-badge small { color: var(--text-m); font-size: 0.7rem; }
.gmr-warning {
  margin: 0; padding: 8px 18px; display: flex; align-items: center; gap: 8px;
  font-size: 0.8rem; color: #92400e; background: #fffbeb; border-bottom: 1px solid #fde68a;
}
.gmr-warning-attention { color: #9a3412; background: #fff7ed; border-color: #fed7aa; }
.gmr-warning-blocked { color: #1e40af; background: #eff6ff; border-color: #bfdbfe; }
.gmr-modal-body { flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0; }
.gmr-bd { flex: 1; min-height: 0; grid-template-columns: minmax(200px, 260px) minmax(0, 1fr) minmax(260px, 320px); }
.gmr-file { position: relative; }
.gmr-conflict-count-badge {
  margin-top: 3px; display: inline-flex; align-items: center; gap: 4px;
  font-size: 0.66rem; color: #b45309;
}
.gmr-origin-tags { display: flex; flex-wrap: wrap; gap: 6px; padding: 6px 12px; border-bottom: 1px solid var(--border, #e2e8f0); }
.gmr-origin-tag { padding: 2px 8px; border-radius: 999px; font-size: 0.68rem; background: #f1f5f9; color: #334155; }
.gmr-origin-tag.gmr-sel-ours { background: #dbeafe; color: #1d4ed8; }
.gmr-origin-tag.gmr-sel-theirs { background: #dcfce7; color: #047857; }
.gmr-origin-tag.gmr-sel-both { background: #ede9fe; color: #6d28d9; }
.gmr-origin-tag.gmr-sel-manual { background: #fef3c7; color: #92400e; }
.gmr-origin-row { box-shadow: inset 3px 0 0 #f59e0b; }
.gmr-origin-flag { grid-column: 1 / -1; font-size: 0.65rem; }
.gmr-conversation {
  min-height: 0; display: flex; flex-direction: column; border-left: 1px solid var(--border, #e2e8f0);
}
.gmr-conv-hd { flex: 0 0 auto; display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border, #e2e8f0); font-size: 0.82rem; }
.gmr-conv-log { flex: 0 1 auto; min-height: 0; overflow: auto; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.gmr-conv-empty { color: var(--text-m); font-size: 0.76rem; }
.gmr-turn { font-size: 0.78rem; line-height: 1.4; }
.gmr-turn-human strong { color: #1d4ed8; }
.gmr-turn-ai strong { color: #047857; }
.gmr-conv-compose {
  flex: 0 0 auto; display: flex; flex-direction: column; gap: 6px; padding: 10px 12px;
  border-top: 1px solid var(--border, #e2e8f0); margin-top: auto;
}
.gmr-conv-label { font-size: 0.72rem; font-weight: 700; color: var(--text-m); }
.gmr-apply-toggle { display: inline-flex; align-items: center; gap: 6px; font-size: 0.74rem; color: var(--text-m); }
.gmr-conv-compose textarea {
  width: 100%; min-height: 56px; padding: 8px; border: 1px solid var(--border, #cbd5e1);
  border-radius: 6px; background: var(--bg, #fff); color: var(--text); font: inherit; font-size: 0.78rem; resize: vertical;
}
.gmr-conv-buttons { display: flex; justify-content: flex-end; gap: 8px; }
.gmr-send-btn, .gmr-allow-test-edits-btn { align-self: flex-end; }
.gmr-held-tests {
  flex: 0 0 auto; margin: 0 12px; padding: 8px 10px; border: 1px solid #fde68a;
  border-radius: 6px; background: #fffbeb; font-size: 0.74rem; color: #92400e;
}
.gmr-held-tests strong { display: block; margin-bottom: 3px; }
.gmr-held-note { margin: 0 0 6px; color: #92400e; }
.gmr-held-tests ul { margin: 0; padding-left: 16px; display: flex; flex-direction: column; gap: 2px; }
.gmr-ft { flex-direction: column; align-items: stretch; gap: 8px; }
.gmr-ft-note { margin: 0; font-size: 0.72rem; color: var(--text-m); }
.gmr-ft-actions { display: flex; justify-content: flex-end; gap: 10px; }
.gmr-reject-overlay {
  position: fixed; inset: 0; z-index: 1500; display: flex; align-items: center; justify-content: center;
  background: rgba(15, 23, 42, 0.46);
}
.gmr-reject-box {
  width: min(480px, calc(100vw - 48px)); display: flex; flex-direction: column; gap: 10px;
  padding: 18px; border-radius: 8px; background: var(--bg, #fff); color: var(--text);
  box-shadow: 0 24px 80px rgba(15, 23, 42, 0.3);
}
.gmr-reject-box h3 { margin: 0; font-size: 1rem; }
.gmr-reject-box label { display: flex; flex-direction: column; gap: 4px; font-size: 0.78rem; }
.gmr-reject-box textarea {
  padding: 8px; border: 1px solid var(--border, #cbd5e1); border-radius: 6px; font: inherit; font-size: 0.8rem;
}
.gmr-reject-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
