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
              'wsp-step--auto': autoHandledIdx(idx),
            }"
            :disabled="idx < headIdx || autoHandledIdx(idx)"
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
            <!-- 0352 T0004 §3.7: per-item_seq N/T auto-approve toggle — only for candidate
                 types (N/T under ai_direct) at or past the head. A dedicated class + its own
                 click/change stop keeps it from re-triggering the row's own target-select
                 click (the row is a <button>).
                 rev2 (rejected twice: "체크박스만 덜렁 나오면 뭔지 알아보기 힘들다" / "다른 형태의
                 방식은 없냐"): a bare checkbox carried no caption, so the row gave no clue what
                 checking it did. Redrawn as a labelled switch — track+thumb shape instead of a
                 checkbox square, plus a visible text caption and a hover tooltip that spells out
                 the effect. The underlying `<input type="checkbox">` and its `checked`/`change`
                 contract are untouched (tests still query `.wsp-step-auto-toggle input`); only
                 what's drawn around it changed. -->
            <label
              v-if="idx >= headIdx && idx <= selectedIdx && autoApproveCandidateIdx(idx)"
              class="wsp-step-auto-toggle"
              :title="t('main.continuous_work.auto_toggle_hint')"
              @click.stop
            >
              <span class="wsp-step-auto-toggle-switch">
                <input
                  type="checkbox"
                  :checked="autoApproveSelectedSet.has(item.item_seq)"
                  @click.stop
                  @change="onAutoApproveToggle(item.item_seq, ($event.target as HTMLInputElement).checked)"
                />
                <span class="wsp-step-auto-toggle-track">
                  <span class="wsp-step-auto-toggle-thumb" />
                </span>
              </span>
              <span class="wsp-step-auto-toggle-text">{{ t('main.continuous_work.auto_toggle_label') }}</span>
            </label>
            <span class="wsp-step-badge doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
            <span class="wsp-step-label">{{ item.label }}</span>
            <!-- 0451 T0007 rev1: back to the pre-T0007 shape the rejection asked for — the state
                 badge (현재/완료/자동 승인) sits directly after the flex:1 label, which is what
                 pushes it to the row's right edge; no fixed-width slot wrapper. The provider
                 badge is gone entirely (좌측단에 프로바이더는 출력하지 않는다) — a step's provider
                 is named in the dialog's [프로바이더] tab, and the fixed 224px row end this
                 replaces is what made long N/T rows overflow to the right. -->
            <span
              v-if="stateTag(idx)"
              class="wsp-step-tag"
              :class="`wsp-step-tag--${stateTag(idx)!.kind}`"
            >{{ stateTag(idx)!.text }}</span>
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
// 0242 NR0003 recommendation 1: the "how far do we run?" step list, extracted out of
// ContinuousWorkDialog so the AI-invoke dialog can present the SAME picker instead of a raw
// `목표 seq` number input. Both continuous-run entry points now go through this component, so
// a target is always an existing, not-yet-done step — the class of silent under/over-run that
// a mistyped seq caused (NR0003 finding 4) cannot be expressed here.
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'
import { findSequenceHeadIndex } from '../composables/useSequenceStepNote'
import type {
  WorkflowStepItem,
  WorkflowStepPickerState,
  WorkflowStepSelection,
} from '../types/workflowStepPicker'

const props = defineProps<{
  /**
   * Sequence-owning root (R/B) doc id — /workflow/sequence is keyed by the root, so a member
   * doc (T/TR/…) must be resolved to its parent R BEFORE it reaches this prop (NR0003 recommendation 2).
   */
  docRef: string
  /** Load/refresh the sequence when this flips true (dialog opened, continuous mode picked). */
  active: boolean
  // 0451 T0007 rev1: the per-step provider tag prop (`stepTag`, 0317 T0010 rev4) is removed.
  // This picker is provider-agnostic again — its only provider-aware caller
  // (ContinuousWorkDialog) names a step's provider in its own [프로바이더] tab instead.
  /**
   * 0337 R0001-1: doc types the SERVER generates and approves by itself under the caller's
   * instruction mode (auto_approved → N/T). Such a step still runs, but no AI worker performs
   * it, so it is not a meaningful stop point: N+NR / T+TR is one logical unit whose boundary is
   * the paired report. Those rows render read-only ("자동 승인") instead of being selectable.
   * Callers that omit this prop keep the unrestricted picker.
   */
  autoHandledTypes?: string[]
  /**
   * 0352 T0004 §2/§3.7: item_seqs the caller's ai_direct chain has selected for server
   * auto-handling — the per-STEP-INSTANCE counterpart of `autoHandledTypes` (which excludes
   * every step of a TYPE under auto_approved). A selected item_seq is excluded from the
   * target choice the exact same way, and the target re-points to its paired report if it
   * was sitting on the step that just got selected (shared `selectableIdxSet` machinery).
   */
  autoHandledItemSeqs?: number[]
  /**
   * 0352 T0004 §3.7: doc types eligible for the per-step auto-approve checkbox itself (N/T,
   * passed only when the caller's instruction mode is ai_direct). Omitted/empty ⇒ no checkbox
   * renders anywhere — callers that don't support the feature (AiInvokeDialog's own picker)
   * are unaffected.
   */
  autoApproveCandidateTypes?: string[]
  /** Controlled: which item_seqs are currently checked (mirrors `autoHandledItemSeqs`, but
   * the caller owns the source of truth so unrelated re-renders don't fight the checkbox). */
  autoApproveSelected?: number[]
  /** Optional target injected by a work-plan fill preset. */
  initialTargetSeq?: number | null
}>()

const emit = defineEmits<{
  change: [state: WorkflowStepPickerState]
  /** 0352 T0004 §3.7: user (un)checked one step's auto-approve toggle. */
  'toggle-auto-approve': [itemSeq: number, checked: boolean]
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
const headIdx = computed(() => findSequenceHeadIndex(items.value))
const headInProgress = computed(
  () => headIdx.value < items.value.length && items.value[headIdx.value].status === 'in_progress',
)
// 0337 R0001-1 ---------------------------------------------------------------------------
// Which of the remaining steps the user may actually stop at. A server-auto-handled type
// (auto_approved N/T) is executed without an AI worker, so picking it as the target would put
// the displayed boundary one step away from the real one — the server auto-approves it and the
// paired report runs anyway (NR0003 finding 3). Exclude it from the choice instead.
const autoHandledTypeSet = computed(
  () => new Set((props.autoHandledTypes ?? []).map(s => s.toUpperCase())),
)
// 0352 T0004 §2/§3.7: the per-item_seq selection, same exclusion effect as the per-TYPE set
// above but scoped to individual step instances (so the same T appearing twice can differ).
const autoHandledItemSeqSet = computed(() => new Set(props.autoHandledItemSeqs ?? []))
const selectableIdxSet = computed(() => {
  const remaining: number[] = []
  for (let i = headIdx.value; i < items.value.length; i += 1) remaining.push(i)
  const selectable = remaining.filter(
    i => !autoHandledTypeSet.value.has(String(items.value[i].type ?? '').toUpperCase())
      && !autoHandledItemSeqSet.value.has(items.value[i].item_seq),
  )
  // Guard: if EVERY remaining step is auto-handled there is no paired report to fall back to,
  // and hiding every choice would lock the user out of continuous work entirely. Degrade to
  // the unrestricted range rather than blocking the run.
  return new Set(selectable.length > 0 ? selectable : remaining)
})
function autoHandledIdx(idx: number): boolean {
  return idx >= headIdx.value && !selectableIdxSet.value.has(idx)
}

// 0451 T0007: head/done/auto used to draw as up to two separate
// `<span>`s on the same row (a head row that is also auto-handled got both), so the number of
// badges — and the row's right edge — shifted depending on state. One value now: auto wins over
// head (the row is not "current" in any AI-worker sense once the server owns it), otherwise
// head/done/nothing exactly as before.
function stateTag(idx: number): { text: string; kind: 'head' | 'done' | 'auto' } | null {
  if (autoHandledIdx(idx)) return { text: t('main.continuous_work.auto_step_tag'), kind: 'auto' }
  if (idx === headIdx.value) return { text: t('main.continuous_work.head_tag'), kind: 'head' }
  if (idx < headIdx.value) return { text: t('main.continuous_work.done_tag'), kind: 'done' }
  return null
}

// 0352 T0004 §3.7: which rows show the checkbox itself — candidate type, not yet excluded by
// TYPE (auto_approved already dims those rows entirely; the checkbox is for the ai_direct
// per-step choice only, so a row auto_approved already forced out never doubles up on both).
const autoApproveCandidateTypeSet = computed(
  () => new Set((props.autoApproveCandidateTypes ?? []).map(s => s.toUpperCase())),
)
const autoApproveSelectedSet = computed(() => new Set(props.autoApproveSelected ?? []))
function autoApproveCandidateIdx(idx: number): boolean {
  if (autoApproveCandidateTypeSet.value.size === 0) return false
  const item = items.value[idx]
  if (!item) return false
  if (autoHandledTypeSet.value.has(String(item.type ?? '').toUpperCase())) return false
  return autoApproveCandidateTypeSet.value.has(String(item.type ?? '').toUpperCase())
}
function onAutoApproveToggle(itemSeq: number, checked: boolean) {
  emit('toggle-auto-approve', itemSeq, checked)
}
/** Last step the user may stop at — the natural "run the whole remaining sequence" default. */
function lastSelectableIdx(): number {
  for (let i = items.value.length - 1; i >= headIdx.value; i -= 1) {
    if (!selectableIdxSet.value.has(i)) continue
    // 0388 NR0003: a sequence ending in TSR would otherwise default the target all the way to
    // the report itself. TS/TSR are a paired unit (AUTO_REPORT_MAP) — stop the auto-selection
    // at TS instead, one step short of the report. The user can still click TSR to extend the
    // target; it stays in selectableIdxSet, only the DEFAULT pulls back.
    if (items.value[i].type === 'TSR' && i > headIdx.value && items.value[i - 1].type === 'TS') {
      return i - 1
    }
    return i
  }
  return -1
}
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
  if (idx < headIdx.value || !selectableIdxSet.value.has(idx)) return
  selectedIdx.value = idx
}

// 0337 R0001-1: the caller can flip the instruction mode while the dialog is open, which
// changes which steps are selectable under the user's feet. Re-point a now-unselectable target
// at the step that actually ends that logical unit — the paired report right after it — rather
// than silently keeping a boundary the run can no longer honour.
function normalizeSelection() {
  if (selectedIdx.value < 0 || items.value.length === 0) return
  if (selectedIdx.value >= headIdx.value && selectableIdxSet.value.has(selectedIdx.value)) return
  for (let i = selectedIdx.value + 1; i < items.value.length; i += 1) {
    if (selectableIdxSet.value.has(i)) {
      selectedIdx.value = i
      return
    }
  }
  selectedIdx.value = lastSelectableIdx()
}

watch(selectableIdxSet, normalizeSelection)

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
    // 0406 T0013: the query-form endpoint has ONE canonical decision_routes handler and it
    // answers `items`. The `sequence` fallback died with the duplicate route it came from —
    // keeping it would let a fixture drift back to a shape production can no longer return,
    // which is how B0001 stayed invisible to the suite (NR0003 Q7).
    items.value = (data?.items ?? []) as WorkflowStepItem[]
    if (data?.decided === false || items.value.length === 0) {
      // R0001 "워크플로 결정부터": the workflow has not been decided yet — the canonical
      // handler answers 400 sequence_not_decided (caught below), and a decided-but-emptied
      // sequence lands here with zero rows. Neither may block with "워크플로 단계가
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
      // the sequence can just confirm and get the natural result (NR0003 recommendation 1). 0337 R0001-1:
      // the last SELECTABLE step — a trailing auto-handled instruction is not a stop point.
      const presetIdx = props.initialTargetSeq == null
        ? -1
        : items.value.findIndex(item => item.item_seq === props.initialTargetSeq)
      selectedIdx.value = presetIdx >= headIdx.value && selectableIdxSet.value.has(presetIdx)
        ? presetIdx
        : lastSelectableIdx()
      // Bring the current execution point into view instead of leaving the user parked on the
      // completed steps at the top (R0001).
      void revealActiveStep()
    }
  } catch (e: any) {
    // The canonical decision_routes handler reports an undecided sequence as this 400.
    // Preserve the existing "start from workflow decision" state for that contract.
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
  () => props.initialTargetSeq,
  (targetSeq) => {
    if (targetSeq == null || items.value.length === 0) return
    const idx = items.value.findIndex(item => item.item_seq === targetSeq)
    if (idx >= headIdx.value && selectableIdxSet.value.has(idx)) selectedIdx.value = idx
  },
)

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
  /* 0451 T0007 rev1 (rejection 3 — 지시서 작성모드에서 N/T 칸의 텍스트가 우측으로 뚫고 나가지
     않게 한다): a flex item's min-width defaults to auto, so the overflow/ellipsis below could
     never shrink this label past its own content width — a long N/T label widened the row and
     pushed its right end out of the list instead of being clipped. This line is the actual fix;
     it has been missing since the label was introduced. */
  min-width: 0;
  font-size: .82rem;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
