<template>
  <section class="inspector-test-spec" data-test="inspector-test-spec" :data-status="card?.test.status.value" :data-form="form">
    <InspectorPaneState v-if="card?.test.status.value === 'loading' && !view" state="loading" />
    <InspectorPaneState v-if="card?.test.errorKey.value" state="error" :message="t(k(card.test.errorKey.value))" retryable @retry="card?.reload()" />
    <div class="test-card-lines" data-test="inspector-slot-list">
      <InspectorSlotRow v-for="line in lines" :key="line.key" :name="t(k(`slot.${line.key}`))" :id="line.id" :decision-layout="decisions.row(line.key, line.id).visible" :class="{ 'is-empty': line.blank }" :focus="line.key === 'report' && (stage.stage === 5 || card?.reportRecentlyConfirmed.value)" :data-slot="line.key" :data-doc="line.id" :openable="canOpen(line.kind)" @open="openWindow(line.kind)">
        <template #summary>
          <InspectorStatusChip :label="decisionLabel(line)" :tone="decisionTone(line)" :data-test="line.key === 'report' ? 'test-spec-state' : `test-card-${line.key}-state`" :data-state="line.key === 'report' ? runState.key : undefined" />
          <!-- F81: a line is its name and its state chip, as on every other card: no text button, no pass tally.
               The pass / fail numbers are in the scenario window only. -->
        </template>
        <template v-if="line.id" #io><DocumentIoGroup :doc-id="line.id" :project-id="target.projectId" :group-id="target.flowId" :card="cardDocumentContext(target, line.id)" /></template>
        <template v-if="line.key === 'scenario'">
          <!-- flowgate.v02.0094: the scenario's AI writing is the line's [AI 호출], as on the other cards' lines: it opens
               the document's AI call window (nothing is written until it runs); the 지시서 window only saves. -->
          <InspectorAiButton v-if="form !== 'legacy' && line.id" :label="t(aiKey)" :title="aiReason ?? t(aiKey)" :state="writing ? 'running' : 'enabled'" :disabled="!writing && !card?.can('rewriteAll').enabled" data-test="test-card-scenario-ai" @click="card?.rewriteAll()" />
          <InspectorHistoryButton v-if="stage.hasScenario && line.id" :count="reviewCount" data-test="test-card-review-history" @click="openDocumentHistory(line.id)" />
        </template>
        <template v-if="line.key === 'report'">
          <IconButton v-if="form !== 'legacy' && !card?.test.availability.value.cancel.visible" :label="t(k('card.run'))" :title="shownReason ? t(k(shownReason)) : t(k('card.run'))" variant="secondary" size="sm" :disabled="!runAvailability.enabled" data-test="test-spec-run" :data-busy="card?.test.busy.value === 'run' ? 'true' : undefined" @click="card?.run()">▶</IconButton>
          <IconButton v-if="form !== 'legacy' && card?.test.availability.value.cancel.visible" :label="t(k('card.cancel'))" :title="shownReason ? t(k(shownReason)) : t(k('card.cancel'))" variant="secondary" size="sm" :disabled="!card.test.availability.value.cancel.enabled" data-test="test-spec-cancel" :data-busy="card.test.busy.value === 'cancel' ? 'true' : undefined" @click="card?.cancel()">■</IconButton>
          <Dropdown v-if="card?.test.history.value.length">
            <template #trigger="{ toggle, triggerAttrs }"><IconButton v-bind="triggerAttrs" :label="t(k('card.run_number'), { n: card.test.history.value.length })" :title="t(k('card.run_number'), { n: card.test.history.value.length })" variant="secondary" size="sm" data-test="test-card-run-history" @click="toggle">↺</IconButton></template>
            <ul class="test-card-history">
              <li v-for="(row, index) in card.test.history.value" :key="row.runId" data-test="test-spec-history-row" :data-run="row.runId">
                #{{ card.test.history.value.length - index }} · {{ t(k(row.statusKey)) }} · {{ row.at }}
                <span v-if="row.counts">{{ t(k('counts'), { ...row.counts }) }}</span>
                <span v-if="row.verdictKey">{{ t(k(row.verdictKey)) }}</span>
              </li>
            </ul>
          </Dropdown>
          <IconButton v-if="stage.canFollow" :label="t(k('card.follow'))" :title="canOpen('follow') ? t(k('card.follow')) : openReason('follow')" variant="secondary" size="sm" :disabled="!canOpen('follow')" data-test="test-card-follow" @click="openWindow('follow')">→</IconButton>
        </template>
        <InspectorHistoryButton v-if="line.key !== 'scenario' && line.id" @click="openDocumentHistory(line.id)" />
        <InspectorOpenButton :disabled="!canOpen(line.kind)" :title="openReason(line.kind)" :data-test="line.key === 'report' ? 'test-spec-open' : 'test-card-scenario-open'" @click="openWindow(line.kind)" />
        <InspectorDocumentDecision v-if="decisions.row(line.key, line.id).visible" :name="t(k(`slot.${line.key}`))"
          :action="decisions.row(line.key, line.id).action" :disabled="decisions.row(line.key, line.id).disabled"
          :reason="decisions.row(line.key, line.id).reason" @click="decisions.decide(line.key, line.id)" />
      </InspectorSlotRow>
    </div>
    <InspectorReason v-if="lines.some(line => line.key === 'report' && line.id) && stage.hasScenario && stage.resultState.key === 'none' && !card?.test.runState.value.active && card?.test.status.value === 'ready'" :text="t(k('missing_case_results'))" data-test="test-spec-missing-results" />
    <InspectorReason v-if="decisions.failure.value" tone="danger" :text="decisions.failure.value" role="alert" data-test="document-decision-failure" />
    <InspectorReason v-if="card?.test.failure.value" tone="danger" :text="t(k(card.test.failure.value.key))" data-test="test-spec-failure" />
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Dropdown from '../../../../../components/ui/Dropdown.vue'
import IconButton from '../../../../../components/ui/IconButton.vue'
import { useCardDocumentDecisions } from '../../openDocument'
import InspectorDocumentDecision from '../../parts/InspectorDocumentDecision.vue'
import InspectorAiButton from '../../parts/InspectorAiButton.vue'
import InspectorHistoryButton from '../../parts/InspectorHistoryButton.vue'
import InspectorSlotRow from '../../parts/InspectorSlotRow.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { cardDocumentContext, DocumentIoGroup, openDocumentHistory } from '../../openDocument'
import { type TestCardWindowKind } from './testCardSession'
import { testCardPresentation, type TestSpecSlotTone } from './testSpecModel'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const decisions = useCardDocumentDecisions(() => props.target)
const k = (key: string) => `gui.inspector.testSpec.${key}`
const card = useTestCard(() => props.target)
const view = computed(() => card.value?.view.value ?? null)
const stage = computed(() => card.value?.presentation.value ?? testCardPresentation(null))
const form = computed(() => card.value?.test.form.value ?? 'none')
const runState = computed(() => card.value?.test.runState.value ?? stage.value.resultState)
const runAvailability = computed(() => card.value?.can('run') ?? { enabled: false, reason: 'reason.busy' })
const shownReason = computed(() => card.value?.test.availability.value.cancel.visible ? card.value.test.availability.value.cancel.reason : runAvailability.value.reason)
const writing = computed(() => card.value?.writing.value === 'initial' || card.value?.writing.value === 'rewrite-all')
const aiKey = computed(() => `gui.designer.inspector.slot.actions.${writing.value ? 'ai_invoke_running' : 'ai_invoke'}`)
const aiReason = computed(() => { const reason = writing.value ? null : card.value?.can('rewriteAll').reason; return reason ? t(k(reason)) : null })
const reviewCount = computed(() => (card.value?.reviewHistory.data.value?.reviews.length ?? 0) + (card.value?.reviewHistory.data.value?.rejections.length ?? 0))
function canOpen(kind: TestCardWindowKind) {
  return !!card.value && card.value.openReason(kind) === null
}
function openReason(kind: TestCardWindowKind) {
  return t(k(canOpen(kind) ? 'open_hint' : card.value?.openReason(kind) ?? 'reason.busy'))
}
function openWindow(kind: TestCardWindowKind) {
  if (!canOpen(kind)) return
  card.value?.openWindow(kind)
}
// F81: two lines only — the scenario and the report. There is no instruction line.
interface Line { key: 'scenario' | 'report'; kind: TestCardWindowKind; id: string | null; blank: boolean; label: string; tone: TestSpecSlotTone }
function decisionLabel(line: Line) {
  const row = decisions.row(line.key, line.id)
  return row.authoritative && row.state ? t(`gui.designer.inspector.review_status.${row.state === 'wf_done' ? 'approved' : row.state}`) : line.label
}
function decisionTone(line: Line): TestSpecSlotTone {
  const row = decisions.row(line.key, line.id)
  if (!row.authoritative) return line.tone
  return row.state === 'approved' || row.state === 'wf_done' ? 'success'
    : row.state === 'rejected' ? 'danger' : row.state === 'pending_review' ? 'warning' : line.tone
}
const lines = computed<Line[]>(() => {
  const v = view.value, state = stage.value, writing = card.value?.writing.value
  const own = (slot: string) => props.target.cardDocuments.find((line) => line.slot === slot)
  // The TS document also holds what was the instruction; its text stays in the document and is not shown.
  const tsId = v?.tsDocId ?? own('scenario')?.docId ?? own('instruction')?.docId ?? (props.target.docType === 'TS' ? props.target.docId : null)
  const reportId = v?.tsrDocId ?? own('report')?.docId ?? null
  return [
    { key: 'scenario', kind: 'scenario', id: tsId, blank: !state.hasScenario, label: t(k(writing ? 'card.writing' : state.hasScenario ? 'card.written' : 'stage.waiting')), tone: writing ? 'running' : state.hasScenario ? 'success' : 'neutral' },
    { key: 'report', kind: 'report', id: reportId, blank: !state.hasScenario, label: t(k(state.stage <= 2 ? 'empty.after_run' : state.stage === 6 ? 'card.confirmed' : state.stateKey), { count: state.pending, done: runState.value.done, total: runState.value.total }), tone: state.stage === 4 ? 'running' : state.tone },
  ]
})
</script>
<style scoped>
.inspector-test-spec, .test-card-lines { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.test-card-summary { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg2-muted); font-size: 11.5px; min-width: 0; }
.test-card-danger { color: var(--fg2-status-danger-fg); }
.test-card-history { padding: 8px; margin: 0; max-width: 560px; max-height: 320px; overflow: auto; list-style: none; color: var(--fg2-text); font-size: 12px; }
.test-card-history li { display: flex; flex-wrap: wrap; gap: 6px; padding: 4px; }
@container (max-width: 420px) { .test-card-summary { display: none; } }
</style>
