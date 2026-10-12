<template>
  <!-- flowgate.v02.0048 T#13 — the WP summary of the documents tab (D#1 §10.2 T#13 row). A thin container:
       useWorkPlan reads, workPlanModel summarizes.
       flowgate.v02.0051 stage 2 — the v7 작업계획 card (0035 plan.m.*): the 작업계획 (WP) slot line and the
       전개 result line (empty "전개 전": no deploy API), built from the common slot list part.
       flowgate.v02.0053 T#5 (0053 CH) — 작업계획 · 전개 are the card's documents (made when it was placed): both
       open (document icon or name) and ✎; no type code in a name.
       flowgate.v02.0056 F1 (0056 CH §5) — the 0048 details (rev, totals, provider assignment, one row per
       card, "Step 편집은 …") are gone: the plan itself shows them in its document window. Under the lines
       stays what an operator acts on — how many cards the workflow started, and a warning when Steps
       have no AI assigned. The `rev n · Step n` line summary is gone as well. -->
  <section class="inspector-work-plan" data-test="inspector-work-plan" :data-status="status" :data-has-document="hasDocument ? 'true' : 'false'">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="openLine" @edit="edit" @history="historyLine" />

    <div v-if="hasDocument" :key="target.docId ?? undefined" class="inspector-work-plan-status" data-test="work-plan-status">
      <InspectorPaneState v-if="status === 'loading' && !view" state="loading" />
      <InspectorPaneState v-else-if="errorKey && !view" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
      <template v-else-if="view">
        <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
        <span class="inspector-work-plan-progress" data-test="work-plan-progress" :data-started="progress.started" :data-cards="progress.cards">
          {{ progress.cards ? t(k('progress'), { ...progress }) : t(k('no_cards')) }}
        </span>
        <span v-if="view.unassignedSteps" class="inspector-work-plan-warning" role="status" data-test="work-plan-unassigned">
          <span aria-hidden="true">⚠</span>{{ t(k('unassigned'), { count: view.unassignedSteps }) }}
        </span>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument, openDocumentHistory } from '../../openDocument'
import { workPlanSlotFocus, workPlanSlotState } from './workPlanModel'
import { useWorkPlan } from './useWorkPlan'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.workPlan.${key}`

const { view, progress, status, errorKey, reload } = useWorkPlan(() => props.target)

const hasDocument = computed(() => props.target.docId !== null)

/** The document icon (or the name): the line's document window (flowgate.v02.0053 T#5). */
function openLine(line: InspectorSlotView) {
  if (line.id) openDocument(line.id)
}

/** ✎: the line's document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}

/** The history button: the line's 검수·반려 이력 window (flowgate.v02.0056 F1). */
function historyLine(line: InspectorSlotView) {
  if (line.id) openDocumentHistory(line.id)
}

function stateOf(reviewStatus: string | null, docStatus: string | null) {
  const state = workPlanSlotState(reviewStatus, docStatus)
  const stateKey = state ? `gui.designer.inspector.${state.key}` : ''
  return { state, view: state ? { label: te(stateKey) ? t(stateKey) : state.raw, tone: state.tone } : null }
}

/**
 * The v7 lines: the plan document and its deployment record — the card's documents (made when the card
 * was placed, flowgate.v02.0053 T#5), else the bound plan; a line without a document says why.
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
  const ownDeploy = target.cardDocuments.find((line) => line.slot === 'deploy')
  const deployState = ownDeploy ? stateOf(ownDeploy.docReviewStatus, ownDeploy.status) : null
  const deploy: InspectorSlotView = ownDeploy?.docId
    ? {
        key: 'deploy',
        kind: 'result',
        name: t(k('slot.deploy')),
        id: ownDeploy.docId,
        state: deployState?.view ?? null,
        focus: workPlanSlotFocus(deployState?.state ?? null),
        danger: deployState?.state?.tone === 'danger',
        open: 'enabled',
      }
    : { key: 'deploy', kind: 'result', name: t(k('slot.deploy')), empty: true, emptyText: t(k('slot_empty.deploy')) }
  return [plan, deploy]
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

.inspector-work-plan-warning {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: #ffc75d;
  font-size: 11.5px;
  font-weight: 600;
}
</style>
