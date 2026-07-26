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

        <!-- ── Body: left = step list, right = settings tabs (0317 T0010 rev4) ── -->
        <div class="modal-bd cwd-body">
          <div class="cwd-col cwd-col-steps">
            <p class="cwd-intro">{{ t('main.continuous_work.intro') }}</p>
            <!-- 0242: the step list / head / all-done / pre-decision handling all live in the
                 shared picker, which the AI-invoke dialog now presents too. -->
            <WorkflowStepPicker
              :doc-ref="docRef"
              :active="visible"
              :step-tag="stepProviderTag"
              @change="onPickerChange"
            />
          </div>

          <div v-if="!picker.loading && !picker.errorKey" class="cwd-col cwd-col-options">
            <template v-if="!picker.allDone">
              <div class="cwd-tabbar">
                <button
                  type="button"
                  class="cwd-tab"
                  :class="{ 'cwd-tab--active': activeTab === 'basic' }"
                  @click="activeTab = 'basic'"
                >{{ t('main.continuous_work.tab_basic') }}</button>
                <button
                  type="button"
                  class="cwd-tab"
                  :class="{ 'cwd-tab--active': activeTab === 'provider' }"
                  @click="activeTab = 'provider'"
                >{{ t('main.continuous_work.tab_provider') }}</button>
              </div>

              <!-- 기본 설정: AI review mode (R0001) + N/T instruction handling. -->
              <div v-if="activeTab === 'basic'" class="cwd-tab-panel">
                <label class="cwd-toggle">
                  <input v-model="reviewMode" type="checkbox" />
                  <span class="cwd-toggle-text">
                    <span class="cwd-toggle-title">
                      <AppIcon name="user-gear" /> {{ t('main.continuous_work.review_mode_label') }}
                    </span>
                    <span class="cwd-toggle-desc">{{ t('main.continuous_work.review_mode_desc') }}</span>
                  </span>
                </label>

                <div class="cwd-mode-group">
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
              </div>

              <!-- 프로바이더: default provider + per-step (item_seq) overrides (0317 T0010 rev4:
                   재정의 단위가 문서 타입에서 실행 단계로 바뀌었다 — 같은 T가 두 번 나와도 서로
                   다른 프로바이더를 지정할 수 있다). Session-scoped: it rides the run's start
                   request, not a persisted project setting. -->
              <div v-else class="cwd-tab-panel">
                <div v-if="!providers || providers.length === 0" class="cwd-empty-card">
                  <AppIcon name="warning" />
                  {{ t('main.continuous_work.empty_provider_text') }}
                </div>
                <div v-else class="cwd-provider-block">
                  <div class="cwd-provider-row">
                    <label>{{ t('main.continuous_work.provider_default_label') }}</label>
                    <AiProviderSelect
                      :providers="providers"
                      :model-value="selectedProvider"
                      :loading="providerLoading"
                      :errored="providerErrored"
                      @update:model-value="(v) => emit('update:provider', v)"
                    />
                  </div>
                  <!-- 0317 T0015: each execution step is shown directly (no "단계별로 다르게
                       지정" opt-in disclosure) and its select defaults to the header default
                       provider (never a blank option) — the user only touches the steps they
                       want to differ. -->
                  <div class="cwd-override-table">
                    <div v-for="(item, idx) in runnableSteps" :key="item.item_seq" class="cwd-override-row">
                      <span class="cwd-override-step-no">{{ t('main.continuous_work.step_no_label', { n: idx + 1 }) }}</span>
                      <span class="doc-tag cwd-override-badge" :class="`c-${item.type}`">{{ item.type }}</span>
                      <span class="cwd-override-label">{{ item.label }}</span>
                      <select
                        class="cwd-override-select"
                        :value="stepProviderValue(item.item_seq)"
                        @change="onStepProviderChange(item.item_seq, ($event.target as HTMLSelectElement).value)"
                      >
                        <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
                      </select>
                    </div>
                  </div>
                </div>
              </div>
            </template>

            <div class="cwd-summary">
              {{ summaryText }}
            </div>
          </div>
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
import type { WorkflowStepItem, WorkflowStepPickerState } from '../types/workflowStepPicker'

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
    // 0317 T0010 rev4: item_seq -> provider_id, for steps the user explicitly overrode.
    // Session-scoped (rides this run's start request only, never persisted).
    providerOverrides: Record<number, string>
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
  steps: [],
})