/* 0451 T0007 rev1: back to the pre-T0007 state-badge spec — no border, .6rem, pill radius.
   The one-spec-for-two-badges rule is gone with the provider badge it was written for. */
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
/* 0337 R0001-1: a server-auto-handled step is not dimmed like a completed one — it still runs
   in this chain. It only reads as "not yours to choose": default cursor, no hover. */
.wsp-step-tag--auto { background: var(--surface-h); color: var(--text-m); }
.wsp-step--auto { cursor: default; }
.wsp-step--auto .wsp-step-label { color: var(--text-m); }
/* 0352 T0004 §3.7 / R rev1-2: dedicated class for the per-item_seq auto-approve control — a
   new toolbar/inline control must not reuse an existing shared class (two prior regressions:
   findAll(...).length / first-match assertions silently shifted when a new control quietly
   folded into a shared selector).
   rev1: rejected as "too small to hit reliably" — fixed by drawing a bigger checkbox box with
   a wider hit halo, but the widget was still a bare checkbox square with no caption.
   rev2 (rejected a second time, "체크박스만 덜렁 나오면 뭔지 알아보기 힘들다" / "다른 형태의 방식은
   없냐"): resizing wasn't the actual ask — a checkbox alone doesn't say what it does. Redrawn
   as a labelled switch: a track+thumb shape (not a checkbox square) plus a visible text
   caption, so the row reads "auto-approve: on/off" at a glance instead of an unexplained box.
   The `<input type="checkbox">` element and its `checked`/`change` contract are unchanged —
   only visually hidden and reskinned via sibling selectors — so `.wsp-step-auto-toggle input`
   + `.checked`/`change` still work exactly as the existing 21 client specs expect. */
