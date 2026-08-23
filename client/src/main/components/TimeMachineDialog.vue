<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-tmd" role="dialog" aria-modal="true" aria-labelledby="tmd-title">

        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="tmd-title">
            <AppIcon name="clock-counter-clockwise" style="color:var(--warning, #d97706); margin-right:6px;" />{{ t('main.time_machine.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body — 0332 D0005 §6.4: after a rewind whose source cancel did not fully
             succeed, the dialog does NOT close; the body becomes the result screen. -->
        <div class="modal-bd tmd-body">
          <template v-if="cancelResult">
            <p class="tmd-result-head">{{ t('main.time_machine.result_title') }}</p>
            <ul class="tmd-result-list">
              <li
                v-for="row in resultRows"
                :key="row.key"
                class="tmd-result-row"
                :class="{ 'tmd-result-row--ok': row.ok }"
              >
                <AppIcon :name="row.ok ? 'check-circle' : 'warning'" />
                <span v-if="row.code" class="tmd-result-code">{{ row.code }}</span>
                <span class="tmd-result-msg">{{ row.message }}</span>
              </li>
            </ul>
          </template>

          <template v-else>
            <p class="tmd-desc">{{ t('main.time_machine.desc') }}</p>

            <div v-if="loading" class="tmd-loading">
              <AppIcon name="spinner" spin /> {{ t('main.time_machine.loading') }}
            </div>

            <div v-else-if="steps.length === 0" class="tmd-empty">
              {{ t('main.time_machine.empty') }}
            </div>

            <ul v-else class="tmd-step-list">
              <li
                v-for="step in steps"
                :key="step.docId"
                class="tmd-step"
                :class="{
                  'tmd-step--selected': selectedDocId === step.docId,
                  'tmd-step--affected': isAffected(step),
                }"
                @click="selectedDocId = step.docId"
              >
                <span class="tmd-step-radio">
                  <AppIcon :name="selectedDocId === step.docId ? 'radio-button' : 'circle'" />
                </span>
                <span class="tmd-step-type">{{ typeLabel(step.typeCode) }}</span>
                <span class="tmd-step-title">{{ step.title || step.docId }}</span>
                <!-- 0332 D0005 §6.3 — what this step's source commit is, BEFORE the press. -->
                <span class="tmd-step-commit" :class="`tmd-step-commit--${commitOf(step).kind}`">
                  <AppIcon v-if="commitOf(step).kind === 'merged'" name="lock" />
                  {{ commitOf(step).label }}
                </span>
              </li>
            </ul>

            <p v-if="selectedStep" class="tmd-cascade-note">
              <AppIcon name="warning" />
              {{ t('main.time_machine.cascade_note', { step: typeLabel(selectedStep.typeCode) }) }}
            </p>
            <p v-if="selectedStep" class="tmd-cancel-summary">{{ cancelSummary }}</p>
          </template>
        </div>

        <!-- Footer -->
        <div class="modal-ft tmd-footer">
          <template v-if="cancelResult">
            <button
              v-if="cancelResult.blocked_reason === 'already_merged'"
              type="button"
              class="btn btn-outline btn-sm tmd-open-git-btn"
              @click="emit('open-git-panel')"
            >
              <AppIcon name="tree-structure" /> {{ t('main.time_machine.btn_open_git_panel') }}
            </button>
            <button
              v-if="cancelResult.retryable"
              type="button"
              class="btn btn-warning btn-sm tmd-retry-btn"
              :disabled="retrying"
              @click="emit('retry-cancel')"
            >
              <template v-if="retrying">
                <AppIcon name="spinner" spin /> {{ t('main.time_machine.retrying') }}
              </template>
              <template v-else>
                <AppIcon name="arrow-counter-clockwise" /> {{ t('main.time_machine.btn_retry') }}
              </template>
            </button>
            <button type="button" class="btn btn-outline btn-sm tmd-close-btn" @click="onClose">
              {{ t('main.time_machine.btn_close') }}
            </button>
          </template>

          <template v-else>
            <button type="button" class="btn btn-outline btn-sm" @click="onClose">
              {{ t('common.cancel') }}
            </button>
            <button
              type="button"
              class="btn btn-warning btn-sm"
              :disabled="!selectedStep || submitting"
              @click="onConfirm"
            >
              <template v-if="submitting">
                <AppIcon name="spinner" spin /> {{ t('main.time_machine.reopening') }}
              </template>
              <template v-else>
                <AppIcon name="clock-counter-clockwise" /> {{ t('main.time_machine.confirm') }}
              </template>
            </button>
          </template>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocTypeStore } from '../stores/docTypeStore'
import AppIcon from '@shared/AppIcon.vue'

interface TimeMachineStep {
  docId: string
  seq: number
  typeCode: string | null
  title: string | null
}

/** 0332 P0006 §2 — one row per TR commit the group has made. */
interface TrCommitPreviewRow {
  seq: number | null
  doc_id: string
  doc_code: string
  commit: string | null
  subject: string | null
  status: 'live' | 'canceled'
  cancel_commit: string | null
}
interface TrCommitPreview {
  group_status: 'active' | 'already_merged' | 'no_worktree' | 'git_inactive'
  commits: TrCommitPreviewRow[]
}

/** 0332 P0006 §3 — what the rewind did to those commits. */
interface TrCommitCancel {
  attempted: boolean
  blocked_reason: string | null
  canceled: { doc_id: string; doc_code: string; commit: string | null; cancel_commit: string | null }[]
  skipped: { doc_id: string; doc_code: string; commit: string | null; reason: string }[]
  stopped_reason: string | null
  retryable: boolean
  /** 0332 TR0019 — present when the conflict was kept as a resolvable session
   *  instead of being wiped; that is what decides which sentence this screen shows. */
  conflict_session?: { merge_id: number } | null
}

const props = defineProps<{
  visible: boolean
  steps: TimeMachineStep[]
  loading?: boolean
  /** 0018 R0001 — when opened from a workflow-strip step click, pre-select that step's
   *  document so the confirm targets the clicked step (still changeable in the picker). */
  preselectDocId?: string | null
  /** 0332 D0005 §6.3 — the group's commit state as of the moment the dialog opened.
   *  `null`/absent means the preview could not be read: every step line then says
   *  "확인할 수 없음" and the confirm button stays enabled (git never blocks a rewind). */
  commitPreview?: TrCommitPreview | null
  /** 0332 D0005 §6.4 — set by the parent when the rewind left something un-canceled;
   *  its presence turns this dialog into the result screen. */
  cancelResult?: TrCommitCancel | null
  retrying?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'confirm': [step: TimeMachineStep]
  'retry-cancel': []
  'open-git-panel': []
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const overlayRef = ref<HTMLElement | null>(null)
const selectedDocId = ref<string | null>(null)
const submitting = ref(false)

const selectedStep = computed(() => props.steps.find(s => s.docId === selectedDocId.value) ?? null)

function typeLabel(typeCode: string | null): string {
  return typeCode ? docTypeStore.getLabel(typeCode) : ''
}

// ── commit column (D0005 §6.3) ──────────────────────────────────────────────
// A step with no preview row is not an error: it means that approval changed no
// source. "확인할 수 없음" is reserved for the one case where we genuinely do not
// know — the preview call itself failed.
function commitOf(step: TimeMachineStep): { kind: string; label: string } {
  const preview = props.commitPreview
  if (!preview) return { kind: 'unknown', label: t('main.time_machine.commit_unknown') }
  if (preview.group_status === 'already_merged') {
    return { kind: 'merged', label: t('main.time_machine.commit_already_merged') }
  }
  // 0332 TR0014 검토 — no_worktree/git_inactive도 already_merged와 같은 자리에서 걸러야
  // 한다: 실제 취소가 group_status를 같은 순서로 읽어(L0007 §3) 이 두 값에서도 아무 것도
  // 시도하지 않고 바로 blocked_reason으로 답한다. 여기서 살아 있는 해시를 보이면 창이
  // "취소된다"고 약속한 뒤 확인은 "취소 못 함"으로 답하게 된다.
  if (preview.group_status === 'no_worktree') {
    return { kind: 'blocked', label: t('main.time_machine.commit_no_worktree') }
  }
  if (preview.group_status === 'git_inactive') {
    return { kind: 'blocked', label: t('main.time_machine.commit_git_inactive') }
  }
  const row = rowOf(step)
  if (!row) return { kind: 'none', label: t('main.time_machine.commit_none') }
  if (row.status === 'canceled') {
    return { kind: 'canceled', label: t('main.time_machine.commit_already_canceled') }
  }
  return { kind: 'live', label: row.commit ?? '' }
}

// Newest live row wins when a step was rewound and re-approved: that is the commit a
// cancel would peel off now.
function rowOf(step: TimeMachineStep): TrCommitPreviewRow | null {
  const rows = (props.commitPreview?.commits ?? []).filter(c => c.doc_id === step.docId)
  return rows.find(c => c.status === 'live') ?? rows[rows.length - 1] ?? null
}

// The cancel range is the selected step AND everything after it, so those lines are
// highlighted together — the same range the cascade warning already describes.
function isAffected(step: TimeMachineStep): boolean {
  const selected = selectedStep.value
  return !!selected && step.seq >= selected.seq
}

const affectedLiveCount = computed(() => {
  const selected = selectedStep.value
  if (!selected || !props.commitPreview) return 0
  return props.commitPreview.commits.filter(
    c => c.status === 'live' && (c.seq ?? 0) >= selected.seq,
  ).length
})

const cancelSummary = computed(() => {
  const status = props.commitPreview?.group_status
  if (status === 'already_merged') {
    return t('main.time_machine.cancel_summary_merged')
  }
  if (status === 'no_worktree') {
    return t('main.time_machine.cancel_summary_no_worktree')
  }
  if (status === 'git_inactive') {
    return t('main.time_machine.cancel_summary_git_inactive')
  }
  const n = affectedLiveCount.value
  return n > 0
    ? t('main.time_machine.cancel_summary_n', { n })
    : t('main.time_machine.cancel_summary_none')
})

// ── result screen (D0005 §6.4) ──────────────────────────────────────────────
// Every line says what happened AND, when there is one, what to do next. A blocked
// result has no per-TR lines at all — nothing was attempted — so the reason itself
// is the single line.
function skipMessage(reason: string): string {
  // TR0019 — a conflict is only a dead end when parking it failed. Once the server keeps
  // it as a session there IS a next step, so the line has to name the button that finishes
  // it instead of sending the person off to sort out their worktree by hand.
  if (reason === 'conflict') {
    return props.cancelResult?.conflict_session
      ? t('main.time_machine.reason_conflict_parked')
      : t('main.time_machine.reason_conflict')
  }
  const key = {
    already_canceled: 'main.time_machine.reason_already_canceled',
    conflict: 'main.time_machine.reason_conflict',
    not_attempted: 'main.time_machine.reason_not_attempted',
  }[reason]
  return key ? t(key) : reason
}

function blockMessage(reason: string): string {
  const key = {
    already_merged: 'main.time_machine.reason_already_merged',
    dirty_worktree: 'main.time_machine.reason_dirty_worktree',
    git_busy: 'main.time_machine.reason_git_busy',
    no_worktree: 'main.time_machine.reason_no_worktree',
    git_inactive: 'main.time_machine.reason_git_inactive',
  }[reason]
  return key ? t(key) : reason
}

const resultRows = computed(() => {
  const result = props.cancelResult
  if (!result) return [] as { key: string; ok: boolean; code: string; message: string }[]
  const rows: { key: string; ok: boolean; code: string; message: string }[] = []
  if (result.blocked_reason) {
    rows.push({
      key: `blocked:${result.blocked_reason}`,
      ok: false,
      code: '',
      message: blockMessage(result.blocked_reason),
    })
  }
  result.canceled.forEach((c, i) => rows.push({
    key: `ok:${c.doc_id}:${i}`,
    ok: true,
    code: c.doc_code,
    message: t('main.time_machine.result_canceled', { commit: c.cancel_commit ?? '' }),
  }))
  result.skipped.forEach((s, i) => rows.push({
    key: `skip:${s.doc_id}:${i}`,
    ok: false,
    code: s.doc_code,
    message: skipMessage(s.reason),
  }))
  return rows
})

watch(
  () => props.visible,
  (v) => {
    if (v) {
      // 0018 R0001 — honour a strip-click pre-selection; AC-reject opens with none (null).
      selectedDocId.value = props.preselectDocId ?? null
      submitting.value = false
      nextTick(() => overlayRef.value?.focus())
    }
  },
)

// The rewind answered: stop the spinner on the confirm button. Without this the dialog
// would sit on "되돌리는 중…" behind the result screen and a retry could never re-arm it.
watch(() => props.cancelResult, () => { submitting.value = false })

function onClose() {
  emit('update:visible', false)
}

function onConfirm() {
  if (!selectedStep.value || submitting.value) return
  submitting.value = true
  emit('confirm', selectedStep.value)
}
</script>

<style scoped>
.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
}

.modal-box {
  background: var(--bg-card, #fff);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
  overflow: hidden;
}

.modal-tmd {
  width: 480px;
}

.modal-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.modal-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text, #1e293b);
}

.modal-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  color: var(--text-m, #64748b);
  padding: 4px 6px;
  border-radius: 4px;
  transition: background 0.1s;
}
.modal-close:hover {
  background: var(--bg-hover, #f1f5f9);
}

.modal-bd {
  padding: 20px;
  overflow-y: auto;
}

.tmd-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.tmd-desc {
  font-size: 0.875rem;
  color: var(--text-m, #64748b);
  margin: 0;
  line-height: 1.5;
}

.tmd-loading,
.tmd-empty {
  font-size: 0.875rem;
  color: var(--text-m, #64748b);
  padding: 16px 0;
  text-align: center;
}

.tmd-step-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tmd-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  cursor: pointer;
  transition: border-color 0.1s, background 0.1s;
}
.tmd-step:hover {
  background: var(--bg-hover, #f1f5f9);
}
.tmd-step--selected {
  border-color: var(--primary, #2563eb);
  background: var(--bg-sub, #f8fafc);
}
/* The cancel range is "this step and everything after it" — show it as one block. */
.tmd-step--affected {
  border-left: 3px solid var(--warning, #d97706);
}

.tmd-step-radio {
  color: var(--primary, #2563eb);
  flex-shrink: 0;
}

.tmd-step-type {
  font-weight: 600;
  font-size: 0.8125rem;
  color: var(--text, #1e293b);
  flex-shrink: 0;
}

.tmd-step-title {
  font-size: 0.8125rem;
  color: var(--text-m, #64748b);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.tmd-step-commit {
  margin-left: auto;
  flex-shrink: 0;
  font-size: 0.75rem;
  font-family: var(--font-mono, ui-monospace, monospace);
  color: var(--text-m, #64748b);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tmd-step-commit--live {
  color: var(--text, #1e293b);
}
.tmd-step-commit--none,
.tmd-step-commit--unknown {
  font-family: inherit;
  opacity: 0.7;
}
.tmd-step-commit--canceled {
  font-family: inherit;
  text-decoration: line-through;
}
.tmd-step-commit--merged,
.tmd-step-commit--blocked {
  font-family: inherit;
  color: var(--warning, #d97706);
}

.tmd-cascade-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 0.8125rem;
  color: var(--warning, #d97706);
  background: var(--bg-sub, #fffbeb);
  border-radius: 6px;
  padding: 10px 12px;
  margin: 0;
  line-height: 1.45;
}

.tmd-cancel-summary {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--text-m, #64748b);
  line-height: 1.45;
}

.tmd-result-head {
  margin: 0;
  font-size: 0.875rem;
  font-weight: 600;
  color: var(--text, #1e293b);
  line-height: 1.5;
}

.tmd-result-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.tmd-result-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 0.8125rem;
  line-height: 1.45;
  color: var(--warning, #d97706);
  background: var(--bg-sub, #f8fafc);
  border-radius: 6px;
  padding: 10px 12px;
}
.tmd-result-row--ok {
  color: var(--text-m, #64748b);
}

.tmd-result-code {
  font-weight: 600;
  flex-shrink: 0;
  color: var(--text, #1e293b);
}

.tmd-result-msg {
  min-width: 0;
}

.modal-ft {
  padding: 14px 20px;
  border-top: 1px solid var(--border, #e2e8f0);
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}
</style>
