<template>
  <section class="inspector-test-spec" data-test="inspector-test-spec" :data-status="card?.test.status.value" :data-form="form">
    <InspectorPaneState v-if="card?.test.status.value === 'loading' && !view" state="loading" />
    <InspectorPaneState v-if="card?.test.errorKey.value" state="error" :message="t(k(card.test.errorKey.value))" retryable @retry="card?.reload()" />
    <div class="test-card-lines" data-test="inspector-slot-list">
      <InspectorSlotRow v-for="line in lines" :key="line.key" :name="t(k(`slot.${line.key}`))" :id="line.id" :class="{ 'is-empty': line.blank }" :focus="line.key === 'report' && (stage.stage === 5 || card?.reportRecentlyConfirmed.value)" :data-slot="line.key" :data-doc="line.id" :openable="canOpen(line.kind)" @open="openWindow(line.kind)">
        <template #summary>
          <InspectorStatusChip :label="line.label" :tone="line.tone" :data-test="line.key === 'report' ? 'test-spec-state' : `test-card-${line.key}-state`" :data-state="line.key === 'report' ? runState.key : undefined" />
          <span class="test-card-summary" :data-test="`test-card-${line.key}-summary`" :title="line.summary">{{ line.summary }}</span>
          <span v-if="line.key === 'report' && stage.resultState.key === 'unchecked'" class="test-card-summary">{{ t(k('stage.unchecked')) }}</span>
          <span v-if="line.key === 'report' && view?.gate?.applies && !view.gate.passed" class="test-card-danger" data-test="test-spec-gate">{{ t(k('card.gate_blocked')) }}</span>
        </template>
        <template v-if="line.id" #io><DocumentIoGroup :doc-id="line.id" :project-id="target.projectId" :group-id="target.flowId" /></template>
        <template v-if="line.key === 'instruction'">
          <Button size="sm" variant="secondary" :disabled="!canOpen('brief')" :title="openReason('brief')" data-test="test-card-brief" @click="openWindow('brief')">{{ t(k(line.blank ? 'card.write' : 'card.edit')) }}</Button>
        </template>
        <template v-if="line.key === 'scenario'">
          <Dropdown v-if="stage.hasScenario">
            <template #trigger="{ toggle, triggerAttrs }"><Button size="sm" variant="secondary" v-bind="triggerAttrs" data-test="test-card-review-history" @click="toggle">{{ t(k('card.reviews'), { count: reviewCount }) }} ▾</Button></template>
            <div class="test-card-history">
              <p v-if="!reviewCount">{{ t(k('history.empty')) }}</p>
              <p v-for="review in card?.reviewHistory.data.value?.reviews ?? []" :key="review.id">{{ review.verdict }} · {{ review.reviewerName }} · {{ review.at }} · {{ review.comment }}</p>
              <p v-for="rejection in card?.reviewHistory.data.value?.rejections ?? []" :key="rejection.id">{{ t(k('card.rejected')) }} · {{ rejection.rejectionProviderName ?? rejection.rejectedBy }} · {{ rejection.rejectedAt }} · {{ rejection.reason }}</p>
              <Button v-if="line.id" size="sm" variant="ghost" @click="openDocumentHistory(line.id)">{{ t(k('card.history_all')) }}</Button>
            </div>
          </Dropdown>
        </template>
        <template v-if="line.key === 'report'">
          <Button v-if="form !== 'legacy' && !card?.test.availability.value.cancel.visible" size="sm" variant="primary" :disabled="!runAvailability.enabled" :loading="card?.test.busy.value === 'run'" :title="shownReason ? t(k(shownReason)) : t(k('run_hint'))" data-test="test-spec-run" @click="card?.run()">{{ t(k('card.run')) }}</Button>
          <Button v-if="form !== 'legacy' && card?.test.availability.value.cancel.visible" size="sm" variant="secondary" :disabled="!card.test.availability.value.cancel.enabled" :loading="card.test.busy.value === 'cancel'" :title="shownReason ? t(k(shownReason)) : undefined" data-test="test-spec-cancel" @click="card?.cancel()">{{ t(k('card.cancel')) }}</Button>
          <Dropdown v-if="card?.test.history.value.length">
            <template #trigger="{ toggle, triggerAttrs }"><Button size="sm" variant="secondary" v-bind="triggerAttrs" data-test="test-card-run-history" @click="toggle">{{ t(k('card.run_number'), { n: card.test.history.value.length }) }} ▾</Button></template>
            <ul class="test-card-history">
              <li v-for="(row, index) in card.test.history.value" :key="row.runId" data-test="test-spec-history-row" :data-run="row.runId">
                #{{ card.test.history.value.length - index }} · {{ t(k(row.statusKey)) }} · {{ row.at }}
                <span v-if="row.counts">{{ t(k('counts'), { ...row.counts }) }}</span>
                <span v-if="row.verdictKey">{{ t(k(row.verdictKey)) }}</span>
              </li>
            </ul>
          </Dropdown>
          <Button v-if="stage.canFollow" size="sm" variant="secondary" :disabled="!canOpen('follow')" :title="openReason('follow')" data-test="test-card-follow" @click="openWindow('follow')">{{ t(k('card.follow')) }}</Button>
        </template>
        <InspectorOpenButton v-if="line.key !== 'instruction'" :disabled="!canOpen(line.kind)" :title="openReason(line.kind)" :data-test="line.key === 'report' ? 'test-spec-open' : 'test-card-scenario-open'" @click="openWindow(line.kind)" />
      </InspectorSlotRow>
    </div>
    <InspectorReason v-if="shownReason && form !== 'legacy'" :text="t(k(shownReason))" data-test="test-spec-reason" />
    <InspectorReason v-if="card?.test.failure.value" tone="danger" :text="t(k(card.test.failure.value.key))" data-test="test-spec-failure" />
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import Dropdown from '../../../../../components/ui/Dropdown.vue'
import InspectorSlotRow from '../../parts/InspectorSlotRow.vue'
import InspectorStatusChip from '../../parts/InspectorStatusChip.vue'
import InspectorOpenButton from '../../parts/InspectorOpenButton.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { DocumentIoGroup, openDocumentHistory } from '../../openDocument'
import { type TestCardWindowKind } from './testCardSession'
import { testCardPresentation, testSpecLiveRun, type TestSpecSlotTone } from './testSpecModel'
import { useTestCard } from './useTestCard'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.${key}`
const card = useTestCard(() => props.target)
const view = computed(() => card.value?.view.value ?? null)
const stage = computed(() => card.value?.presentation.value ?? testCardPresentation(null))
const form = computed(() => card.value?.test.form.value ?? 'none')
const runState = computed(() => card.value?.test.runState.value ?? stage.value.resultState)
const runAvailability = computed(() => card.value?.can('run') ?? { enabled: false, reason: 'reason.busy' })
const shownReason = computed(() => card.value?.test.availability.value.cancel.visible ? card.value.test.availability.value.cancel.reason : runAvailability.value.reason)
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
interface Line { key: 'instruction' | 'scenario' | 'report'; kind: TestCardWindowKind; id: string | null; blank: boolean; label: string; tone: TestSpecSlotTone; summary: string }
const lines = computed<Line[]>(() => {
  const v = view.value, state = stage.value, writing = card.value?.writing.value
  const own = (slot: string) => props.target.cardDocuments.find((line) => line.slot === slot)
  const tsId = v?.tsDocId ?? own('scenario')?.docId ?? own('instruction')?.docId ?? (props.target.docType === 'TS' ? props.target.docId : null)
  const reportId = v?.tsrDocId ?? own('report')?.docId ?? null
  const intro = v?.details?.intro?.trim().split('\n')[0] ?? ''
  const tally = card.value?.test.tally.value
  const ignored = v?.cases.filter((c) => !c.required).length ?? 0
  const noCode = card.value?.cases.value.filter((c) => c.executionMode === 'automated' && !c.fields.automationRef).length ?? 0
  const aiChecked = v?.cases.filter((c) => c.executionMode === 'ai').length ?? 0
  const tokens = [t(k('card.case_count'), { count: tally?.total ?? 0 })]
  if (tally?.manual) tokens.push(t(k('card.human_count'), { count: tally.manual }))
  if (aiChecked) tokens.push(t(k('card.ai_count'), { count: aiChecked }))
  if (ignored) tokens.push(t(k('card.ignore_count'), { count: ignored }))
  if (noCode) tokens.push(t(k('card.no_code_count'), { count: noCode }))
  const counts = card.value?.test.counts.value
  const code = card.value?.cases.value.filter((c) => c.executionMode === 'automated' && c.fields.automationRef).map((c) => c.fields.automationRef!) ?? []
  const reportSummary = state.stage === 6 ? t(k('card.confirmed_counts'), { pass: counts?.pass ?? 0, total: v?.cases.length ?? 0 })
    : state.stage >= 5 || state.resultState.key === 'stale' ? counts ? t(k('card.counts'), { ...counts }) : ''
    : state.stage === 4 ? t(k('card.run_counts'), { pass: testSpecLiveRun(v)?.passedCases ?? 0, pending: Math.max(0, runState.value.total - runState.value.done) })
    : state.resultState.error ?? (state.resultState.key === 'cancelled' ? v?.lastExecution?.finishedAt ?? '' : code.length > 1 ? t(k('card.code_summary'), { file: code[0], n: code.length - 1 }) : code[0] ?? '')
  const result: Line[] = [
    { key: 'instruction', kind: 'brief', id: tsId, blank: !intro, label: t(k(intro ? 'card.written' : 'card.absent')), tone: intro ? 'success' : 'neutral', summary: intro || t(k('card.to_ai')) },
    { key: 'scenario', kind: 'scenario', id: tsId, blank: !state.hasScenario, label: t(k(writing ? 'card.writing' : state.hasScenario ? 'card.written' : 'stage.waiting')), tone: writing ? 'running' : state.hasScenario ? 'success' : 'neutral', summary: state.hasScenario ? tokens.join(' · ') : own('scenario')?.title ?? '' },
    { key: 'report', kind: 'report', id: reportId, blank: !state.hasScenario, label: t(k(state.stage <= 2 ? 'empty.after_run' : state.stage === 6 ? 'card.confirmed' : state.stateKey), { count: state.pending, done: runState.value.done, total: runState.value.total }), tone: state.stage === 4 ? 'running' : state.tone, summary: reportSummary },
  ]
  if (card.value?.instructionOmitted.value) { result[0].label = t(k('guard.instruction_omitted')); result[0].tone = 'neutral' }
  return result
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
