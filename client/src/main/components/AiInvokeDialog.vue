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
            <div v-if="canContinuous && mode === 'continuous'" class="aiv-seq-row">
              <template v-if="actionScope === 'workflow_decide'">
                <AppIcon name="fast-forward" />
                <span class="aiv-seq-hint">{{ t('main.ai_invoke_dialog.target_to_end_hint') }}</span>
              </template>
              <template v-else>
                <label class="aiv-seq-label" for="aiv-target-seq">{{ t('main.ai_invoke_dialog.target_seq_label') }}</label>
                <input
                  id="aiv-target-seq"
                  v-model.number="targetSeq"
                  type="number"
                  class="form-ctrl aiv-seq-input"
                  min="1"
                />
                <span class="aiv-seq-hint">{{ t('main.ai_invoke_dialog.target_seq_hint') }}</span>
              </template>
            </div>
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
import { useAiProviderStore } from '../stores/aiProvider'
import { aiInvokeGroupId, useAiInvokeRunsStore } from '../stores/aiInvokeRuns'

const props = defineProps<{
  visible: boolean
  project: string
  module?: string | null
  group: string
  docRef: string
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
const targetSeq = ref<number | null>(null)
const starting = ref(false)
const startError = ref('')

// Continuous chains only make sense for the scopes the server can chain
// (group 0223): the parallel-invoke extras are one-shot actions.
const canContinuous = computed(() =>
  props.actionScope === 'new' || props.actionScope === 'edit' || props.actionScope === 'workflow_decide',
)
const canStart = computed(() =>
  mode.value === 'single' ||
  props.actionScope === 'workflow_decide' ||
  (targetSeq.value != null && targetSeq.value > 0),
)

function resetState() {
  mode.value = canContinuous.value ? (props.initialMode ?? 'single') : 'single'
  targetSeq.value = props.initialTargetSeq ?? (props.actionScope === 'workflow_decide' ? -1 : null)
  starting.value = false
  startError.value = ''
}

async function start() {
  if (starting.value || !canStart.value) return
  starting.value = true
  startError.value = ''
  try {
    await aiProviderStore.ensureLoaded(props.project)
    const body: Record<string, unknown> = {
      project: props.project,
      group: props.group,
      doc_ref: props.docRef,
      action_scope: props.actionScope,
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
      body.continuation_target_seq = props.actionScope === 'workflow_decide' ? -1 : targetSeq.value
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
.aiv-seq-label { font-size: .8rem; color: var(--text); }
.aiv-seq-input { width: 90px; }
.aiv-seq-hint { font-size: .72rem; color: var(--text-m); }
.aiv-error {
  font-size: .8rem;
  color: var(--danger);
  background: var(--danger-light, #fee2e2);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
</style>
