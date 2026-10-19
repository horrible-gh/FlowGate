<template>
  <!-- flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §10.2 T#13 row). A thin container:
       useWorkPlan reads, workPlanModel summarizes.
       flowgate.v02.0051 stage 2 — the v7 작업계획 card (0035 plan.m.*): the 작업계획 (WP) slot line and the
       전개 result line (empty "전개 전": no deploy API), built from the common slot list part.
       flowgate.v02.0053 T#5 (0053 CH) — 작업계획 · 전개 are the card's documents (made when it was placed): both
       open (document icon or name); no type code in a name.
       flowgate.v02.0056 F1 (0056 CH §5) — the 0048 details (rev, totals, provider assignment, one row per
       card, "Step 편집은 …") are gone: the plan itself shows them in its document window. Under the lines
       stays what an operator acts on — how many cards the workflow started, and a warning when Steps
       have no AI assigned. The `rev n · Step n` line summary is gone as well.
       flowgate.v02.0072 T#3 — the documents area is the one 「작업계획」 line (the 전개 document line is gone; the header's
       ⇉ 전개 actions stay as they were). Its document icon / name open the plan in the v0.2 work plan edit window
       (features/workPlanWindow through openDocument.ts: quantities, Steps, ✎ → the Step window), not the document
       window; the line's history, [업로드][다운로드], [작성] (no document yet) and [AI 호출] stay the card line's.
       flowgate.v02.0074 T#1 (R §1.1 · §1.3) — one way in: the line has no ✎ (the document icon / name open it), and a
       plan with no cards shows a wordless dashed block where the progress goes (no "카드 없음" text). -->
  <section class="inspector-work-plan" data-test="inspector-work-plan" :data-status="status" :data-has-document="hasDocument ? 'true' : 'false'">
    <InspectorSlotList :slots="lines" @open="openLine" @history="historyLine" @action="actionLine">
      <!-- flowgate.v02.0068 T#1: a document line's [업로드][다운로드]. -->
      <template #io="{ docId }"><DocumentIoGroup :doc-id="docId"  :project-id="target.projectId" :group-id="target.flowId" /></template>
    </InspectorSlotList>

    <div v-if="hasDocument" :key="target.docId ?? undefined" class="inspector-work-plan-status" data-test="work-plan-status">
      <InspectorPaneState v-if="status === 'loading' && !view" state="loading" />
      <InspectorPaneState v-else-if="errorKey && !view" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <template v-else-if="view">
        <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
        <span v-if="progress.cards" class="inspector-work-plan-progress" data-test="work-plan-progress" :data-started="progress.started" :data-cards="progress.cards">
          {{ t(k('progress'), { ...progress }) }}
        </span>
        <span v-else class="inspector-work-plan-progress is-empty" data-test="work-plan-progress" :data-started="progress.started" data-cards="0" aria-hidden="true"></span>
        <span v-if="view.unassignedSteps" class="inspector-work-plan-warning" role="status" data-test="work-plan-unassigned">
          <span aria-hidden="true">⚠</span>{{ t(k('unassigned'), { count: view.unassignedSteps }) }}
        </span>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, inject } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorSlotList, { type InspectorSlotView, type SlotActionKey } from '../../parts/InspectorSlotList.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { cardLineRow, DocumentIoGroup, openCardLineAction, openDocumentHistory, openWorkPlan } from '../../openDocument'
import { workPlanSlotFocus, workPlanSlotState } from './workPlanModel'
import { useWorkPlan } from './useWorkPlan'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const host = inject(INSPECTOR_HOST, null)

const k = (key: string) => `gui.inspector.workPlan.${key}`

const { view, progress, status, errorKey, reload } = useWorkPlan(() => props.target)

const hasDocument = computed(() => props.target.docId !== null)

/**
 * The document icon (or the name): flowgate.v02.0072 T#3 — the plan's work plan edit window (read only there when the
 * Flow is: ownership conflict, revision view, its run lock).
 */
function openLine(line: InspectorSlotView) {
  if (line.id) openWorkPlan(props.target, line.id, host)
}

/** The history button: the line's 검수·반려 이력 window (flowgate.v02.0056 F1). */
function historyLine(line: InspectorSlotView) {
  if (line.id) openDocumentHistory(line.id)
}

/**
 * flowgate.v02.0056 F2-2: a row action of a card line — [작성] opens the line's write window (a plan line is made as an
 * empty plan), [AI 호출] the document's AI call window (openCardLineAction; the Canvas is read again after them).
 */
function actionLine(line: InspectorSlotView, key: SlotActionKey) {
  openCardLineAction(props.target, line, key, host)
}

function stateOf(reviewStatus: string | null, docStatus: string | null) {
  const state = workPlanSlotState(reviewStatus, docStatus)
  const stateKey = state ? `gui.designer.inspector.${state.key}` : ''
  return { state, view: state ? { label: te(stateKey) ? t(stateKey) : state.raw, tone: state.tone } : null }
}

/**
 * The plan line: the card's plan document (made when the card was placed, flowgate.v02.0053 T#5), else the bound
 * plan; a line without a document says why. flowgate.v02.0072 T#3: the one document line (R0001 §1.2, 0005-NR §1 —
 * the 전개 line is not shown).
 */
const lines = computed<InspectorSlotView[]>(() => {
  const target = props.target
  const ownPlan = target.cardDocuments.find((line) => line.slot === 'plan')
  const planId = ownPlan ? ownPlan.docId : target.docId
  const planState = ownPlan ? stateOf(ownPlan.docReviewStatus, ownPlan.status) : stateOf(target.docReviewStatus, target.docStatus)
  const plan: InspectorSlotView =
    planId !== null
      ? {
          key: 'plan',
          name: t(k('slot.plan')),
          id: planId,
          state: planState.view,
          focus: workPlanSlotFocus(planState.state),
          danger: planState.state?.tone === 'danger',
          open: 'enabled',
        }
      : { key: 'plan', name: t(k('slot.plan')), empty: true }
  // flowgate.v02.0056 F2-2: 문서 없음 [작성] · the history number · [AI 호출] of the card's own line (a WP / M line is
  // never "비어 있음" — O3 §4-1 has_body is always true there).
  return [{ ...plan, ...cardLineRow(target, plan, 'plan') }]
})
</script>

<style scoped>
.inspector-work-plan {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 12px;
}

/* One short line under the slots: the progress, then the warning when Steps have no AI. */
.inspector-work-plan-status {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px 14px;
  min-width: 0;
  padding: 0 2px;
}

.inspector-work-plan-progress {
  color: var(--fg2-muted);
  font-size: 11.5px;
}

/* flowgate.v02.0074 T#1 (R §1.1): no cards yet — a wordless dashed block (the .inspector-pane-disabled dashed token). */
.inspector-work-plan-progress.is-empty {
  flex: 1 1 100%;
  min-height: 18px;
  border: 1px dashed #2b4562;
  border-radius: 8px;
}

.inspector-work-plan-warning {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #ffc75d;
  font-size: 11.5px;
  font-weight: 600;
}
</style>
