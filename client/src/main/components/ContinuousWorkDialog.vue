<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box modal-cwd">
        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="fast-forward" style="color:var(--primary); margin-right:6px;" />
            {{ t('main.continuous_work.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd cwd-body">
          <p class="cwd-intro">{{ t('main.continuous_work.intro') }}</p>

          <!-- 0242: the step list / head / all-done / pre-decision handling all live in the
               shared picker, which the AI-invoke dialog now presents too. -->
          <WorkflowStepPicker :doc-ref="docRef" :active="visible" @change="onPickerChange" />

          <template v-if="!picker.loading && !picker.errorKey">
            <!-- AI review mode (R0001): pause after the first step for a human review + Q&A
                 instead of auto-chaining all the way. Hidden when allDone — there is no step
                 left to run, so the toggle would be meaningless. -->
            <label v-if="!picker.allDone" class="cwd-toggle">
              <input v-model="reviewMode" type="checkbox" />
              <span class="cwd-toggle-text">
                <span class="cwd-toggle-title">
                  <AppIcon name="user-gear" /> {{ t('main.continuous_work.review_mode_label') }}
                </span>
                <span class="cwd-toggle-desc">{{ t('main.continuous_work.review_mode_desc') }}</span>
              </span>
            </label>

            <!-- 0234 B0001 RC3: the continuous run auto-starts (the AI-invoke dialog is not
                 shown), so this is the only chance to confirm/change which provider it uses. -->
            <div v-if="!picker.allDone" class="cwd-provider">
              <AiProviderSelect
                :providers="providers"
                :model-value="selectedProvider"
                :loading="providerLoading"
                :errored="providerErrored"
                @update:model-value="(v) => emit('update:provider', v)"
              />
            </div>

            <div v-if="!picker.allDone" class="cwd-mode-group">
              <div class="cwd-section-title">{{ t('main.continuous_work.instruction_mode_title') }}</div>
              <label class="cwd-mode" :class="{ active: instructionMode === 'auto_approved' }">
                <input v-model="instructionMode" type="radio" value="auto_approved" />
                <span class="cwd-toggle-text">
                  <span class="cwd-toggle-title">
                    <AppIcon name="seal-check" /> {{ t('main.continuous_work.instruction_mode_auto') }}
                  </span>
                  <span class="cwd-toggle-desc">{{ t('main.continuous_work.instruction_mode_auto_desc') }}</span>
                </span>
              </label>
              <label class="cwd-mode" :class="{ active: instructionMode === 'ai_direct' }">
                <input v-model="instructionMode" type="radio" value="ai_direct" />
                <span class="cwd-toggle-text">
                  <span class="cwd-toggle-title">
                    <AppIcon name="robot" /> {{ t('main.continuous_work.instruction_mode_ai') }}
                  </span>
                  <span class="cwd-toggle-desc">{{ t('main.continuous_work.instruction_mode_ai_desc') }}</span>
                </span>
              </label>
            </div>

            <div class="cwd-summary">
              {{ summaryText }}
            </div>
          </template>
        </div>

        <!-- ── Footer ── -->
        <div class="modal-ft">
          <button type="button" class="btn btn-ghost" @click="close">{{ t('common.cancel') }}</button>
          <button
            type="button"
            class="btn btn-primary"
            :disabled="!canProceed"
            @click="onProceed"
          >
            <AppIcon name="arrow-right" /> {{ t('main.continuous_work.btn_next') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkflowStepPicker from './WorkflowStepPicker.vue'
import type { WorkflowStepPickerState } from '../types/workflowStepPicker'

const props = defineProps<{
  visible: boolean
  /** Sequence-owning root (R/B) doc id — the picker reads /workflow/sequence by this id. */
  docRef: string
  // 0234 B0001: runtime provider list + current selection, owned by MainPanel (which holds
  // the aiProvider store). Surfaced so the continuous run's provider is confirmable here.
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  // 0234 B0001 RC3: propagate a provider change back to the store-owning parent.
  'update:provider': [value: string]
  // Proceed to the warning/consent gate with the chosen run parameters.
  // fromDecision = true ⇒ the workflow is not decided yet; the run starts FROM the
  // workflow-decision step and targetSeq is the run-to-end sentinel (-1, R0001 "워크플로 결정부터").
  'confirm': [payload: {
    targetSeq: number
    targetType: string
    targetLabel: string
    reviewMode: boolean
    instructionMode: 'auto_approved' | 'ai_direct'
    stepCount: number
    fromDecision: boolean
  }]
}>()

const { t } = useI18n()

const reviewMode = ref(false)
const instructionMode = ref<'auto_approved' | 'ai_direct'>('auto_approved')
const picker = ref<WorkflowStepPickerState>({
  loading: true,
  errorKey: null,
  allDone: false,
  fromDecision: false,
  selection: null,
})

function onPickerChange(state: WorkflowStepPickerState) {
  picker.value = state
}

const canProceed = computed(() => picker.value.selection != null)

const summaryText = computed(() => {
  if (picker.value.allDone) return t('main.continuous_work.all_done_summary')
  if (picker.value.fromDecision) return t('main.continuous_work.from_decision_summary')
  const sel = picker.value.selection
  return t('main.continuous_work.summary', {
    count: sel?.stepCount ?? 0,
    target: sel?.targetLabel ?? '',
  })
})

function onProceed() {
  const sel = picker.value.selection
  if (!sel) return
  emit('confirm', {
    targetSeq: sel.targetSeq,
    targetType: sel.targetType,
    targetLabel: sel.targetLabel,
    reviewMode: reviewMode.value,
    instructionMode: instructionMode.value,
    stepCount: sel.stepCount,
    fromDecision: sel.fromDecision,
  })
}

function close() {
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      // The picker reloads itself off `active`; reset only what this dialog owns.
      reviewMode.value = false
      instructionMode.value = 'auto_approved'
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.modal-cwd {
  width: 480px;
  max-width: 96vw;
  display: flex;
  flex-direction: column;
  max-height: 88vh;
}
.modal-cwd .modal-bd {
  flex: 1;
  overflow-y: auto;
  min-height: 0;
}
.cwd-body {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.cwd-intro {
  margin: 0;
  font-size: .85rem;
  color: var(--text-s);
  line-height: 1.5;
}
.cwd-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
}
.cwd-toggle {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
}
.cwd-mode-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cwd-mode {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 10px 12px;
  cursor: pointer;
  background: var(--surface);
}
.cwd-mode.active {
  border-color: var(--primary);
  background: var(--primary-l);
}
.cwd-mode input {
  margin-top: 3px;
  flex-shrink: 0;
}
.cwd-toggle input { margin-top: 3px; flex-shrink: 0; }
.cwd-toggle-text { display: flex; flex-direction: column; gap: 2px; }
.cwd-toggle-title { font-size: .85rem; font-weight: 600; color: var(--text); }
.cwd-toggle-desc { font-size: .76rem; color: var(--text-m); line-height: 1.4; }
.cwd-provider {
  display: flex;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
}
.cwd-provider > * { flex: 1; min-width: 0; }
.cwd-summary {
  font-size: .82rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface-h);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
</style>
