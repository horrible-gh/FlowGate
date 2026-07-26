<template>
  <div class="wsp">
    <div v-if="loading" class="wsp-state">
      <AppIcon name="spinner" spin /> {{ t('common.loading') }}
    </div>
    <div v-else-if="errorKey" class="wsp-state wsp-state--error">
      <AppIcon name="warning" /> {{ t(errorKey) }}
    </div>
    <template v-else>
      <!-- R0001 "워크플로 결정부터": when the workflow has not been decided yet, the
           continuous run starts FROM the workflow-decision step itself. There are no
           concrete sequence items to pick (they don't exist until the AI decides), so
           the run goes from the decision through the WHOLE decided sequence. -->
      <template v-if="fromDecision">
        <div class="wsp-section-title">{{ t('main.continuous_work.from_decision_title') }}</div>
        <div class="wsp-steps">
          <div class="wsp-step wsp-step--head wsp-step--target wsp-step--static">
            <span class="wsp-step-dot"><AppIcon name="radio-button" /></span>
            <span class="wsp-step-badge doc-tag c-R">WF</span>
            <span class="wsp-step-label">{{ t('main.continuous_work.from_decision_step') }}</span>
            <span class="wsp-step-tag wsp-step-tag--head">{{ t('main.continuous_work.head_tag') }}</span>
          </div>
        </div>
        <div class="wsp-note wsp-note--info">
          <AppIcon name="info" />
          {{ t('main.continuous_work.from_decision_note') }}
        </div>
      </template>
      <template v-else>
        <!-- R0001: pick how far to continue. Steps before the current head are 'done'
             and disabled (no skipping / no mid-start); the run goes from the head to the
             checked step inclusive. When everything is already done (allDone) the list is
             read-only and just lets the user review what ran. -->
        <div class="wsp-section-title">
          {{ allDone ? t('main.continuous_work.all_done_title') : t('main.continuous_work.pick_target') }}
        </div>
        <div ref="stepsRef" class="wsp-steps">
          <button
            v-for="(item, idx) in items"
            :key="item.item_seq"
            type="button"
            class="wsp-step"
            :data-step-idx="idx"
            :class="{
              'wsp-step--done': idx < headIdx,
              'wsp-step--in-range': idx >= headIdx && idx <= selectedIdx,
              'wsp-step--target': idx === selectedIdx,
              'wsp-step--head': idx === headIdx,
            }"
            :disabled="idx < headIdx"
            @click="selectTarget(idx)"
          >
            <span class="wsp-step-dot">
              <AppIcon
                :name="idx < headIdx
                  ? 'check-circle'
                  : idx === selectedIdx
                    ? 'radio-button'
                    : 'circle'"
              />
            </span>
            <span class="wsp-step-badge doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
            <span class="wsp-step-label">{{ item.label }}</span>
            <span v-if="idx === headIdx" class="wsp-step-tag wsp-step-tag--head">
              {{ t('main.continuous_work.head_tag') }}
            </span>
            <span v-else-if="idx < headIdx" class="wsp-step-tag wsp-step-tag--done">
              {{ t('main.continuous_work.done_tag') }}
            </span>
            <span
              v-if="idx >= headIdx && stepTags[idx]"
              class="wsp-prov-tag"
              :class="{ 'wsp-prov-tag--override': stepTags[idx]!.override }"
            >{{ stepTags[idx]!.text }}</span>
          </button>
        </div>

        <div v-if="headInProgress" class="wsp-note wsp-note--warn">
          <AppIcon name="warning-circle" />
          {{ t('main.continuous_work.head_in_progress_note') }}
        </div>
        <div v-if="allDone" class="wsp-note wsp-note--info">
          <AppIcon name="check-circle" />
          {{ t('main.continuous_work.all_done_note') }}
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
// 0242 NR0003 권고 1: the "how far do we run?" step list, extracted out of
// ContinuousWorkDialog so the AI-invoke dialog can present the SAME picker instead of a raw
// `목표 seq` number input. Both continuous-run entry points now go through this component, so
// a target is always an existing, not-yet-done step — the class of silent under/over-run that
// a mistyped seq caused (NR0003 발견 4) cannot be expressed here.
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import type {
  WorkflowStepItem,
  WorkflowStepPickerState,
  WorkflowStepSelection,
} from '../types/workflowStepPicker'

