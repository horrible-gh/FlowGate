<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="onBackdrop">
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
                 as a raw `목표 seq` number. Same picker the action-bar 연속 작업 path uses. -->
            <template v-if="pickerActive">
              <WorkflowStepPicker
                :doc-ref="sequenceDocRef || docRef"
                :active="pickerActive"
                @change="onPickerChange"
              />
              <div v-if="pickerSummary" class="aiv-seq-summary">{{ pickerSummary }}</div>
            </template>
            <div v-if="startError" class="aiv-error">
              <AppIcon name="warning" /> {{ startError }}
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
import { postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkflowStepPicker from './WorkflowStepPicker.vue'
import { useAiProviderStore } from '../stores/aiProvider'
import { aiInvokeGroupId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'
import type { WorkflowStepPickerState } from '../types/workflowStepPicker'

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
   * (0242 NR0003 권고 2). Defaults to `docRef` for callers that already pass a root.
   */
  sequenceDocRef?: string
  actionScope: 'new' | 'edit' | 'workflow_decide' | 'chat' | 'rework' | 'review' | 'vr_correction' | 'next_step_message' | 'design_handoff'
  initialMode?: 'single' | 'continuous'
  initialTargetSeq?: number | null
  continuationReviewMode?: boolean
  continuationInstructionMode?: 'auto_approved' | 'ai_direct'
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
// (the action-bar 연속 작업 path) already picked its target in ContinuousWorkDialog and closes
// immediately — mounting the picker there would refetch the sequence and clobber that choice.
const pickerActive = computed(() =>
  canContinuous.value &&
  mode.value === 'continuous' &&
  props.actionScope !== 'workflow_decide' &&
  !props.autoStart,
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
    if (aiProviderStore.selectedProviderId) body.provider_id = aiProviderStore.selectedProviderId
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
      body.continuation_instruction_mode = props.continuationInstructionMode ?? 'auto_approved'
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
      // Adopt the already-running run instead of erroring (scenario 8 restore).
      const groupId = data.group_id ?? aiInvokeGroupId(props.project, props.module, props.group)
      aiInvokeStore.trackStarted({
        run_id: data.run_id,
        group_id: groupId,
        doc_ref: props.docRef,
        mode: mode.value,
      })
      void aiInvokeStore.refresh(groupId)
      emit('update:visible', false)
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

function onBackdrop() {
  close()
}

function close() {
  emit('update:visible', false)
}

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
</style>
