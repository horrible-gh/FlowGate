<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg" @click.self="close">
      <div class="modal-box modal-cwd">
        <!-- ── Header ── -->
        <div class="modal-hd">
          <span class="modal-title">
            <i class="fa-solid fa-forward-fast" style="color:var(--primary); margin-right:6px;"></i>
            {{ t('main.continuous_work.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <!-- ── Body ── -->
        <div class="modal-bd cwd-body">
          <p class="cwd-intro">{{ t('main.continuous_work.intro') }}</p>

          <div v-if="loading" class="cwd-state">
            <i class="fa-solid fa-spinner fa-spin"></i> {{ t('common.loading') }}
          </div>
          <div v-else-if="errorKey" class="cwd-state cwd-state--error">
            <i class="fa-solid fa-triangle-exclamation"></i> {{ t(errorKey) }}
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
                  <span class="cwd-step-dot"><i class="fa-solid fa-circle-dot"></i></span>
                  <span class="cwd-step-badge doc-tag c-R">WF</span>
                  <span class="cwd-step-label">{{ t('main.continuous_work.from_decision_step') }}</span>
                  <span class="cwd-step-tag cwd-step-tag--head">{{ t('main.continuous_work.head_tag') }}</span>
                </div>
              </div>
              <div class="cwd-note cwd-note--info">
                <i class="fa-solid fa-circle-info"></i>
                {{ t('main.continuous_work.from_decision_note') }}
              </div>
            </template>
            <template v-else>
              <!-- R0001: pick how far to continue. Steps before the current head are 'done'
                   and disabled (no skipping / no mid-start); the run goes from the head to the
                   checked step inclusive. -->
              <div class="cwd-section-title">{{ t('main.continuous_work.pick_target') }}</div>
              <div class="cwd-steps">
                <button
                  v-for="(item, idx) in items"
                  :key="item.item_seq"
                  type="button"
                  class="cwd-step"
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
                    <i
                      class="fa-solid"
                      :class="idx < headIdx
                        ? 'fa-circle-check'
                        : idx === selectedIdx
                          ? 'fa-circle-dot'
                          : 'fa-circle'"
                    ></i>
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
                <i class="fa-solid fa-circle-exclamation"></i>
                {{ t('main.continuous_work.head_in_progress_note') }}
              </div>
            </template>

            <!-- AI review mode (R0001): pause after the first step for a human review + Q&A
                 instead of auto-chaining all the way. -->
            <label class="cwd-toggle">
              <input v-model="reviewMode" type="checkbox" />
              <span class="cwd-toggle-text">
                <span class="cwd-toggle-title">
                  <i class="fa-solid fa-user-shield"></i> {{ t('main.continuous_work.review_mode_label') }}
                </span>
                <span class="cwd-toggle-desc">{{ t('main.continuous_work.review_mode_desc') }}</span>
              </span>
            </label>

            <div class="cwd-summary">
              {{ fromDecision
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
            <i class="fa-solid fa-arrow-right"></i> {{ t('main.continuous_work.btn_next') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest } from '@shared/api'

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
  'confirm': [payload: { targetSeq: number; targetLabel: string; reviewMode: boolean; stepCount: number; fromDecision: boolean }]
}>()

const { t } = useI18n()

// Run-to-end sentinel: emitted as targetSeq when starting before the workflow is decided.
// The server resolves it to the last item_seq of the freshly-decided sequence.
const TARGET_TO_END = -1

const loading = ref(false)
const errorKey = ref<string | null>(null)
const items = ref<SequenceItem[]>([])
const selectedIdx = ref(-1)
const reviewMode = ref(false)
// R0001 "워크플로 결정부터": set when /workflow/sequence reports the sequence is not decided
// yet. Instead of blocking, the dialog offers to start the run from the workflow decision.
const fromDecision = ref(false)

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

async function loadSequence() {
  loading.value = true
  errorKey.value = null
  items.value = []
  selectedIdx.value = -1
  reviewMode.value = false
  fromDecision.value = false
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
      errorKey.value = 'main.continuous_work.error_all_done'
    } else {
      // Default to running the whole remaining sequence.
      selectedIdx.value = items.value.length - 1
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