const activeTab = ref<'basic' | 'provider'>('basic')
// 0317 T0010 rev4: item_seq -> provider_id. Replaces the D0004 per-doc-type map (chainDocTypes/
// assignments) — the override unit moved from document TYPE to individual STEP INSTANCE, so the
// same type appearing twice in one chain (e.g. two T steps) can resolve to different providers.
const overrides = ref<Record<number, string>>({})

const runnableSteps = computed(() => (picker.value.steps ?? []).filter(s => s.status !== 'done'))

// 0317 T0015: each step's select is pre-selected to the header default provider rather than a
// blank "use default" option. `overrides` still stores ONLY genuine per-step overrides (a step
// that differs from the default), so the step tag's default/override distinction and the
// confirm payload stay unchanged — the default just becomes visible instead of an empty slot.
function stepProviderValue(itemSeq: number): string {
  return overrides.value[itemSeq] ?? props.selectedProvider ?? ''
}

function onStepProviderChange(itemSeq: number, value: string) {
  const next = { ...overrides.value }
  // Selecting the header default (or nothing) folds back to "follow default" — no override row.
  if (!value || value === props.selectedProvider) delete next[itemSeq]
  else next[itemSeq] = value
  overrides.value = next
}

function providerName(id: string | undefined | null): string | null {
  if (!id) return null
  return props.providers?.find(p => p.id === id)?.name ?? null
}

function stepProviderTag(item: WorkflowStepItem): { text: string; override: boolean } | null {
  const stepNo = runnableSteps.value.findIndex(s => s.item_seq === item.item_seq)
  if (stepNo < 0) return null
  const overrideId = overrides.value[item.item_seq]
  if (overrideId) {
    const name = providerName(overrideId)
    if (!name) return null
    return { text: t('main.continuous_work.provider_tag_override', { step: stepNo + 1, name }), override: true }
  }
  const name = providerName(props.selectedProvider)
  if (!name) return null
  return { text: t('main.continuous_work.provider_tag_default', { name }), override: false }
}

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
  const providerOverrides: Record<number, string> = {}
  for (const [seq, providerId] of Object.entries(overrides.value)) {
    if (providerId) providerOverrides[Number(seq)] = providerId
  }
  emit('confirm', {
    targetSeq: sel.targetSeq,
    targetType: sel.targetType,
    targetLabel: sel.targetLabel,
    reviewMode: reviewMode.value,
    instructionMode: instructionMode.value,
    stepCount: sel.stepCount,
    fromDecision: sel.fromDecision,
    providerOverrides,
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
      activeTab.value = 'basic'
      overrides.value = {}
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.modal-cwd {
  width: 860px;
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
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  align-items: start;
}
.cwd-col {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}
.cwd-col-options {
  border-left: 1px solid var(--border);
  background: var(--surface-h);
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
.cwd-tabbar {
  display: flex;
  gap: 2px;
  border-bottom: 1px solid var(--border);
}
.cwd-tab {
  padding: 7px 14px;
  font-size: .82rem;
  font-weight: 600;
  color: var(--text-m);
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
}
.cwd-tab--active {
  color: var(--primary);
  border-bottom-color: var(--primary);
}
.cwd-tab-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.cwd-toggle {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  cursor: pointer;
  background: var(--surface);
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
.cwd-provider-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
}
.cwd-provider-row { display: flex; align-items: center; gap: 8px; }
.cwd-provider-row label { font-size: .78rem; color: var(--text-m); min-width: 56px; flex-shrink: 0; }
.cwd-provider-row > :not(label) { flex: 1; min-width: 0; }
.cwd-override-table {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
}
.cwd-override-row {
  display: flex;
  align-items: center;
  gap: 7px;
}
.cwd-override-step-no {
  font-size: .66rem;
  font-weight: 700;
  color: var(--text-m);
  min-width: 64px;
  flex-shrink: 0;
}
.cwd-override-badge { flex-shrink: 0; }
.cwd-override-label {
  font-size: .74rem;
  color: var(--text);
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cwd-override-select {
  flex: 1.2;
  min-width: 0;
  padding: 5px 7px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text);
  font-size: .78rem;
}
.cwd-empty-card {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 14px;
  border: 1px dashed #d97706;
  background: #fffbeb;
  border-radius: var(--r);
  font-size: .8rem;
  color: #92600a;
}
.cwd-summary {
  font-size: .82rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
</style>