const props = defineProps<{
  /**
   * Sequence-owning root (R/B) doc id — /workflow/sequence is keyed by the root, so a member
   * doc (T/TR/…) must be resolved to its parent R BEFORE it reaches this prop (NR0003 권고 2).
   */
  docRef: string
  /** Load/refresh the sequence when this flips true (dialog opened, continuous mode picked). */
  active: boolean
  /**
   * 0317 T0010 rev4: per-step "which provider will actually run this" tag, shown next to
   * runnable (not-yet-done) steps. Owned by the caller (ContinuousWorkDialog) so this shared
   * picker stays provider-agnostic; callers that omit it (AiInvokeDialog) render no tag.
   */
  stepTag?: (item: WorkflowStepItem) => { text: string; override: boolean } | null
}>()

const emit = defineEmits<{
  change: [state: WorkflowStepPickerState]
}>()

const { t } = useI18n()

// Run-to-end sentinel: reported as targetSeq when starting before the workflow is decided.
// The server resolves it to the last item_seq of the freshly-decided sequence.
const TARGET_TO_END = -1

const loading = ref(false)
const errorKey = ref<string | null>(null)
const items = ref<WorkflowStepItem[]>([])
// Scroll container for the step list (capped + overflow-y:auto). Used to bring the current
// execution point into view (R0001).
const stepsRef = ref<HTMLElement | null>(null)
const selectedIdx = ref(-1)
// R0001 "워크플로 결정부터": set when /workflow/sequence reports the sequence is not decided
// yet. Instead of blocking, the picker offers to start the run from the workflow decision.
const fromDecision = ref(false)
// R0001 (group 0129): set when every step is already done (head=null). The run has nothing
// left to continue, but the user still opened the dialog to SEE what ran — so we keep showing
// the step list (read-only, scrolled to the most recent step) instead of blanking it out with
// a bare "all done" error. Repro: flowgate.default.0094.0001-R = 8 T/TR steps, all done.
const allDone = ref(false)

// Head = first step that is not yet done. Steps before it are completed and cannot be a
// target (no skipping / no mid-start, R0001). All-done → no head (headIdx = length).
const headIdx = computed(() => {
  const i = items.value.findIndex(it => it.status !== 'done')
  return i === -1 ? items.value.length : i
})
const headInProgress = computed(
  () => headIdx.value < items.value.length && items.value[headIdx.value].status === 'in_progress',
)
// Memoized per-step tags (0317 T0010 rev4): computed once per items/props.stepTag change
// rather than re-invoked on every template re-render.
const stepTags = computed(() => items.value.map(it => props.stepTag?.(it) ?? null))
const stepCount = computed(() =>
  selectedIdx.value >= headIdx.value ? selectedIdx.value - headIdx.value + 1 : 0,
)

const selection = computed<WorkflowStepSelection | null>(() => {
  if (loading.value || errorKey.value) return null
  if (fromDecision.value) {
    // Pre-decision: the workflow-decision step is the head and the run goes to the end of the
    // whole decided sequence. No concrete target item exists yet → run-to-end sentinel.
    return {
      targetSeq: TARGET_TO_END,
      targetType: '',
      targetLabel: t('main.continuous_work.from_decision_target_label'),
      stepCount: 0,
      fromDecision: true,
    }
  }
  if (headIdx.value >= items.value.length) return null
  if (selectedIdx.value < headIdx.value) return null
  const target = items.value[selectedIdx.value]
  return {
    targetSeq: target.item_seq,
    targetType: target.type,
    targetLabel: target.label,
    stepCount: stepCount.value,
    fromDecision: false,
  }
})

