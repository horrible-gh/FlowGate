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
              :auto-handled-types="autoHandledTypes"
              :auto-handled-item-seqs="autoApproveItemSeqs"
              :auto-approve-candidate-types="autoApproveCandidateTypes"
              :auto-approve-selected="autoApproveItemSeqs"
              @change="onPickerChange"
              @toggle-auto-approve="onToggleAutoApprove"
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
                <button
                  type="button"
                  class="cwd-tab"
                  :class="{ 'cwd-tab--active': activeTab === 'message' }"
                  @click="activeTab = 'message'"
                >{{ t('main.continuous_work.tab_message') }}</button>
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
              <div v-else-if="activeTab === 'provider'" class="cwd-tab-panel">
                <div v-if="!providers || providers.length === 0" class="cwd-empty-card">
                  <AppIcon name="warning" />
                  {{ t('main.continuous_work.empty_provider_text') }}
                </div>
                <div v-else class="cwd-provider-block">
                  <div class="cwd-provider-row">
                    <label class="cwd-provider-label">{{ t('main.continuous_work.provider_default_label') }}</label>
                    <AiProviderSelect
                      class="cwd-provider-select"
                      :providers="providers"
                      :model-value="selectedProvider"
                      :loading="providerLoading"
                      :errored="providerErrored"
                      hide-label
                      @update:model-value="(v) => emit('update:provider', v)"
                    />
                  </div>
                  <!-- 0317 T0015: each execution step is shown directly (no "단계별로 다르게
                       지정" opt-in disclosure) and its select defaults to the header default
                       provider (never a blank option) — the user only touches the steps they
                       want to differ.
                       0337 R0001: the rows are the steps an AI worker will ACTUALLY run in this
                       run — head through the chosen target, minus the instructions the server
                       auto-approves. A step past the target, or one with no worker, gets no
                       select because its value could never be used. -->
                  <div v-if="excludedNote" class="cwd-scope-note">
                    <AppIcon name="info" /> {{ excludedNote }}
                  </div>
                  <div class="cwd-override-table">
                    <div v-for="(item, idx) in executionSteps" :key="item.item_seq" class="cwd-override-row">
                      <span class="cwd-override-step-no">{{ t('main.continuous_work.step_no_label', { n: idx + 1 }) }}</span>
                      <span class="doc-tag cwd-override-badge" :class="`c-${item.type}`">{{ item.type }}</span>
                      <span class="cwd-override-label">{{ item.label }}</span>
                      <AiProviderSelect
                        class="cwd-override-select"
                        :providers="providers"
                        :model-value="stepProviderValue(item.item_seq)"
                        hide-label
                        @update:model-value="(v) => onStepProviderChange(item.item_seq, v)"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <!-- 전달멘트 (flowgate.default.0346 T0005 / D0004): a common note for the whole
                   chain and/or an individual note per step, both prepended to the hop's prompt
                   as a "사용자 메시지" section server-side. Unlike the provider tab, this one has
                   no "등록된 것이 없다" empty state — it depends on nothing external. -->
              <div v-else class="cwd-tab-panel">
                <div class="cwd-provider-block">
                  <div class="cwd-provider-row">
                    <label class="cwd-provider-label">{{ t('main.continuous_work.message_default_label') }}</label>
                    <input
                      v-model="defaultMessage"
                      type="text"
                      class="cwd-message-input cwd-message-default-input"
                      :placeholder="t('main.continuous_work.message_default_placeholder')"
                    />
                  </div>
                  <div v-if="excludedNote" class="cwd-scope-note">
                    <AppIcon name="info" /> {{ excludedNote }}
                  </div>
                  <div class="cwd-override-table">
                    <div v-for="(item, idx) in executionSteps" :key="item.item_seq" class="cwd-override-row">
                      <span class="cwd-override-step-no">{{ t('main.continuous_work.step_no_label', { n: idx + 1 }) }}</span>
                      <span class="doc-tag cwd-override-badge" :class="`c-${item.type}`">{{ item.type }}</span>
                      <span class="cwd-override-label">{{ item.label }}</span>
                      <input
                        :value="messageOverrides[item.item_seq] ?? ''"
                        type="text"
                        class="cwd-message-input cwd-override-message-input"
                        :placeholder="t('main.continuous_work.message_step_placeholder')"
                        @input="onStepMessageChange(item.item_seq, ($event.target as HTMLInputElement).value)"
                      />
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
    // 0346 T0005: [전달멘트] tab — a common note for every hop, and item_seq -> note for the
    // steps the user singled out. Session-scoped, same as providerOverrides above.
    defaultMessage: string
    messageOverrides: Record<number, string>
    // 0352 T0004 §2/§3.7: ai_direct-only — item_seqs of individual N/T steps the user picked
    // for the SERVER to still auto-generate + auto-approve (like auto_approved does for that
    // one step). Always [] outside ai_direct.
    autoApproveItemSeqs: number[]
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

const activeTab = ref<'basic' | 'provider' | 'message'>('basic')
// 0317 T0010 rev4: item_seq -> provider_id. Replaces the D0004 per-doc-type map (chainDocTypes/
// assignments) — the override unit moved from document TYPE to individual STEP INSTANCE, so the
// same type appearing twice in one chain (e.g. two T steps) can resolve to different providers.
const overrides = ref<Record<number, string>>({})
// 0346 T0005: [전달멘트] tab state — a common note plus item_seq -> note overrides, mirroring
// `overrides` above but additive rather than replacing (D0004 §3-3: 개별 멘트가 공통 멘트를
// 밀어내지 않는다).
const defaultMessage = ref('')
const messageOverrides = ref<Record<number, string>>({})
// 0352 T0004 §2/§3.7: ai_direct-only per-item_seq server-auto-approve selection. A checked
// N/T step is server-generated + auto-approved exactly like auto_approved handles it, without
// switching the whole chain out of ai_direct.
const autoApproveItemSeqs = ref<number[]>([])

// 0337 R0001 -----------------------------------------------------------------------------
// A document step and an AI-execution step are not the same thing, and only the latter can
// carry a provider.
//   1) auto_approved: N/T are written and approved by the SERVER, with no AI worker and no
//      provider lookup of their own (ai_invoke_service maps an N/T hop onto its paired report).
//      They are therefore neither a stop point nor a provider choice.
//   2) Whatever the mode, a step AFTER the chosen target never runs in this session, so its
//      provider cannot be consumed either.
// Both were being offered as choices; both are removed from the dialog here, and the confirm
// payload is built from exactly the rows that remain.
const AUTO_APPROVED_INSTRUCTION_TYPES = ['N', 'T']
const autoHandledTypes = computed(
  () => (instructionMode.value === 'auto_approved' ? AUTO_APPROVED_INSTRUCTION_TYPES : []),
)
// 0352 T0004 §3.7: the checkbox itself only ever appears for N/T under ai_direct — under
// auto_approved every N/T is already forced out via autoHandledTypes, so there is nothing
// left to individually pick.
const autoApproveCandidateTypes = computed(
  () => (instructionMode.value === 'ai_direct' ? AUTO_APPROVED_INSTRUCTION_TYPES : []),
)

/** Not-yet-done steps, i.e. everything the picker still offers. */
const runnableSteps = computed(() => (picker.value.steps ?? []).filter(s => s.status !== 'done'))

// 0352 T0004 §2: the per-item_seq selection is only meaningful under ai_direct; switching
// modes clears it (mirrors the picker's own autoHandledTypes/autoHandledItemSeqs split).
const autoApproveItemSeqSet = computed(
  () => (instructionMode.value === 'ai_direct' ? new Set(autoApproveItemSeqs.value) : new Set<number>()),
)

/** The steps an AI worker actually performs in THIS run — the provider table's rows. */
const executionSteps = computed<WorkflowStepItem[]>(() => {
  const sel = picker.value.selection
  if (!sel || sel.fromDecision) return []
  const autoHandled = new Set(autoHandledTypes.value)
  return runnableSteps.value.filter(
    s => s.item_seq <= sel.targetSeq
      && !autoHandled.has(String(s.type ?? '').toUpperCase())
      && !autoApproveItemSeqSet.value.has(s.item_seq),
  )
})

/** Why the provider list is shorter than the step list — stated, not left to be guessed. */
const excludedNote = computed(() => {
  const sel = picker.value.selection
  if (!sel || sel.fromDecision) return ''
  const autoHandled = new Set(autoHandledTypes.value)
  const inRange = runnableSteps.value.filter(s => s.item_seq <= sel.targetSeq)
  const autoCount = inRange.filter(
    s => autoHandled.has(String(s.type ?? '').toUpperCase()) || autoApproveItemSeqSet.value.has(s.item_seq),
  ).length
  const beyondCount = runnableSteps.value.length - inRange.length
  if (autoCount > 0 && beyondCount > 0) {
    return t('main.continuous_work.provider_scope_note_both', { auto: autoCount, beyond: beyondCount })
  }
  if (autoCount > 0) return t('main.continuous_work.provider_scope_note_auto', { auto: autoCount })
  if (beyondCount > 0) return t('main.continuous_work.provider_scope_note_beyond', { beyond: beyondCount })
  return ''
})

function onToggleAutoApprove(itemSeq: number, checked: boolean) {
  const next = new Set(autoApproveItemSeqs.value)
  if (checked) next.add(itemSeq)
  else next.delete(itemSeq)
  autoApproveItemSeqs.value = [...next].sort((a, b) => a - b)
}

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

// 0346 T0005 §2-1 항목 4: blank (or whitespace-only) input is the same as "no individual note
// for this step" — mirrors onStepProviderChange's "같으면 삭제" rule with a "비면 삭제" rule.
function onStepMessageChange(itemSeq: number, value: string) {
  const next = { ...messageOverrides.value }
  if (!value.trim()) delete next[itemSeq]
  else next[itemSeq] = value
  messageOverrides.value = next
}

function providerName(id: string | undefined | null): string | null {
  if (!id) return null
  return props.providers?.find(p => p.id === id)?.name ?? null
}

function stepProviderTag(item: WorkflowStepItem): { text: string; override: boolean } | null {
  // 0337 R0001: only a step that this run will actually hand to a provider gets a provider
  // tag. Steps past the target and server-auto-approved instructions get none — the picker
  // labels the latter "자동 승인" instead.
  const stepNo = executionSteps.value.findIndex(s => s.item_seq === item.item_seq)
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

// 0337 R0001: shrinking the target or switching to auto-approve invalidates the overrides that
// were picked for the steps that just left the run. Drop them as soon as the row disappears, so
// what the user sees in the table IS what the confirm payload carries — no stale key survives.
watch(executionSteps, (steps) => {
  const inRun = new Set(steps.map(s => s.item_seq))
  const kept = Object.entries(overrides.value).filter(([seq]) => inRun.has(Number(seq)))
  if (kept.length !== Object.keys(overrides.value).length) {
    overrides.value = Object.fromEntries(kept)
  }
  // 0346 T0005: the [전달멘트] per-step notes follow the exact same rule — a step that leaves
  // the run must not leave a stale note behind it.
  const keptMessages = Object.entries(messageOverrides.value).filter(([seq]) => inRun.has(Number(seq)))
  if (keptMessages.length !== Object.keys(messageOverrides.value).length) {
    messageOverrides.value = Object.fromEntries(keptMessages)
  }
})

// 0352 T0004 §3.7: the N/T item_seqs still eligible for the auto-approve checkbox — ai_direct
// only, within the chosen target. Shrinking the target, switching back to auto_approved, or a
// step no longer being N/T (sequence reload) must all drop the stale selection the same way
// the provider/message overrides above do.
const inRangeAutoApproveCandidates = computed<Set<number>>(() => {
  const sel = picker.value.selection
  if (!sel || sel.fromDecision || instructionMode.value !== 'ai_direct') return new Set()
  const candidateTypes = new Set(AUTO_APPROVED_INSTRUCTION_TYPES)
  return new Set(
    runnableSteps.value
      .filter(s => s.item_seq <= sel.targetSeq && candidateTypes.has(String(s.type ?? '').toUpperCase()))
      .map(s => s.item_seq),
  )
})

watch(inRangeAutoApproveCandidates, (validSet) => {
  const kept = autoApproveItemSeqs.value.filter(seq => validSet.has(seq))
  if (kept.length !== autoApproveItemSeqs.value.length) {
    autoApproveItemSeqs.value = kept
  }
})

function onProceed() {
  const sel = picker.value.selection
  if (!sel) return
  const inRun = new Set(executionSteps.value.map(s => s.item_seq))
  const providerOverrides: Record<number, string> = {}
  for (const [seq, providerId] of Object.entries(overrides.value)) {
    if (providerId && inRun.has(Number(seq))) providerOverrides[Number(seq)] = providerId
  }
  const messageOverridesOut: Record<number, string> = {}
  for (const [seq, note] of Object.entries(messageOverrides.value)) {
    if (note && note.trim() && inRun.has(Number(seq))) messageOverridesOut[Number(seq)] = note
  }
  const validAutoApprove = inRangeAutoApproveCandidates.value
  const autoApproveOut = autoApproveItemSeqs.value.filter(seq => validAutoApprove.has(seq))
  emit('confirm', {
    targetSeq: sel.targetSeq,
    targetType: sel.targetType,
    targetLabel: sel.targetLabel,
    reviewMode: reviewMode.value,
    instructionMode: instructionMode.value,
    stepCount: sel.stepCount,
    fromDecision: sel.fromDecision,
    providerOverrides,
    defaultMessage: defaultMessage.value.trim(),
    messageOverrides: messageOverridesOut,
    autoApproveItemSeqs: autoApproveOut,
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
      defaultMessage.value = ''
      messageOverrides.value = {}
      autoApproveItemSeqs.value = []
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
/* 0328 TR0005 rev4: the body itself must NOT scroll. While it did, a short window pushed the
   whole right-hand column (tab bar + default-provider row included) into the body scroller, so
   capping the per-step list alone could not keep those two fixed. The body now clips and each
   column owns its own scrolling. */
.modal-cwd .modal-bd {
  flex: 1;
  overflow: hidden;
  min-height: 0;
  /* 0328 TR0005 rev6: one bound for BOTH columns. Each column used to cap its own list
     (280px each), so the taller column decided the body height and the shorter one was left
     with dead space under its last box — that is the misaligned bottom line the rejection
     pointed at, and it got worse the more steps there were. Capping the body instead makes the
     single grid row the only height authority: both columns are stretched to it and each one's
     list flexes to fill, so the two bottom borders land on the same line at any step count. */
  max-height: min(480px, 58vh);
}
.cwd-body {
  display: grid;
  /* rev6: even 5:5 split — the left "어디까지 연속 작업할지 선택" column was 1.15fr and read
     as the wider half. */
  grid-template-columns: 1fr 1fr;
  /* A definite row height (not content-sized) is what makes the columns' min-height:0 bite —
     without it the grid row grows to fit the tallest column and gets clipped by the body. */
  grid-template-rows: minmax(0, 1fr);
  align-items: stretch;
  min-height: 0;
}
.cwd-col {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
  min-height: 0;
}
/* rev6: neither column scrolls as a whole any more — a column-level scrollbar would move its
   last box off the shared bottom line. Both columns clip and the two step lists inside them are
   the only scroll containers. */
.cwd-col-steps { overflow: hidden; }
/* rev6: make the picker fill the left column instead of sitting at its natural height, so its
   step list's bottom border ends where the right column's summary card ends. Scoped to this
   dialog's instance (:deep from .cwd-col-steps) — the shared picker used by the AI-invoke dialog
   keeps its own self-capped geometry. */
.cwd-col-steps :deep(.wsp) {
  flex: 1 1 auto;
  min-height: 0;
}
.cwd-col-steps :deep(.wsp-steps) {
  /* The body cap above is what bounds it now; a second 280px cap here would re-open the gap. */
  max-height: none;
  flex: 1 1 auto;
  /* min-height:0 (not a 72px floor): on a very short window a floor makes the column overflow
     its clipped grid row, and the overflowing box's bottom border no longer matches the other
     column's. Rows keep flex-shrink:0, so the list scrolls instead of squashing them. */
  min-height: 0;
}
.cwd-col-options {
  border-left: 1px solid var(--border);
  background: var(--surface-h);
  overflow: hidden;
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
  /* Outside every scroll container, and never squeezed: the tab bar stays put. */
  flex-shrink: 0;
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
  /* Takes the height left over by the tab bar and summary, and passes the constraint down to
     the per-step list (min-height:0 so the chain can actually shrink). */
  flex: 1 1 auto;
  min-height: 0;
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
  flex: 1 1 auto;
  min-height: 0;
}
.cwd-provider-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  /* Sibling of the scrolling list, never inside it: the default row stays put. */
  flex-shrink: 0;
}
.cwd-provider-label { font-size: .78rem; color: var(--text-m); min-width: 56px; flex-shrink: 0; }
.cwd-provider-select { flex: 1; min-width: 0; }
/* 0337 R0001: sits between the default-provider row and the step list, outside the scroller —
   the reason the list is shorter than the sequence must stay visible while the list scrolls. */
.cwd-scope-note {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  font-size: .72rem;
  line-height: 1.45;
  color: var(--text-m);
  flex-shrink: 0;
}
.cwd-override-table {
  display: flex;
  flex-direction: column;
  /* rev5: the rejection asked for tighter vertical rhythm between step rows. */
  gap: 4px;
  padding-top: 10px;
  border-top: 1px dashed var(--border);
  /* 0328 TR0005 rev3: the step list grows with the sequence length and used to push the
     summary/footer down without bound. Mirror the left-hand step picker (.wsp-steps in
     WorkflowStepPicker.vue), which caps its own height and scrolls: only this per-step list
     scrolls — the tab bar and the default-provider row sit outside it and stay put.
     rev6: the 280px cap moved up to the dialog body (.modal-cwd .modal-bd), which bounds both
     columns at once; keeping it here as well would stop this list short of the column bottom and
     leave the summary card floating away from the left column's bottom line. */
  overflow-y: auto;
  /* rev4: also shrink below the cap when the window is short, so the list absorbs the squeeze
     instead of overflowing the column and dragging the tab bar / default row along with it.
     This is the dialog's ONLY per-step scroll container. */
  flex: 1 1 auto;
  /* rev6: the 72px floor is gone for the same reason as the left list — it would push the
     summary card out of the clipped column on a short window and break the shared bottom line. */
  min-height: 0;
  /* Keeps the scrollbar off the row cards' right border when the list overflows. */
  padding-right: 4px;
}
.cwd-override-row {
  display: flex;
  align-items: center;
  gap: 7px;
  /* rev5: slim vertical padding — the row card was called out as too tall. */
  padding: 3px 8px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  /* Column flex items shrink by default; inside the capped scroll container that would
     squash the rows instead of scrolling them. */
  flex-shrink: 0;
}
.cwd-override-step-no {
  font-size: .66rem;
  font-weight: 700;
  color: var(--text-m);
  min-width: 64px;
  flex-shrink: 0;
}
/* 0328 TR0005 rev1: a fixed min-width keeps the badge column aligned across rows —
   otherwise "T"/"TR"/"TSR" render at different widths and the label/select columns
   that follow start at a different x per row, which is the "들쑥날쑥" the rejection
   pointed at. */
.cwd-override-badge { flex-shrink: 0; min-width: 34px; text-align: center; }
.cwd-override-label {
  font-size: .74rem;
  color: var(--text);
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* A fixed basis (not flex-grow) keeps every step's select the same width regardless
   of how much space the badge/label ate on that row. */
.cwd-override-select {
  flex: 0 0 160px;
  min-width: 0;
}
/* rev5: the row's height is set by the embedded select — compact it inside step rows only
   (the default-provider row above keeps the regular size). */
.cwd-override-select :deep(.aip-select-input) {
  padding-top: 2px;
  padding-bottom: 2px;
  font-size: .78rem;
}
/* 0346 T0005: [전달멘트] tab text inputs — the header row uses the regular provider-select
   sizing, the per-step row uses the compact override sizing (mirrors .cwd-override-select).
   rev1: `font: inherit` pulled in the page's base font-size (larger than the provider select's
   own .82rem), and the 6px/10px padding didn't match the select's 5px/8px either — next to the
   프로바이더 tab's default-provider select the 전달멘트 tab's common-note input read as visibly
   bigger. Match `.aip-select-input`'s font-size/vertical padding exactly (AiProviderSelect.vue)
   so the two tabs' default-row controls read as the same size. */
.cwd-message-input {
  font-size: .82rem;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  padding: 5px 8px;
}
.cwd-message-default-input {
  flex: 1;
  min-width: 0;
}
.cwd-override-message-input {
  flex: 1 1 160px;
  min-width: 0;
  padding: 2px 8px;
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
  flex-shrink: 0;
}
</style>
