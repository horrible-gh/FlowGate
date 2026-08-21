<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box modal-aiv">
        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="robot" style="color:var(--primary); margin-right:6px;" />
            {{ t('main.ai_invoke_dialog.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd aiv-body">
          <!-- Invoke setup only; admitted runs move to the group-scoped inline strip. -->
            <div class="aiv-target-row">
              <span class="aiv-target-label">{{ t('main.ai_invoke_dialog.target_doc') }}</span>
              <span class="aiv-target-id">{{ docRef }}</span>
            </div>
            <div v-if="singleStepNoteActive" class="aiv-step-note" data-test="single-step-note">
              <div class="aiv-step-note-label">{{ t('main.ai_invoke_dialog.step_note_label') }}</div>
              <div v-if="singleStepNoteLoading" class="aiv-step-note-empty">
                {{ t('main.ai_invoke_dialog.step_note_loading') }}
              </div>
              <template v-else-if="singleStepNote">
                <div class="aiv-step-note-value">{{ singleStepNote }}</div>
                <div class="aiv-step-note-hint">{{ t('main.ai_invoke_dialog.step_note_auto') }}</div>
              </template>
              <div v-else class="aiv-step-note-empty">
                {{ t('main.ai_invoke_dialog.step_note_empty') }}
              </div>
            </div>
            <!-- 0234 B0001 RC2: confirm/change which provider this run uses, at the
                 invocation point itself (not only the global header dropdown). -->
            <div class="aiv-provider-row">
              <AiProviderSelect
                :providers="aiProviderStore.providers"
                :model-value="aiProviderStore.selectedProviderId"
                :loading="aiProviderStore.loading"
                :errored="!!aiProviderStore.error"
                @update:model-value="aiProviderStore.selectProvider"
              />
            </div>
            <label class="aiv-mode" :class="{ active: mode === 'single' }">
              <input v-model="mode" type="radio" value="single" />
              <span class="aiv-mode-text">
                <span class="aiv-mode-title">{{ t('main.ai_invoke_dialog.mode_single') }}</span>
                <span class="aiv-mode-desc">{{ t('main.ai_invoke_dialog.mode_single_desc') }}</span>
              </span>
            </label>
            <label v-if="canContinuous" class="aiv-mode" :class="{ active: mode === 'continuous' }">
              <input v-model="mode" type="radio" value="continuous" />
              <span class="aiv-mode-text">
                <span class="aiv-mode-title">{{ t('main.ai_invoke_dialog.mode_continuous') }}</span>
                <span class="aiv-mode-desc">{{ t('main.ai_invoke_dialog.mode_continuous_desc') }}</span>
              </span>
            </label>
            <div v-if="canContinuous && mode === 'continuous' && actionScope === 'workflow_decide'" class="aiv-seq-row">
              <AppIcon name="fast-forward" />
              <span class="aiv-seq-hint">{{ t('main.ai_invoke_dialog.target_to_end_hint') }}</span>
            </div>
            <!-- 0242 NR0003: how far the chain runs is picked from the real sequence, not typed
                 as a raw `목표 seq` number. Same picker the action-bar continuous-work path uses. -->
            <template v-if="pickerActive">
              <WorkflowStepPicker
                :doc-ref="sequenceDocRef || docRef"
                :active="pickerActive"
                :auto-handled-types="autoHandledTypes"
                :auto-handled-item-seqs="continuationAutoApproveItemSeqs"
                @change="onPickerChange"
              />
              <div v-if="pickerSummary" class="aiv-seq-summary">{{ pickerSummary }}</div>
            </template>
            <div v-if="startError" class="aiv-error">
              <AppIcon name="warning" /> {{ startError }}
              <button
                v-if="lockedGroupId"
                type="button"
                class="btn btn-warning btn-sm aiv-release-lease-btn"
                data-test="ai-invoke-dialog-release-lease"
                :disabled="releasingLease"
                @click="onReleaseLeaseClick"
              >{{ t('main.review_action_bar.btn_release_lease') }}</button>
            </div>
        </div>

        <!-- ── Footer ── -->
        <div class="modal-ft">
            <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
            <button type="button" class="btn btn-primary" :disabled="starting || !canStart" @click="start">
              <AppIcon name="lightning" /> {{ t('main.ai_invoke_dialog.btn_start') }}
            </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkflowStepPicker from './WorkflowStepPicker.vue'
import { useAiProviderStore } from '../stores/aiProvider'
import { aiInvokeGroupId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import type { WorkflowStepItem, WorkflowStepPickerState } from '../types/workflowStepPicker'
import { findSequenceHeadIndex } from '../composables/useSequenceStepNote'

const props = defineProps<{
  visible: boolean
  project: string
  module?: string | null
  group: string
  docRef: string
  /**
   * Sequence-owning root (R/B) doc id for the continuous-target picker. Distinct from
   * `docRef`, which is the document the run ACTS on and may be a member doc (T/TR/…) —
   * /workflow/sequence is keyed by the root only, so a member doc there returns no sequence
   * (0242 NR0003 recommendation 2). Defaults to `docRef` for callers that already pass a root.
   */
  sequenceDocRef?: string
  actionScope: 'new' | 'edit' | 'workflow_decide' | 'chat' | 'rework' | 'review' | 'vr_correction' | 'next_step_message' | 'design_handoff'
  initialMode?: 'single' | 'continuous'
  initialTargetSeq?: number | null
  continuationReviewMode?: boolean
  continuationInstructionMode: 'auto_approved' | 'ai_direct'
  // 0352 T0004 §2/§3.7: ai_direct-only per-item_seq N/T server-auto-approve selection chosen
  // in ContinuousWorkDialog. Session-scoped, same lifetime as the fields around it.
  continuationAutoApproveItemSeqs?: number[]
  // 0317 T0010 rev4: item_seq -> provider_id, for steps the user explicitly overrode in
  // ContinuousWorkDialog. Session-scoped — rides this start request only.
  providerOverrides?: Record<number, string>
  // 0346 T0005: [전달멘트] tab values from ContinuousWorkDialog — a common note for every
  // hop, and item_seq -> note for steps the user singled out. Session-scoped, same as above.
  defaultMessage?: string
  messageOverrides?: Record<number, string>
  // flowgate.default.0400 M0005: the per-hop wall-clock budget (seconds) chosen in
  // ContinuousWorkDialog's time section. null/omitted ⇒ the server falls back to its own
  // default (this dialog's own continuous picker, reached without ContinuousWorkDialog, never
  // sets this).
  continuationStepTimeoutSec?: number | null
  // flowgate.default.0443 T0002 (R0001): the dialog's "재시작 횟수" pick, forwarded the
  // same session-scoped way as the budget pick above.
  continuationRestartMaxAttempts?: number | null
  autoStart?: boolean
  // Parallel-invoke extras (group 0223): context the matching copy-mention flow
  // assembles client-side; forwarded so the server rebuilds the identical prompt.
  selectedDocs?: string[] | null
  messages?: string[] | null
  rejectReason?: string | null
  designTypes?: string[] | null
  designMode?: string | null
  designFirstLabel?: string | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
}>()

const { t } = useI18n()
const aiProviderStore = useAiProviderStore()
const aiInvokeStore = useAiInvokeRunsStore()

const mode = ref<'single' | 'continuous'>('single')
const starting = ref(false)
const startError = ref('')
const singleStepNote = ref('')
const singleStepNoteLoading = ref(false)
// 0401 NR0003 §3 cause 4 / T0004 task 6: a group whose 409 named a dead run_id -- the lease's
// group id, so the inline [잠금 해제] button below has something to release.
const lockedGroupId = ref<string | null>(null)
const releasingLease = ref(false)
const picker = ref<WorkflowStepPickerState>({
  loading: true,
  errorKey: null,
  allDone: false,
  fromDecision: false,
  selection: null,
})

// Continuous chains only make sense for the scopes the server can chain
// (group 0223): the parallel-invoke extras are one-shot actions.
const canContinuous = computed(() =>
  props.actionScope === 'new' || props.actionScope === 'edit' || props.actionScope === 'workflow_decide',
)

// Show the picker only where the user actually chooses a target: a workflow_decide run has no
// sequence to pick from (it runs to the end of whatever the AI decides), and an autoStart run
// (the action-bar continuous-work path) already picked its target in ContinuousWorkDialog and closes
// immediately — mounting the picker there would refetch the sequence and clobber that choice.
const pickerActive = computed(() =>
  canContinuous.value &&
  mode.value === 'continuous' &&
  props.actionScope !== 'workflow_decide' &&
  !props.autoStart,
)

const singleStepNoteActive = computed(() =>
  mode.value === 'single' &&
  ['new', 'next_step_message', 'design_handoff'].includes(props.actionScope),
)

async function loadSingleStepNote() {
  singleStepNoteLoading.value = true
  singleStepNote.value = ''
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', {
      doc_id: props.sequenceDocRef || props.docRef,
    })
    const items = (res.data?.items ?? []) as WorkflowStepItem[]
    const headIndex = findSequenceHeadIndex(items)
    if (headIndex < items.length) singleStepNote.value = String(items[headIndex].note ?? '').trim()
  } catch {
    // Display-only enrichment follows the server's best-effort contract: a lookup problem is
    // rendered as the explicit empty state and never blocks the single run.
    singleStepNote.value = ''
  } finally {
    singleStepNoteLoading.value = false
  }
}

// 0337 R0001-1: this dialog starts its chain with the same instruction mode the request carries
// (auto_approved unless told otherwise), so its picker must exclude the same server-approved
// N/T steps as ContinuousWorkDialog — otherwise the two continuous entry points would disagree
// on what a valid stop point is.
const autoHandledTypes = computed(() =>
  props.continuationInstructionMode === 'auto_approved' ? ['N', 'T'] : [],
)

/** The chain's stop point, or null when nothing runnable is chosen yet. */
const resolvedTarget = computed<{ seq: number; fromDecision: boolean } | null>(() => {
  if (props.actionScope === 'workflow_decide') return { seq: -1, fromDecision: true }
  if (pickerActive.value) {
    const sel = picker.value.selection
    return sel ? { seq: sel.targetSeq, fromDecision: sel.fromDecision } : null
  }
  // autoStart: the target came in as a preset from the ContinuousWorkDialog path.
  return props.initialTargetSeq != null
    ? { seq: props.initialTargetSeq, fromDecision: props.initialTargetSeq === -1 }
    : null
})

const canStart = computed(() => mode.value === 'single' || resolvedTarget.value != null)

const pickerSummary = computed(() => {
  if (picker.value.loading || picker.value.errorKey) return ''
  if (picker.value.allDone) return t('main.continuous_work.all_done_summary')
  if (picker.value.fromDecision) return t('main.continuous_work.from_decision_summary')
  const sel = picker.value.selection
  if (!sel) return ''
  return t('main.continuous_work.summary', { count: sel.stepCount, target: sel.targetLabel })
})

function onPickerChange(state: WorkflowStepPickerState) {
  picker.value = state
}

function resetState() {
  mode.value = canContinuous.value ? (props.initialMode ?? 'single') : 'single'
  starting.value = false
  startError.value = ''
  lockedGroupId.value = null
}

// 0401 NR0003 §3 cause 4: the 409 body always names a run_id, whether or not it is still
// alive -- the server-side end record (T0004 task 1-2) means a dead one now answers with a
// persisted status='finished' payload instead of 404, so both cases are readable here.
async function checkRunLive(runId: string): Promise<boolean> {
  try {
    const res = await getRequest<any>(`/api/v1/ai-invoke/${encodeURIComponent(runId)}`)
    const status = res.data?.status
    return status === 'running' || status === 'pause_requested'
  } catch (e: any) {
    // Unknown run_id ⇒ definitely not live. Any other failure fails toward "live" (the
    // pre-T0004 behaviour) so a transient lookup error cannot dead-end the dialog either.
    return e?.response?.status !== 404
  }
}

async function onReleaseLeaseClick(): Promise<void> {
  if (!lockedGroupId.value || releasingLease.value) return
  releasingLease.value = true
  try {
    await aiInvokeStore.releaseGroupLease(lockedGroupId.value)
    lockedGroupId.value = null
    startError.value = ''
  } catch {
    startError.value = t('main.ai_invoke_dialog.error_release_lease_failed')
  } finally {
    releasingLease.value = false
  }
}

async function start() {
  if (starting.value || !canStart.value) return
  starting.value = true
  startError.value = ''
  try {
    await aiProviderStore.ensureLoaded(props.project)
    const target = resolvedTarget.value
    // A continuous run picked before the workflow is decided must start FROM the decision step,
    // exactly as the ContinuousWorkDialog path does (MainPanel.onContinuousWarnConfirm): the
    // scope becomes workflow_decide and the run-to-end sentinel stands in for a target that
    // does not exist yet. workflow_decide is keyed by the sequence ROOT, not the acted-on doc.
    const preDecision = mode.value === 'continuous' && !!target?.fromDecision
    const scope = preDecision ? 'workflow_decide' : props.actionScope
    const body: Record<string, unknown> = {
      project: props.project,
      group: props.group,
      doc_ref: preDecision ? (props.sequenceDocRef || props.docRef) : props.docRef,
      action_scope: scope,
      mode: mode.value,
    }
    // 0448 T0005 §5-1. Two independent request states, never one:
    //   provider_id            — the ordinary selection (aiProviderStore.selectProvider), i.e.
    //                            the default for hops that stored no provider of their own.
    //   provider_pinned=true   — force-all, and ONLY when the run went through the explicit
    //                            aiProviderStore.forceProviderForAllSteps API. `pinned` can no
    //                            longer be set by a selector's change event, so an ordinary
    //                            pick sends provider_id alone and the server keeps running each
    //                            step's stored provider (start_run tier 3 beats tier 4).
    if (aiProviderStore.selectedProviderId) body.provider_id = aiProviderStore.selectedProviderId
    if (aiProviderStore.pinned) body.provider_pinned = true
    if (props.module != null) body.module = props.module
    if (props.selectedDocs?.length) body.selected_docs = props.selectedDocs
    if (props.messages?.length) body.messages = props.messages
    if (props.rejectReason) body.reject_reason = props.rejectReason
    if (props.designTypes?.length) body.design_types = props.designTypes
    if (props.designMode) body.design_mode = props.designMode
    if (props.designFirstLabel) body.design_first_label = props.designFirstLabel
    if (mode.value === 'continuous') {
      body.continuation_target_seq = target?.seq ?? null
      body.continuation_review_mode = !!props.continuationReviewMode
      body.continuation_instruction_mode = props.continuationInstructionMode
      // 0448 T0005 §5-2: item_seq -> provider_id, independent of provider_id/provider_pinned
      // above. One fixed rule for the empty case — an empty map is OMITTED, exactly like the
      // note map below, so "no per-step override" reaches the server as an absent key and never
      // as `{}`. A present map is forwarded verbatim (the only producer of this wire key).
      if (props.providerOverrides && Object.keys(props.providerOverrides).length) {
        body.continuation_provider_overrides = props.providerOverrides
      }
      if (props.defaultMessage) body.continuation_default_note = props.defaultMessage
      if (props.messageOverrides && Object.keys(props.messageOverrides).length) {
        body.continuation_note_overrides = props.messageOverrides
      }
      if (props.continuationStepTimeoutSec) {
        body.continuation_step_timeout_sec = props.continuationStepTimeoutSec
      }
      // 0 and -1 are both meaningful restart-count picks ("재실행 안 함" / "될 때까지"),
      // so this must not use a truthy check like the budget pick above.
      if (props.continuationRestartMaxAttempts != null) {
        body.continuation_restart_max_attempts = props.continuationRestartMaxAttempts
      }
      // 0352 T0004 §2/§3.7: never sent for a pre-decision (workflow_decide) start — no
      // item_seq exists yet, and the server rejects a selection on that scope (§2).
      if (!preDecision && props.continuationAutoApproveItemSeqs?.length) {
        body.continuation_auto_approve_item_seqs = props.continuationAutoApproveItemSeqs
      }
    }
    const res = await postRequest<any>('/api/v1/ai-invoke/start', body)
    const data = res.data
    const groupId = aiInvokeGroupId(props.project, props.module, props.group)
    aiInvokeStore.trackStarted({
      ...data,
      group_id: data.group_id ?? groupId,
      doc_ref: data.doc_ref ?? props.docRef,
    })
    // Starting an invoke is a short setup interaction. Once admitted, progress
    // belongs to the group-scoped inline indicator and must not block the UI.
    emit('update:visible', false)
  } catch (e: any) {
    const status = e?.response?.status
    const data = e?.response?.data ?? {}
    if (status === 409 && data.code === 'run_in_progress' && data.run_id) {
      const groupId = data.group_id ?? aiInvokeGroupId(props.project, props.module, props.group)
      // 0401 NR0003 §3 cause 4: the 409 body always names A run_id, live or not -- adopting it
      // unconditionally closed this dialog onto a run that was already gone, and the very next
      // poll turned it back into the '실행 기록이 소실되었습니다' card this run was trying to
      // escape. Verify liveness first; only a genuinely live run gets adopted (scenario 8 restore).
      if (await checkRunLive(data.run_id)) {
        aiInvokeStore.trackStarted({
          run_id: data.run_id,
          group_id: groupId,
          doc_ref: props.docRef,
          mode: mode.value,
        })
        void aiInvokeStore.refresh(groupId)
        emit('update:visible', false)
      } else {
        lockedGroupId.value = groupId
        startError.value = t('main.ai_invoke_dialog.error_run_in_progress_orphaned')
      }
    } else if (status === 409 && data.code === 'no_provider_registered') {
      // 0292 T0003: distinct from no_enabled_provider — there is nothing in AI settings
      // to switch on, so point at the seed script instead of at a toggle.
      startError.value = t('main.ai_invoke_dialog.error_no_provider_registered')
    } else if (status === 409 && data.code === 'no_enabled_provider') {
      startError.value = t('main.ai_invoke_dialog.error_no_provider')
    } else if (status === 422) {
      const msgs = (data.errors ?? []).map((er: any) => `${er.loc}: ${er.msg}`).join(' / ')
      startError.value = msgs || t('main.ai_invoke_dialog.error_start_failed')
    } else {
      startError.value = data.message ?? t('main.ai_invoke_dialog.error_start_failed')
    }
  } finally {
    starting.value = false
  }
}

function close() {
  emit('update:visible', false)
}

watch(
  [() => props.visible, singleStepNoteActive, () => props.sequenceDocRef, () => props.docRef],
  ([visible, active]) => {
    if (visible && active) void loadSingleStepNote()
  },
  { immediate: true },
)

watch(
  () => props.visible,
  (val) => {
    if (val) {
      resetState()
      // Load the runtime provider list so the confirm/change selector is populated as
      // soon as the dialog opens (RC2). start() also ensures this for the autoStart path.
      void aiProviderStore.ensureLoaded(props.project)
      if (props.autoStart) void start()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.modal-aiv {
  width: 520px;
  max-width: 96vw;
  display: flex;
  flex-direction: column;
  max-height: 88vh;
}
.modal-aiv .modal-bd {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.aiv-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.aiv-target-row {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: .82rem;
}
.aiv-target-label { color: var(--text-m); }
.aiv-target-id { font-family: 'JetBrains Mono', monospace; color: var(--text); }
.aiv-step-note {
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface-h);
  padding: 10px 12px;
}
.aiv-step-note-label { font-size: .72rem; font-weight: 700; color: var(--text-m); margin-bottom: 5px; }
.aiv-step-note-value { white-space: pre-wrap; font-size: .82rem; color: var(--text); }
.aiv-step-note-hint, .aiv-step-note-empty { font-size: .74rem; color: var(--text-m); line-height: 1.4; }
.aiv-step-note-hint { margin-top: 5px; }
.aiv-provider-row { display: flex; }
.aiv-provider-row > * { flex: 1; min-width: 0; }
.aiv-mode {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
}
.aiv-mode.active { border-color: var(--primary); background: var(--primary-l); }
.aiv-mode input { margin-top: 3px; flex-shrink: 0; }
.aiv-mode-text { display: flex; flex-direction: column; gap: 2px; }
.aiv-mode-title { font-size: .85rem; font-weight: 600; color: var(--text); }
.aiv-mode-desc { font-size: .76rem; color: var(--text-m); line-height: 1.4; }
.aiv-seq-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-left: 4px;
}
.aiv-seq-hint { font-size: .72rem; color: var(--text-m); }
.aiv-seq-summary {
  font-size: .82rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface-h);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
.aiv-error {
  font-size: .8rem;
  color: var(--danger);
  background: var(--danger-light, #fee2e2);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
.aiv-release-lease-btn {
  display: block;
  margin-top: 6px;
}
</style>