const state = computed<WorkflowStepPickerState>(() => ({
  loading: loading.value,
  errorKey: errorKey.value,
  allDone: allDone.value,
  fromDecision: fromDecision.value,
  selection: selection.value,
  // 0317 D0004: expose the loaded steps so the continuous dialog can list the distinct doc
  // types for per-document-type provider assignment. Consumers that ignore it are unaffected.
  steps: items.value,
}))

function selectTarget(idx: number) {
  if (idx < headIdx.value) return
  selectedIdx.value = idx
}

// R0001: when a sequence has many completed steps the capped step list shows only the top
// 'done' rows, leaving the step that is actually running (the head) — or, when everything is
// done, the most recent step — scrolled out of view. Scroll the list so the current execution
// point is visible. Done steps above it scroll off the top.
//
// Writes scrollTop in LAYOUT coordinates (offsetTop / scrollHeight), NOT getBoundingClientRect.
// The modal opens with a `transform: scale(.97)` keyframe (.modal-box `mIn`); a rect-difference
// read taken mid-animation is scaled and lands short, leaving the user parked on the completed
// steps. offsetTop is the untransformed layout offset relative to `.wsp-steps` (which is
// position:relative), so it is immune to that animation and is idempotent across re-applies.
function applyReveal() {
  const container = stepsRef.value
  if (!container) return
  // All steps done (no head): there is nothing to "reveal at the top" — scroll to the very
  // bottom so the most recently executed step is what the user lands on (R0001:
  // "스크롤을 가장 하단으로 내려서 어떤 문서가 실행되는지 보이게").
  if (headIdx.value >= items.value.length) {
    container.scrollTop = container.scrollHeight
    return
  }
  const row = container.querySelector<HTMLElement>(`[data-step-idx="${headIdx.value}"]`)
  if (!row) {
    container.scrollTop = container.scrollHeight
    return
  }
  // Align the head row to the top of the visible area (revealing it + the steps below it),
  // scoped to this scroll container so it never jumps the surrounding page.
  container.scrollTop = row.offsetTop
}

async function revealActiveStep() {
  await nextTick()
  // Apply once now (covers the common case + the unit test). Then re-apply on the next animation
  // frames: on a fresh open the capped list (max-height) may not have overflowed yet when the
  // first write runs, so scrollTop clamps to 0 and the list stays on the completed steps. Once
  // layout settles (and the .15s open animation is past) the re-apply makes the scroll stick.
  // offsetTop-based, so re-applying is idempotent — it never over-scrolls.
  applyReveal()
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      applyReveal()
      requestAnimationFrame(applyReveal)
    })
  }
}

async function loadSequence() {
  loading.value = true
  errorKey.value = null
  items.value = []
  selectedIdx.value = -1
  fromDecision.value = false
  allDone.value = false
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.docRef })
    const data = res.data as any
    // The live /workflow/sequence is served by workflow_head_routes (included before the
    // decision_routes handler in routers/main.py, so it shadows it), whose response shape is
    // { decided, sequence, head } — NOT the { items } / 400-on-undecided shape this picker
    // first targeted. Read `sequence` (fall back to `items` for the shadowed handler), exactly
    // as the sibling WorkflowDecisionModal does (`data?.items ?? data?.sequence`).
    items.value = (data?.sequence ?? data?.items ?? []) as WorkflowStepItem[]
    if (data?.decided === false || items.value.length === 0) {
      // R0001 "워크플로 결정부터": the workflow has not been decided yet. head_routes signals
      // this with 200 + decided:false (NOT a 400), so do NOT block with "워크플로 단계가
      // 없습니다" (error_empty). Start the continuous run from the workflow-decision step and
      // run to the end of the sequence the AI decides; the server self-chains from the decide
      // response.
      fromDecision.value = true
    } else if (headIdx.value >= items.value.length) {
      // R0001 (group 0129) repro flowgate.default.0094.0001-R: every step is done. Do NOT
      // replace the whole picker with a bare "모든 단계가 이미 완료되었습니다" error that hides
      // the steps — the user opened this to SEE what ran ("들어가보면 전부 완료된것만 보이잖아").
      // Keep the (read-only) step list and scroll to the most recent step. The consumer's
      // confirm button stays disabled because `selection` is null (no head to continue from).
      allDone.value = true
      void revealActiveStep()
    } else {
      // Default to running the whole remaining sequence, so a user who knows nothing about
      // the sequence can just confirm and get the natural result (NR0003 권고 1).
      selectedIdx.value = items.value.length - 1
      // Bring the current execution point into view instead of leaving the user parked on the
      // completed steps at the top (R0001).
      void revealActiveStep()
    }
  } catch (e: any) {
    // Defensive fallback: the shadowed decision_routes handler would return 400
    // sequence_not_decided. Treat it the same as decided:false.
    const code = e?.response?.data?.error
    if (e?.response?.status === 400 && code === 'sequence_not_decided') {
      fromDecision.value = true
    } else {
      errorKey.value = 'main.continuous_work.error_load'
    }
  } finally {
    loading.value = false
  }
}

