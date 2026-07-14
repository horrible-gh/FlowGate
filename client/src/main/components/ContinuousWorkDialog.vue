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

          <div v-if="loading" class="cwd-state">
            <AppIcon name="spinner" spin /> {{ t('common.loading') }}
          </div>
          <div v-else-if="errorKey" class="cwd-state cwd-state--error">
            <AppIcon name="warning" /> {{ t(errorKey) }}
          </div>
          <template v-else>
            <!-- R0001 "워크플로 결정부터": when the workflow has not been decided yet, the
                 continuous run starts FROM the workflow-decision step itself. There are no
                 concrete sequence items to pick (they don't exist until the AI decides), so
                 the run goes from the decision through the WHOLE decided sequence. -->
            <template v-if="fromDecision">
              <div class="cwd-section-title">{{ t('main.continuous_work.from_decision_title') }}</div>
              <div class="cwd-steps">
                <div class="cwd-step cwd-step--head cwd-step--target cwd-step--static">
                  <span class="cwd-step-dot"><AppIcon name="radio-button" /></span>
                  <span class="cwd-step-badge doc-tag c-R">WF</span>
                  <span class="cwd-step-label">{{ t('main.continuous_work.from_decision_step') }}</span>
                  <span class="cwd-step-tag cwd-step-tag--head">{{ t('main.continuous_work.head_tag') }}</span>
                </div>
              </div>
              <div class="cwd-note cwd-note--info">
                <AppIcon name="info" />
                {{ t('main.continuous_work.from_decision_note') }}
              </div>
            </template>
            <template v-else>
              <!-- R0001: pick how far to continue. Steps before the current head are 'done'
                   and disabled (no skipping / no mid-start); the run goes from the head to the
                   checked step inclusive. When everything is already done (allDone) the list is
                   read-only and just lets the user review what ran. -->
              <div class="cwd-section-title">
                {{ allDone ? t('main.continuous_work.all_done_title') : t('main.continuous_work.pick_target') }}
              </div>
              <div ref="stepsRef" class="cwd-steps">
                <button
                  v-for="(item, idx) in items"
                  :key="item.item_seq"
                  type="button"
                  class="cwd-step"
                  :data-step-idx="idx"
                  :class="{
                    'cwd-step--done': idx < headIdx,
                    'cwd-step--in-range': idx >= headIdx && idx <= selectedIdx,
                    'cwd-step--target': idx === selectedIdx,
                    'cwd-step--head': idx === headIdx,
                  }"
                  :disabled="idx < headIdx"
                  @click="selectTarget(idx)"
                >
                  <span class="cwd-step-dot">
                    <AppIcon
                      :name="idx < headIdx
                        ? 'check-circle'
                        : idx === selectedIdx
                          ? 'radio-button'
                          : 'circle'"
                    />
                  </span>
                  <span class="cwd-step-badge doc-tag" :class="`c-${item.type}`">{{ item.type }}</span>
                  <span class="cwd-step-label">{{ item.label }}</span>
                  <span v-if="idx === headIdx" class="cwd-step-tag cwd-step-tag--head">
                    {{ t('main.continuous_work.head_tag') }}
                  </span>
                  <span v-else-if="idx < headIdx" class="cwd-step-tag cwd-step-tag--done">
                    {{ t('main.continuous_work.done_tag') }}
                  </span>
                </button>
              </div>

              <div v-if="headInProgress" class="cwd-note cwd-note--warn">
                <AppIcon name="warning-circle" />
                {{ t('main.continuous_work.head_in_progress_note') }}
              </div>
              <div v-if="allDone" class="cwd-note cwd-note--info">
                <AppIcon name="check-circle" />
                {{ t('main.continuous_work.all_done_note') }}
              </div>
            </template>

            <!-- AI review mode (R0001): pause after the first step for a human review + Q&A
                 instead of auto-chaining all the way. Hidden when allDone — there is no step
                 left to run, so the toggle would be meaningless. -->
            <label v-if="!allDone" class="cwd-toggle">
              <input v-model="reviewMode" type="checkbox" />
              <span class="cwd-toggle-text">
                <span class="cwd-toggle-title">
                  <AppIcon name="user-gear" /> {{ t('main.continuous_work.review_mode_label') }}
                </span>
                <span class="cwd-toggle-desc">{{ t('main.continuous_work.review_mode_desc') }}</span>
              </span>
            </label>

            <div v-if="!allDone" class="cwd-mode-group">
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
              {{ allDone
                ? t('main.continuous_work.all_done_summary')
                : fromDecision
                  ? t('main.continuous_work.from_decision_summary')
                  : t('main.continuous_work.summary', { count: stepCount, target: targetLabel }) }}
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
import { computed, nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'

interface SequenceItem {
  id: number
  item_seq: number
  type: string
  label: string
  status: string // pending | in_progress | done
}

const props = defineProps<{
  visible: boolean
  /** Sequence-owning root (R/B) doc id — the dialog reads /workflow/sequence by this id. */
  docRef: string
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  // Proceed to the warning/consent gate with the chosen run parameters.
  // fromDecision = true ⇒ the workflow is not decided yet; the run starts FROM the
  // workflow-decision step and targetSeq is the run-to-end sentinel (-1, R0001 "워크플로 결정부터").
  'confirm': [payload: {
    targetSeq: number
    targetLabel: string
    reviewMode: boolean
    instructionMode: 'auto_approved' | 'ai_direct'
    stepCount: number
    fromDecision: boolean
  }]
}>()

const { t } = useI18n()

// Run-to-end sentinel: emitted as targetSeq when starting before the workflow is decided.
// The server resolves it to the last item_seq of the freshly-decided sequence.
const TARGET_TO_END = -1

const loading = ref(false)
const errorKey = ref<string | null>(null)
const items = ref<SequenceItem[]>([])
// Scroll container for the step list (capped + overflow-y:auto). Used to bring the current
// execution point into view (R0001).
const stepsRef = ref<HTMLElement | null>(null)
const selectedIdx = ref(-1)
const reviewMode = ref(false)
const instructionMode = ref<'auto_approved' | 'ai_direct'>('auto_approved')
// R0001 "워크플로 결정부터": set when /workflow/sequence reports the sequence is not decided
// yet. Instead of blocking, the dialog offers to start the run from the workflow decision.
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
const stepCount = computed(() =>
  selectedIdx.value >= headIdx.value ? selectedIdx.value - headIdx.value + 1 : 0,
)
const targetLabel = computed(() =>
  selectedIdx.value >= 0 && selectedIdx.value < items.value.length
    ? items.value[selectedIdx.value].label
    : '',
)
const canProceed = computed(() => {
  if (loading.value || errorKey.value) return false
  // Pre-decision: the workflow-decision step is always runnable (no target to pick).
  if (fromDecision.value) return true
  return headIdx.value < items.value.length && selectedIdx.value >= headIdx.value
})

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
// steps. offsetTop is the untransformed layout offset relative to `.cwd-steps` (which is
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
  reviewMode.value = false
  instructionMode.value = 'auto_approved'
  fromDecision.value = false
  allDone.value = false
  try {
    const res = await getRequest<any>('/api/v1/workflow/sequence', { doc_id: props.docRef })
    const data = res.data as any
    // The live /workflow/sequence is served by workflow_head_routes (included before the
    // decision_routes handler in routers/main.py, so it shadows it), whose response shape is
    // { decided, sequence, head } — NOT the { items } / 400-on-undecided shape this dialog
    // first targeted. Read `sequence` (fall back to `items` for the shadowed handler), exactly
    // as the sibling WorkflowDecisionModal does (`data?.items ?? data?.sequence`).
    items.value = (data?.sequence ?? data?.items ?? []) as SequenceItem[]
    if (data?.decided === false || items.value.length === 0) {
      // R0001 "워크플로 결정부터": the workflow has not been decided yet. head_routes signals
      // this with 200 + decided:false (NOT a 400), so do NOT block with "워크플로 단계가
      // 없습니다" (error_empty). Start the continuous run from the workflow-decision step and
      // run to the end of the sequence the AI decides; the server self-chains from the decide
      // response.
      fromDecision.value = true
    } else if (headIdx.value >= items.value.length) {
      // R0001 (group 0129) repro flowgate.default.0094.0001-R: every step is done. Do NOT
      // replace the whole dialog with a bare "모든 단계가 이미 완료되었습니다" error that hides
      // the steps — the user opened this to SEE what ran ("들어가보면 전부 완료된것만 보이잖아").
      // Keep the (read-only) step list and scroll to the most recent step. [Next] stays
      // disabled because canProceed is false (no head to continue from).
      allDone.value = true
      void revealActiveStep()
    } else {
      // Default to running the whole remaining sequence.
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

function onProceed() {
  if (!canProceed.value) return
  if (fromDecision.value) {
    // Pre-decision: the workflow-decision step is the head and the run goes to the end of
    // the whole decided sequence. No concrete target item exists yet → run-to-end sentinel.
    emit('confirm', {
      targetSeq: TARGET_TO_END,
      targetLabel: t('main.continuous_work.from_decision_target_label'),
      reviewMode: reviewMode.value,
      instructionMode: instructionMode.value,
      stepCount: 0,
      fromDecision: true,
    })
    return
  }
  const target = items.value[selectedIdx.value]
  emit('confirm', {
    targetSeq: target.item_seq,
    targetLabel: target.label,
    reviewMode: reviewMode.value,
    instructionMode: instructionMode.value,
    stepCount: stepCount.value,
    fromDecision: false,
  })
}

function close() {
  emit('update:visible', false)
}

watch(
  () => props.visible,
  (val) => {
    if (val) void loadSequence()
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
.cwd-state {
  padding: 24px;
  text-align: center;
  font-size: .85rem;
  color: var(--text-m);
}
.cwd-state--error { color: var(--danger); }
.cwd-section-title {
  font-size: .72rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .08em;
  color: var(--text-m);
}
.cwd-steps {
  display: flex;
  flex-direction: column;
  gap: 4px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  padding: 6px;
  /* Anchor for child .cwd-step offsetTop so revealActiveStep() can scroll the running step
     to the top in layout coordinates (immune to the modal's open `scale()` animation). */
  position: relative;
  /* R0001: when a sequence has many steps the list grew unbounded and pushed the
     review-mode toggle / summary / footer out of view. Cap the list and let it scroll
     on its own so those controls stay visible. Short sequences show no scrollbar. */
  max-height: min(280px, 40vh);
  overflow-y: auto;
}
.cwd-step {
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
.cwd-step:hover:not(:disabled):not(.cwd-step--static) { background: var(--surface-h); }
.cwd-step:disabled { cursor: not-allowed; }
.cwd-step--static { cursor: default; }
.cwd-step--done { opacity: .5; }
.cwd-step--in-range { background: var(--primary-l); }
.cwd-step--target { border-color: var(--primary); }
.cwd-step-dot {
  flex-shrink: 0;
  font-size: .8rem;
  color: var(--text-m);
}
.cwd-step--in-range .cwd-step-dot { color: var(--primary); }
.cwd-step--done .cwd-step-dot { color: var(--success, #16a34a); }
.cwd-step-badge {
  font-size: .65rem;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 3px;
  font-family: 'JetBrains Mono', monospace;
  flex-shrink: 0;
}
.cwd-step-label {
  flex: 1;
  font-size: .82rem;
  color: var(--text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cwd-step-tag {
  font-size: .6rem;
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: .04em;
  padding: 1px 6px;
  border-radius: 10px;
  flex-shrink: 0;
}
.cwd-step-tag--head { background: var(--primary-l); color: var(--primary); }
.cwd-step-tag--done { background: var(--surface-h); color: var(--text-m); }
.cwd-note {
  font-size: .78rem;
  line-height: 1.5;
  padding: 8px 10px;
  border-radius: var(--r-sm);
}
.cwd-note--warn {
  background: #fef3c7;
  color: #92600a;
  border: 1px solid #fde68a;
}
.cwd-note--info {
  background: var(--primary-l);
  color: var(--primary);
  border: 1px solid var(--primary);
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
.cwd-summary {
  font-size: .82rem;
  font-weight: 600;
  color: var(--text);
  background: var(--surface-h);
  border-radius: var(--r-sm);
  padding: 8px 10px;
}
</style>
