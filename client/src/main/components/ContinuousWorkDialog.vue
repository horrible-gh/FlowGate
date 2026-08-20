<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
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

        <div v-if="presetActive" class="cwd-preset-banner">
          <div>
            <strong>{{ t('main.continuous_work.preset_source', { doc: preset?.sourceDocId }) }}</strong>
            <span>{{ t('main.continuous_work.preset_not_started') }}</span>
            <span v-if="presetUnsetCount">{{ t('main.continuous_work.preset_unset', { n: presetUnsetCount }) }}</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" @click="revertPreset">{{ t('main.continuous_work.preset_revert') }}</button>
        </div>
        <!-- 0399 T0018: the normal (post-save) entry — no unsaved preset, but the sequence
             itself may already carry plan-poured notes (TR0015/TR0017) and/or note-empty rows.
             One line names which plan the notes came from; the count of empty rows is informed
             but never blocks (D0010 §3.5/§3.6). -->
        <div v-else-if="showSequenceOriginBanner" class="cwd-preset-banner">
          <div>
            <strong>{{ t('main.continuous_work.sequence_source', { doc: sequenceSourceDocId }) }}</strong>
            <span>{{ t('main.continuous_work.preset_not_started') }}</span>
            <span v-if="noteUnsetCount" class="cwd-note-unset-flag">{{ t('main.continuous_work.note_unset', { n: noteUnsetCount }) }}</span>
          </div>
          <button type="button" class="btn btn-outline btn-sm" @click="revertSequenceNotes">{{ t('main.continuous_work.preset_revert') }}</button>
        </div>
        <div v-if="presetRefreshMessage" class="cwd-preset-refresh">{{ presetRefreshMessage }}</div>

        <!-- ── Body: left = step list, right = settings tabs (0317 T0010 rev4) ── -->
        <div class="modal-bd cwd-body">
          <div class="cwd-col cwd-col-steps">
            <p class="cwd-intro">{{ t('main.continuous_work.intro') }}</p>
            <!-- 0242: the step list / head / all-done / pre-decision handling all live in the
                 shared picker, which the AI-invoke dialog now presents too. -->
            <WorkflowStepPicker
              :doc-ref="docRef"
              :active="visible"
              :initial-target-seq="presetTargetSeq"
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

              <!-- Basic settings: AI review mode (R0001) + N/T instruction handling. -->
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

                <!-- Timeout setting (flowgate.default.0400 M0005): the per-hop wall-clock budget. A
                     small fixed list, not free input — fewer mistakes and a narrower control.
                     Rendered as a combo box (TR0007 2nd rejection: "리스트 박스로 해라...
                     콤보박스"), the same native-select pattern as AiProviderSelect's
                     .aip-select-input used in the provider tab, rather than a button/pill
                     group or a radio-row list. Session-scoped like the provider/message tabs,
                     but its last pick is also remembered in localStorage (D0005 Q&A) so
                     reopening the dialog defaults to what was chosen last time, without
                     becoming a project-level setting. -->
                <div class="cwd-mode-group">
                  <div class="cwd-section-title">{{ t('main.continuous_work.step_timeout_title') }}</div>
                  <label class="cwd-timeout-combo">
                    <select
                      v-model="stepTimeoutMinutes"
                      class="cwd-timeout-select"
                      :aria-label="t('main.continuous_work.step_timeout_title')"
                    >
                      <option v-for="opt in STEP_TIMEOUT_OPTIONS_MIN" :key="opt" :value="opt">
                        {{ t('main.continuous_work.step_timeout_option_minutes', { n: opt }) }}
                      </option>
                    </select>
                    <AppIcon name="caret-down" class="cwd-timeout-caret" />
                  </label>
                  <p class="cwd-timeout-desc">{{ t('main.continuous_work.step_timeout_desc') }}</p>
                </div>
              </div>

              <!-- Provider: default provider + per-step (item_seq) overrides (0317 T0010 rev4:
                   the override unit moved from document type to execution step — the same T can
                   appear twice and each occurrence can be assigned a different provider).
                   Session-scoped: it rides the run's start request, not a persisted project
                   setting. -->
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
                  <!-- 0442 B0001 재반려 2 ("예전처럼 되돌리라고"): 이 자리에 실행 요약
                       문장도, 고정 배지도, 해제 단추도 두지 않는다. 위의 기본 공급자
                       셀렉터가 이번 실행에 쓸 공급자를 이미 이름으로 보여 주고 아래 단계
                       행마다 태그가 붙으므로, 이 줄은 같은 정보를 한 문장처럼 되풀이할
                       뿐이었다. 고정 배지가 붙기 전의 배치로 되돌렸다. -->
                  <!-- 0317 T0015: each execution step is shown directly (no "단계별로 다르게
                       지정" opt-in disclosure) and its select defaults to the header default
                       provider (never a blank option) — the user only touches the steps they
                       want to differ. -->
                  <div v-if="excludedNote" class="cwd-scope-note">
                    <AppIcon name="info" /> {{ excludedNote }}
                  </div>
                  <!-- 0408 TR0021 2nd re-rejection ("왜 프로바이더는 안고치냐? 자동승인 상태면 N/T
                       빼야지... 이번 실행 미사용 이것떄문에 스크롤 생기니까 다 빼라 필요없는건
                       대체 왜넣은거야?"): reverts TR0018 rev1's "show every in-range row, read
                       only, with a badge" design — that row nobody can act on and no worker will
                       ever read was exactly the clutter the rejection means. This table now draws
                       exactly `executionSteps`, the same rows [Message] draws below. A step this
                       run auto-approves keeps its stored provider visible on the picker's own tag
                       instead (`stepProviderTag`, gated on `inRangeSteps`, not this table). -->
                  <div class="cwd-override-table">
                    <div v-for="row in providerRows" :key="row.item.item_seq" class="cwd-override-row">
                      <span class="cwd-override-step-no">{{ t('main.continuous_work.step_no_label', { n: row.stepNo }) }}</span>
                      <span class="doc-tag cwd-override-badge" :class="`c-${row.item.type}`">{{ row.item.type }}</span>
                      <span class="cwd-override-label">{{ row.item.label }}</span>
                      <AiProviderSelect
                        class="cwd-override-select"
                        :providers="providers"
                        :model-value="stepProviderValue(row.item)"
                        hide-label
                        @update:model-value="(v) => onStepProviderChange(row.item, v)"
                      />
                      <span
                        v-if="providerBadgeKey(row.item)"
                        class="cwd-filled-badge"
                        :class="{
                          'cwd-stored-provider--unavailable': providerBadgeKey(row.item) === 'main.continuous_work.sequence_provider_unavailable',
                          'cwd-stored-provider--pin-overridden': providerBadgeKey(row.item) === 'main.continuous_work.sequence_provider_pin_overridden',
                        }"
                      >
                        {{ t(providerBadgeKey(row.item)) }}
                      </span>
                    </div>
                  </div>
                </div>
              </div>

              <!-- Message (flowgate.default.0346 T0005 / D0004): a common note for the whole
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
                  <!-- 0408 M0019 1st re-rejection ("[자동승인] 인데 왜 N/T가 표시되게 했지?") + TR0021
                       2nd re-rejection ("프로바이더는 안고치냐... 다 빼라"): the mention AND provider
                       rows are now both exactly the steps an AI WORKER runs in this mode. Under
                       [Auto-approve] the server writes and approves N/T with no worker at all, so
                       neither a mention nor a provider CHOICE typed there could ever be
                       delivered — a row that carries no choice is not offered one. A step's
                       stored provider stays visible on the picker's own tag either way
                       (`stepProviderTag`), just not as a dead row in this table. -->
                  <div class="cwd-override-table">
                    <div v-for="row in messageRows" :key="row.item.item_seq" class="cwd-override-row">
                      <span class="cwd-override-step-no">{{ t('main.continuous_work.step_no_label', { n: row.stepNo }) }}</span>
                      <span class="doc-tag cwd-override-badge" :class="`c-${row.item.type}`">{{ row.item.type }}</span>
                      <span class="cwd-override-label">{{ row.item.label }}</span>
                      <input
                        :value="stepMessageValue(row.item)"
                        type="text"
                        class="cwd-message-input cwd-override-message-input"
                        :placeholder="t('main.continuous_work.message_step_placeholder')"
                        @input="onStepMessageChange(row.item, ($event.target as HTMLInputElement).value)"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </template>

            <div class="cwd-summary">
              {{ summaryText }}
              <span v-if="sequenceSourceDocId && noteUnsetCount" class="cwd-note-unset-flag">{{ t('main.continuous_work.note_unset', { n: noteUnsetCount }) }}</span>
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
import { postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'
import WorkflowStepPicker from './WorkflowStepPicker.vue'
import type { WorkflowStepItem, WorkflowStepPickerState } from '../types/workflowStepPicker'
import { DEFAULT_INSTRUCTION_MODE, type WorkPlanFillPreset } from '../types/workPlanFillPreset'

const props = defineProps<{
  visible: boolean
  /** Sequence-owning root (R/B) doc id — the picker reads /workflow/sequence by this id. */
  docRef: string
  /** Optional work-plan values. Omitted means the legacy dialog state exactly. */
  preset?: WorkPlanFillPreset | null
  // 0234 B0001: runtime provider list + current selection, owned by MainPanel (which holds
  // the aiProvider store). Surfaced so the continuous run's provider is confirmable here.
  providers?: { id: string; name: string }[]
  selectedProvider?: string
  providerLoading?: boolean
  providerErrored?: boolean
  providerPinned?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  // 0234 B0001 RC3: propagate a provider change back to the store-owning parent.
  'update:provider': [value: string]
  // 0442 B0001: 프로바이더 탭에서 보이는 고정 배지·해제 단추는 없앴지만, 스토어를 가진
  // 부모와의 이벤트 계약은 그대로 둔다(T0004 §1 — 고정의 저장 수명과 해제 경로는 불변).
  'clear-provider-pin': []
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
    // 0346 T0005: [Message] tab — a common note for every hop, and item_seq -> note for the
    // steps the user singled out. Session-scoped, same as providerOverrides above.
    defaultMessage: string
    messageOverrides: Record<number, string>
    // 0352 T0004 §2/§3.7: ai_direct-only — item_seqs of individual N/T steps the user picked
    // for the SERVER to still auto-generate + auto-approve (like auto_approved does for that
    // one step). Always [] outside ai_direct.
    autoApproveItemSeqs: number[]
    // flowgate.default.0400 M0005: the per-hop wall-clock budget, in seconds. Session-scoped
    // (rides this run's start request only) — its last pick is remembered in localStorage so
    // reopening the dialog defaults to it, but it is never a persisted project setting.
    stepTimeoutSec: number
  }]
}>()

const { t } = useI18n()

const reviewMode = ref(false)
// 0409 B0001 rejection — the default selection is [Auto-approve] (auto_approved). 0406 T0022
// task 1 had flipped the default to ai_direct; this reverts it per the user's instruction:
// "원래 [자동승인] 이 기본 선택이였는데 왜 지시서 작성으로 선택되어있는거야". The value itself
// comes from a single place, DEFAULT_INSTRUCTION_MODE.
const instructionMode = ref<'auto_approved' | 'ai_direct'>(DEFAULT_INSTRUCTION_MODE)
// Timeout setting (0400 M0005): a fixed list of per-hop budgets, minutes. 60 is the default;
// unlimited was deliberately rejected (M0005 discussion) because it removes the only automatic
// guard against a runaway unmanned hop.
const STEP_TIMEOUT_OPTIONS_MIN = [30, 45, 60, 90, 120, 180, 240]
const STEP_TIMEOUT_DEFAULT_MIN = 60
const STEP_TIMEOUT_STORAGE_KEY = 'flowgate.continuousWork.stepTimeoutMinutes'
function loadStoredStepTimeoutMin(): number {
  try {
    const stored = Number(window.localStorage.getItem(STEP_TIMEOUT_STORAGE_KEY))
    if (STEP_TIMEOUT_OPTIONS_MIN.includes(stored)) return stored
  } catch {
    // localStorage unavailable (private mode, SSR, ...) — fall back to the default below.
  }
  return STEP_TIMEOUT_DEFAULT_MIN
}
// Not reset by installPreset: this is a local UI preference, not part of a work-plan preset or
// the per-run confirm state those functions manage.
const stepTimeoutMinutes = ref(loadStoredStepTimeoutMin())
watch(stepTimeoutMinutes, (value) => {
  try {
    window.localStorage.setItem(STEP_TIMEOUT_STORAGE_KEY, String(value))
  } catch {
    // Best-effort remembering only — a write failure must not block the dialog.
  }
})
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
// 0346 T0005: [Message] tab state — a common note plus item_seq -> note overrides, mirroring
// `overrides` above but additive rather than replacing (D0004 §3-3: an individual note does not
// push out the common note).
const defaultMessage = ref('')
const messageOverrides = ref<Record<number, string>>({})
// 0352 T0004 §2/§3.7: ai_direct-only per-item_seq server-auto-approve selection. A checked
// N/T step is server-generated + auto-approved exactly like auto_approved handles it, without
// switching the whole chain out of ai_direct.
const autoApproveItemSeqs = ref<number[]>([])
const presetActive = ref(false)
const presetTargetSeq = ref<number | null>(null)
const editedSeqs = ref(new Set<number>())
const prefilledMessageSeqs = ref(new Set<number>())
// 0408 M0019 3rd re-rejection ("문서에서 멘트와 프로바이더를 변경했는데 왜 다이얼로그에 적용되지 않는거지?"):
// the sequence rows are a SNAPSHOT of the work plan, taken when somebody last poured it. The
// plan kept moving afterwards, so the dialog was showing sentences and providers the person
// had already replaced in the document. These hold the plan's values as they are right now.
const planFill = ref<{
  wpDocId: string
  wpRevisionNo: number | null
  notes: Record<number, string>
  providers: Record<number, string>
} | null>(null)
// Steps the person edited by hand in THIS dialog. A later plan read never overwrites them —
// the newest word about a step is the one its owner just typed.
// 0444 T0007 (NR0003 §4-5): two sets, not one. They used to share a single set, so typing a
// sentence into a row also froze that row's PROVIDER against every later plan re-read — which
// is a large part of why the provider looked stuck to the person who reported this.
// A hand-typed value is only protected from the plan on the field it was actually typed in.
const touchedNoteSeqs = ref(new Set<number>())
const touchedProviderSeqs = ref(new Set<number>())
let planFillToken = 0
const presetRefreshMessage = ref('')
let initializingPreset = false
const presetUnsetCount = computed(() => (
  props.preset?.warnings.find(warning => warning.code === 'provider_unset')?.count ?? 0
))

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

// 0408 TR0021 2nd re-rejection ("자동승인 상태면 N/T 빼야지... 스크롤 생기니까 다 빼라"): reverts
// TR0018 rev1's "show every in-range row" table design. The rows an AI worker actually runs
// THIS SESSION — identical to `executionSteps`, and identical to the [Message] table below —
// are the only ones either tab draws now. A step this run auto-approves keeps its stored
// provider visible on the picker's own tag (`stepProviderTag`, gated on `inRangeSteps`), never
// as a dead row here.
const providerRows = computed<{ item: WorkflowStepItem; stepNo: number }[]>(
  () => executionSteps.value.map((item, idx) => ({ item, stepNo: idx + 1 })),
)

// Every step inside the chosen range, regardless of whether this run's mode auto-approves it —
// wider than `executionSteps`/`providerRows`/`messageRows` on purpose. Used only to keep
// state alive across a radio toggle (the picker's provider tag, a mention typed for a step that
// just became auto-approved): never to decide what a table renders.
const inRangeSteps = computed<WorkflowStepItem[]>(() => {
  const sel = picker.value.selection
  if (!sel || sel.fromDecision) return []
  return runnableSteps.value.filter(s => s.item_seq <= sel.targetSeq)
})

// 0408 M0019 1st re-rejection: the [Message] table. Same rows as `executionSteps` — the steps this run
// hands to an AI worker — because the mention is delivered to that worker and to nobody else.
// They are all numbered: every one of them runs.
// 0408 TR0021 2nd re-rejection: identical to `providerRows` now (both tabs draw the same run rows) —
// kept as a separate name for readability at each call site.
const messageRows = providerRows

/** Why the provider list is shorter than the step list — stated, not left to be guessed. */
const excludedNote = computed(() => {
  const sel = picker.value.selection
  if (!sel || sel.fromDecision) return ''
  const inRange = runnableSteps.value.filter(s => s.item_seq <= sel.targetSeq)
  const beyondCount = runnableSteps.value.length - inRange.length
  // 0408 TR0021 2nd re-rejection: auto-approved steps are excluded again (they no longer draw a row
  // of their own to explain themselves with) but this note still only counts steps past the
  // target — a step auto-approved WITHIN range still shows its stored provider on the picker's
  // tag, so nothing about it is actually unexplained.
  if (beyondCount > 0) return t('main.continuous_work.provider_scope_note_beyond', { beyond: beyondCount })
  return ''
})

// 0399 T0018 --------------------------------------------------------------------------------
// The normal "AI 호출" entry has no `preset` (that prop is only the legacy, pre-save work-plan
// apply-preview flow). But the sequence itself may already carry per-step notes saved via the
// [Edit Sequence] window (TR0015 note/source_doc_id columns, TR0017 direct-edit + type-change).
// D0010 §3.6: the mention field reads that stored value back in and counts (without blocking)
// the steps that still have none.
const sequenceSourceDocId = computed<string | null>(() => {
  for (const item of picker.value.steps ?? []) {
    if (item.source_doc_id) return item.source_doc_id
  }
  return null
})
const noteUnsetCount = computed(
  () => messageRows.value.filter(row => !stepMessageValue(row.item).trim()).length,
)
// Gated on an actual plan origin — a plain, hand-built sequence (never poured from a plan) has
// nothing to name here, and would otherwise show an empty-mention count on every open even
// though D0010 §3.5's "멘트 없는 단계" warning is scoped to the plan-pour flow.
const showSequenceOriginBanner = computed(() => !presetActive.value && !!sequenceSourceDocId.value)

function applySequenceNotePrefill(steps: WorkflowStepItem[]) {
  if (presetActive.value) return
  const filled = new Set<number>()
  for (const item of steps) {
    if (item.note && item.note.trim()) filled.add(item.item_seq)
  }
  // Sequence values are stored fallbacks, not request overrides. The input derives its visible
  // value from the row (or its pair); only a real edit below creates an override entry.
  messageOverrides.value = {}
  prefilledMessageSeqs.value = new Set(filled)
}

// 0405 L0010 §2.6 `project()` already answers exactly this question on the server — which
// plan step's provider/mention belongs to which sequence row, folded the way the chosen
// execution mode folds it (N/T -> NR/TR under [Auto-approve], own value wins over the folded one). It is
// the same call the work-plan apply preview makes; this reads it for a sequence that was
// poured earlier, so the document stays the thing a person edits and the dialog stops
// showing a stale copy of it.
async function loadPlanFill() {
  const wpDocId = sequenceSourceDocId.value
  if (presetActive.value || !wpDocId) {
    planFill.value = null
    return
  }
  const token = ++planFillToken
  try {
    const res = await postRequest<any>(
      '/api/v1/documents/' + encodeURIComponent(wpDocId) + '/work-plan/apply/preview',
      { instruction_mode: instructionMode.value },
    )
    if (token !== planFillToken) return
    const fill = res.data?.fill_preview ?? {}
    planFill.value = {
      wpDocId,
      wpRevisionNo: res.data?.wp_revision_no ?? null,
      notes: Object.fromEntries(
        Object.entries(fill.note_overrides ?? {}).map(([seq, note]) => [Number(seq), String(note)]),
      ),
      providers: Object.fromEntries(
        Object.entries(fill.provider_overrides ?? {}).map(([seq, id]) => [Number(seq), String(id)]),
      ),
    }
    applyPlanFill()
  } catch {
    // Best effort: an unreadable plan leaves every stored value exactly where it was. The
    // dialog must still open and still be usable without it.
    if (token === planFillToken) {
      planFill.value = null
    }
  }
}

/** Write the plan's current values in, for every step the person has not typed into. */
function applyPlanFill() {
  const fill = planFill.value
  if (!fill || presetActive.value) return
  const steps = picker.value.steps ?? []
  const nextNotes = { ...messageOverrides.value }
  const nextProviders = { ...overrides.value }
  for (const item of steps) {
    const seq = item.item_seq
    const planNote = fill.notes[seq]
    if (!touchedNoteSeqs.value.has(seq) && planNote !== undefined && planNote !== ownStoredMessage(item)) {
      nextNotes[seq] = planNote
    }
    const planProvider = fill.providers[seq]
    if (!touchedProviderSeqs.value.has(seq) && planProvider !== undefined && planProvider !== (item.provider_id ?? '')) {
      nextProviders[seq] = planProvider
    }
  }
  messageOverrides.value = nextNotes
  overrides.value = nextProviders
}

function revertSequenceNotes() {
  if (!window.confirm(t('main.continuous_work.preset_revert_confirm'))) return
  applySequenceNotePrefill(picker.value.steps ?? [])
}

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
const PAIRED_REPORT_TYPES: Record<string, string> = { N: 'NR', T: 'TR' }

function pairedReportProviderItem(item: WorkflowStepItem): WorkflowStepItem | null {
  if (item.provider_id) return null
  const reportType = PAIRED_REPORT_TYPES[String(item.type ?? '').toUpperCase()]
  if (!reportType) return null
  return [...(picker.value.steps ?? [])]
    .filter(candidate => candidate.item_seq > item.item_seq)
    .sort((a, b) => a.item_seq - b.item_seq)
    .find(candidate => String(candidate.type ?? '').toUpperCase() === reportType) ?? null
}

// 0408 M0019 2nd re-rejection ("TR/NR 의 멘트가 왜 T/N의 멘트를 사용하고 있지?"): a row shows its OWN
// note and never its partner's. The plan writes N/NR and T/TR as two separate sentences and
// the pour keeps them on two separate rows (work_plan_sequence_service.attach_auto_rows), so
// borrowing here would put a sentence in front of a worker it was not written for.
function ownStoredMessage(item: WorkflowStepItem): string {
  return String(item.note ?? '').trim()
}

function storedStepMessage(item: WorkflowStepItem): string {
  return ownStoredMessage(item)
}

function stepMessageValue(item: WorkflowStepItem): string {
  return messageOverrides.value[item.item_seq] ?? ownStoredMessage(item)
}

function storedProviderItem(item: WorkflowStepItem): WorkflowStepItem | null {
  if (item.provider_id) return item.provider_registered !== false ? item : null
  const paired = pairedReportProviderItem(item)
  if (!paired?.provider_id || paired.provider_registered === false) return null
  return paired
}

function storedProviderValue(item: WorkflowStepItem): string {
  return storedProviderItem(item)?.provider_id ?? props.selectedProvider ?? ''
}

function providerBadgeKey(item: WorkflowStepItem): string {
  if (overrides.value[item.item_seq] !== undefined) return ''
  if (props.providerPinned) {
    // 0444 T0007 (NR0003 §4-5): a pin used to blank this column outright, so the table gave
    // no sign that the row had a stored provider of its own being set aside. An unusable
    // stored provider still reports itself first — that is the more urgent of the two facts.
    if (item.provider_id && item.provider_registered === false) {
      return 'main.continuous_work.sequence_provider_unavailable'
    }
    const displaced = storedProviderItem(item)
    return displaced && (displaced.provider_id ?? '') !== props.selectedProvider
      ? 'main.continuous_work.sequence_provider_pin_overridden'
      : ''
  }
  if (item.provider_id) {
    return item.provider_registered === false
      ? 'main.continuous_work.sequence_provider_unavailable'
      : ''
  }
  return storedProviderItem(item)
    ? 'main.continuous_work.sequence_provider_inherited'
    : ''
}

function stepProviderValue(item: WorkflowStepItem): string {
  return overrides.value[item.item_seq] ?? (props.providerPinned ? (props.selectedProvider ?? '') : storedProviderValue(item))
}

function markPresetEdited(itemSeq: number) {
  if (!presetActive.value) return
  editedSeqs.value = new Set([...editedSeqs.value, itemSeq])
}

function onStepProviderChange(item: WorkflowStepItem, value: string) {
  markPresetEdited(item.item_seq)
  touchedProviderSeqs.value = new Set([...touchedProviderSeqs.value, item.item_seq])
  const next = { ...overrides.value }
  const inherited = props.providerPinned ? (props.selectedProvider ?? '') : storedProviderValue(item)
  if (!value || value === inherited) delete next[item.item_seq]
  else next[item.item_seq] = value
  overrides.value = next
}
// 0346 T0005 §2-1 item 4: blank (or whitespace-only) input is the same as "no individual note
// for this step" — mirrors onStepProviderChange's "같으면 삭제" rule with a "비면 삭제" rule.
function onStepMessageChange(item: WorkflowStepItem, value: string) {
  const itemSeq = item.item_seq
  markPresetEdited(itemSeq)
  touchedNoteSeqs.value = new Set([...touchedNoteSeqs.value, itemSeq])
  const next = { ...messageOverrides.value }
  const stored = storedStepMessage(item)
  if (value === stored) {
    delete next[itemSeq]
  } else if (!value.trim()) {
    if (stored || prefilledMessageSeqs.value.has(itemSeq)) next[itemSeq] = ''
    else delete next[itemSeq]
  } else {
    next[itemSeq] = value
  }
  messageOverrides.value = next
}

function providerName(id: string | undefined | null): string | null {
  if (!id) return null
  return props.providers?.find(p => p.id === id)?.name ?? null
}

const selectedProviderName = computed(() =>
  providerName(props.selectedProvider) ?? props.selectedProvider ?? '',
)

// 0408 TR0018 rev1: an auto-approved N/T row used to lose its provider tag here too, so the
// same step read "시퀀스 저장값 · X" under one radio and only "자동 승인" under the other.
// In-range steps all get the tag (gated on `inRangeSteps`, wider than the now execution-only
// `providerRows`); a step past the target still gets none (it never runs).
function stepProviderTag(item: WorkflowStepItem): { text: string; override: boolean; pinned?: boolean } | null {
  if (!inRangeSteps.value.some(s => s.item_seq === item.item_seq)) return null
  const stepNo = executionSteps.value.findIndex(s => s.item_seq === item.item_seq)
  const overrideId = overrides.value[item.item_seq]
  // A row outside executionSteps has no run step number. It also gets no select, so it cannot
  // carry an override today — but never print "실행단계0" if that ever changes.
  if (overrideId && stepNo >= 0) {
    const name = providerName(overrideId)
    if (!name) return null
    return { text: t('main.continuous_work.provider_tag_override', { step: stepNo + 1, name }), override: true }
  }
  if (props.providerPinned) {
    const name = selectedProviderName.value
    if (!name) return null
    // 0444 T0007 (NR0003 §4-5): the pin really does beat a row's stored provider — that is
    // what ai_invoke_service.start_run() does for a continuous run, and NR0003 §2-5 re-ran it
    // to be certain. Flipping this side to "stored wins" would leave the screen showing one
    // provider while another one runs, which is the worse defect. What was actually wrong is
    // that the swap happened in silence, so the row now names BOTH: the pin that won and the
    // stored value it displaced.
    const displaced = storedProviderItem(item)
    const displacedId = displaced?.provider_id ?? ''
    if (displaced && displacedId !== props.selectedProvider) {
      return {
        text: t('main.continuous_work.provider_tag_pinned_over_stored', {
          name,
          stored: displaced.provider_display_name || displacedId,
        }),
        override: false,
        pinned: true,
      }
    }
    return { text: t('main.continuous_work.provider_tag_default', { name }), override: false }
  }
  const stored = storedProviderItem(item)
  if (stored) {
    const name = stored.provider_display_name || stored.provider_id || ''
    return { text: t('main.continuous_work.provider_tag_stored', { name }), override: false }
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
  // A preset is installed before the picker finishes loading. Do not interpret that temporary
  // empty execution list as the user shrinking the run, or the injected overrides vanish.
  if (picker.value.loading || picker.value.selection == null) return
  const inRun = new Set(steps.map(s => s.item_seq))
  const kept = Object.entries(overrides.value).filter(([seq]) => inRun.has(Number(seq)))
  if (kept.length !== Object.keys(overrides.value).length) {
    overrides.value = Object.fromEntries(kept)
  }
  // Mentions stay attached to every row inside the chosen range regardless of mode (gated on
  // `inRangeSteps`, not the now execution-only `providerRows`). Changing only the execution
  // mode must not erase N/T values; shrinking the target still removes rows that leave the range.
  const inMessageRange = new Set(inRangeSteps.value.map(s => s.item_seq))
  const keptMessages = Object.entries(messageOverrides.value).filter(([seq]) => inMessageRange.has(Number(seq)))
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
  // A mention only rides the request for a step this run hands to a worker. Values typed for
  // a step the mode currently auto-approves stay in `messageOverrides` (the radio may go back)
  // but are never sent, because no hop would read them.
  const messageOverridesOut: Record<number, string> = {}
  for (const [seq, note] of Object.entries(messageOverrides.value)) {
    const itemSeq = Number(seq)
    if (!inRun.has(itemSeq)) continue
    if (note && note.trim()) messageOverridesOut[itemSeq] = note
    else messageOverridesOut[itemSeq] = ''
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
    stepTimeoutSec: stepTimeoutMinutes.value * 60,
  })
}

function close() {
  emit('update:visible', false)
}

function installPreset(value: WorkPlanFillPreset | null | undefined) {
  initializingPreset = true
  reviewMode.value = false
  activeTab.value = 'basic'
  presetRefreshMessage.value = ''
  editedSeqs.value = new Set()
  prefilledMessageSeqs.value = new Set()
  touchedNoteSeqs.value = new Set()
  touchedProviderSeqs.value = new Set()
  planFill.value = null
  planFillToken += 1
  if (value) {
    presetActive.value = true
    instructionMode.value = value.instructionMode
    presetTargetSeq.value = value.targetSeq
    overrides.value = { ...value.providerOverrides }
    defaultMessage.value = value.defaultMessage
    messageOverrides.value = { ...value.messageOverrides }
    prefilledMessageSeqs.value = new Set(
      Object.entries(value.messageOverrides)
        .filter(([, note]) => !!note.trim())
        .map(([seq]) => Number(seq)),
    )
  } else {
    presetActive.value = false
    presetTargetSeq.value = null
    // The reset must land on the same default too — if even one place keeps a different value,
    // the result diverges by entry point.
    instructionMode.value = DEFAULT_INSTRUCTION_MODE
    overrides.value = {}
    defaultMessage.value = ''
    messageOverrides.value = {}
  }
  setTimeout(() => { initializingPreset = false }, 0)
}

async function refreshPresetForMode() {
  if (!props.preset || !presetActive.value) return
  try {
    const res = await postRequest<any>(
      '/api/v1/documents/' + encodeURIComponent(props.preset.sourceDocId) + '/work-plan/apply/preview',
      { instruction_mode: instructionMode.value },
    )
    const fill = res.data.fill_preview ?? {}
    const incomingProviders: Record<number, string> = Object.fromEntries(
      Object.entries(fill.provider_overrides ?? {}).map(([key, value]) => [Number(key), String(value)]),
    )
    const incomingMessages: Record<number, string> = Object.fromEntries(
      Object.entries(fill.note_overrides ?? {}).map(([key, value]) => [Number(key), String(value)]),
    )
    const keepEdited = editedSeqs.value
    for (const seq of keepEdited) {
      if (overrides.value[seq] !== undefined) incomingProviders[seq] = overrides.value[seq]
      if (messageOverrides.value[seq] !== undefined) incomingMessages[seq] = messageOverrides.value[seq]
    }
    overrides.value = incomingProviders
    messageOverrides.value = incomingMessages
    presetTargetSeq.value = fill.target_seq ?? presetTargetSeq.value
    presetRefreshMessage.value = t('main.continuous_work.preset_mode_refreshed', { n: keepEdited.size })
  } catch (e: any) {
    presetRefreshMessage.value = e?.response?.data?.message || t('main.continuous_work.preset_mode_failed')
  }
}

function revertPreset() {
  if (!window.confirm(t('main.continuous_work.preset_revert_confirm'))) return
  installPreset(null)
}

watch(
  () => props.visible,
  (val) => {
    if (val) {
      // The picker reloads itself off `active`; reset only what this dialog owns.
      installPreset(props.preset)
      autoApproveItemSeqs.value = []
    }
  },
  { immediate: true },
)
watch(
  () => props.preset,
  (value) => {
    if (props.visible) installPreset(value)
  },
)
watch(instructionMode, (value, previous) => {
  if (initializingPreset || value === previous) return
  // 0405 L0010 §2.6: which row a plan step lands on depends on the mode (auto-approved folds
  // N/T onto NR/TR), so the projection is read again rather than re-keyed here.
  if (presetActive.value) void refreshPresetForMode()
  else void loadPlanFill()
})

// 0399 T0018: re-seed from the sequence's own stored notes each time a fresh /workflow/sequence
// load lands (steps is a new array reference only on an actual reload, never on a same-data
// re-emit like a target-step click — see WorkflowStepPicker's `state` computed). Skipped while a
// (legacy) preset is installed — that flow owns messageOverrides itself.
watch(
  () => picker.value.steps,
  (steps, prevSteps) => {
    if (!steps || steps === prevSteps) return
    applySequenceNotePrefill(steps)
    void loadPlanFill()
  },
)
// Reverting an unsaved preset (installPreset(null)) hands the field back to the sequence's own
// stored notes instead of leaving it blank.
watch(presetActive, (active) => {
  if (!active && picker.value.steps?.length) applySequenceNotePrefill(picker.value.steps)
})
</script>

<style scoped>
.cwd-preset-banner,.cwd-preset-refresh {
  flex:0 0 auto; display:flex; align-items:center; justify-content:space-between; gap:10px;
  padding:8px 14px; background:#ecfeff; border-bottom:1px solid #a5f3fc; font-size:.74rem;
}
.cwd-preset-banner div { display:flex; flex-wrap:wrap; gap:8px; align-items:center; }
.cwd-preset-banner span { color:var(--text-m); }
.cwd-preset-refresh { background:var(--surface-h); justify-content:flex-start; }
.cwd-filled-badge { flex:0 0 auto; font-size:.61rem; color:#0f766e; background:#ccfbf1; border-radius:99px; padding:2px 5px; }
.cwd-stored-provider--unavailable { color:#b45309; background:#fff7ed; }
/* 0444 T0007: the pin-displaced-a-stored-provider badge gets its own modifier, so a test can
   tell it apart from the unavailable one. */
.cwd-stored-provider--pin-overridden { color:var(--warning); background:var(--warning-l); }
/* 0399 T0018: notice for steps with no mention — informational, never blocks (D0010 §3.5). */
.cwd-note-unset-flag { color:#b45309; font-weight:700; }
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
/* Timeout setting (0400 M0005 / TR0007 2nd rejection: "리스트 박스로 해라... 콤보박스"): a native
   select combo box, same input pattern as .aip-select-input in AiProviderSelect.vue. */
.cwd-timeout-combo {
  position: relative;
  display: flex;
}
.cwd-timeout-select {
  flex: 1;
  min-width: 0;
  width: 100%;
  padding: 8px 30px 8px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r-sm);
  background: var(--surface);
  color: var(--text);
  font-size: .82rem;
  cursor: pointer;
  appearance: none;
  -webkit-appearance: none;
  -moz-appearance: none;
}
.cwd-timeout-caret {
  position: absolute;
  right: 10px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text-m);
  font-size: .7rem;
  pointer-events: none;
}
.cwd-timeout-desc {
  margin: 0;
  font-size: .76rem;
  color: var(--text-m);
  line-height: 1.4;
}
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
/* 0346 T0005: [Message] tab text inputs — the header row uses the regular provider-select
   sizing, the per-step row uses the compact override sizing (mirrors .cwd-override-select).
   rev1: `font: inherit` pulled in the page's base font-size (larger than the provider select's
   own .82rem), and the 6px/10px padding didn't match the select's 5px/8px either — next to the
   provider tab's default-provider select the message tab's common-note input read as visibly
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