.wsp-step-auto-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-shrink: 0;
  cursor: pointer;
  padding: 6px;
  margin: -6px;
  border-radius: var(--r-sm);
}
.wsp-step-auto-toggle:hover {
  background: var(--surface-h);
}
.wsp-step-auto-toggle-switch {
  position: relative;
  display: inline-flex;
  align-items: center;
  flex-shrink: 0;
  width: 32px;
  height: 18px;
}
.wsp-step-auto-toggle input {
  /* Visually hidden, not display:none — stays a real, focusable, native checkbox so the
     label-click-forwards-to-input browser behavior and the checked/change contract both
     keep working; only its own box is invisible, the track+thumb below draw the visible
     shape instead. */
  position: absolute;
  inset: 0;
  margin: 0;
  width: 100%;
  height: 100%;
  opacity: 0;
  cursor: pointer;
}
.wsp-step-auto-toggle-track {
  position: absolute;
  inset: 0;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--surface);
  transition: background var(--tr), border-color var(--tr);
}
.wsp-step-auto-toggle input:checked ~ .wsp-step-auto-toggle-track {
  background: var(--primary);
  border-color: var(--primary);
}
.wsp-step-auto-toggle-thumb {
  position: absolute;
  top: 1px;
  left: 1px;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 2px rgba(0, 0, 0, .3);
  transition: transform var(--tr);
}
.wsp-step-auto-toggle input:checked ~ .wsp-step-auto-toggle-track .wsp-step-auto-toggle-thumb {
  transform: translateX(14px);
}
.wsp-step-auto-toggle input:focus-visible ~ .wsp-step-auto-toggle-track {
  outline: 2px solid var(--primary);
  outline-offset: 2px;
}
.wsp-step-auto-toggle-text {
  font-size: .68rem;
  font-weight: 600;
  color: var(--text-m);
  white-space: nowrap;
  flex-shrink: 0;
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