// The consumer holds the confirm button, so it needs every state transition — not just the
// final one. Emitting the whole state (rather than exposing refs) keeps the picker the single
// owner of head/target/all-done semantics.
watch(state, (val) => emit('change', val), { immediate: true })

watch(
  () => props.active,
  (val) => {
    if (val) void loadSequence()
  },
  { immediate: true },
)
</script>

<style scoped>
.wsp {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.wsp-state {
  padding: 24px;
  text-align: center;
  font-size: .85rem;
  color: var(--text-m);
}
.wsp-state--error { color: var(--danger); }
.wsp-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
}
.wsp-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 6px;
  /* Anchor for child .wsp-step offsetTop so revealActiveStep() can scroll the running step
     to the top in layout coordinates (immune to the modal's open `scale()` animation). */
  position: relative;
  /* R0001: when a sequence has many steps the list grew unbounded and pushed the
     review-mode toggle / summary / footer out of view. Cap the list and let it scroll
     on its own so those controls stay visible. Short sequences show no scrollbar. */
  max-height: min(280px, 40vh);
  overflow-y: auto;
}
.wsp-step {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 10px;
  border: 1px solid transparent;
  border-radius: var(--r-sm);
  background: none;
  cursor: pointer;
  text-align: left;
  transition: background var(--tr), border-color var(--tr);
}
.wsp-step:hover:not(:disabled):not(.wsp-step--static) { background: var(--surface-h); }
.wsp-step:disabled { cursor: not-allowed; }
.wsp-step--static { cursor: default; }
.wsp-step--done { opacity: .5; }
.wsp-step--in-range { background: var(--primary-l); }
.wsp-step--target { border-color: var(--primary); }
.wsp-step-dot {
  flex-shrink: 0;
  font-size: .8rem;
  color: var(--text-m);
}
.wsp-step--in-range .wsp-step-dot { color: var(--primary); }
.wsp-step--done .wsp-step-dot { color: var(--success, #16a34a); }
.wsp-step-badge {
  font-size: .65rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
}
.wsp-step-label {
  flex: 1;
  font-size: .82rem;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.wsp-step-tag {
  font-size: .6rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .04em;
  padding: 1px 6px;
  border-radius: 10px;
  flex-shrink: 0;
}
.wsp-step-tag--head { background: var(--primary-l); color: var(--primary); }
.wsp-step-tag--done { background: var(--surface-h); color: var(--text-m); }
.wsp-prov-tag {
  font-size: .62rem;
  font-weight: 700;
  color: var(--text-m);
  background: var(--surface-h);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 1px 7px;
  flex-shrink: 0;
  white-space: nowrap;
}
.wsp-prov-tag--override {
  color: var(--primary);
  border-color: var(--primary);
  background: var(--primary-l);
}
.wsp-note {
  font-size: .78rem;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: var(--r-sm);
}
.wsp-note--warn {
  background: #fef3c7;
  color: #92600a;
  border: 1px solid #fde68a;
}
.wsp-note--info {
  background: var(--primary-l);
  color: var(--primary);
  border: 1px solid var(--primary);
}
</style>
