<template>
  <section class="inspector-test-spec" data-test="inspector-test-spec" :data-status="card?.test.status.value" :data-form="form">
    <InspectorPaneState v-if="card?.test.status.value === 'loading' && !view" state="loading" />
    <InspectorPaneState v-if="card?.test.errorKey.value" state="error" :message="t(k(card.test.errorKey.value))" retryable @retry="card?.reload()" />
    <div class="test-card-lines" data-test="inspector-slot-list">
      <InspectorSlotRow v-for="line in lines" :key="line.key" :name="t(k(`slot.${line.key}`))" :id="line.id" :class="{ 'is-empty': line.blank }" :focus="line.key === 'report' && (stage.stage === 5 || card?.reportRecentlyConfirmed.value)" :data-slot="line.key" :data-doc="line.id" :openable="canOpen(line.kind)" @open="openWindow(line.kind)">
        <template #summary>
          <InspectorStatusChip :label="line.label" :tone="line.tone" :data-test="line.key === 'report' ? 'test-spec-state' : `test-card-${line.key}-state`" :data-state="line.key === 'report' ? runState.key : undefined" />
          <span v-if="line.summary" class="test-card-summary" :data-test="`test-card-${line.key}-summary`" :title="line.summary">{{ line.summary }}</span>
          <span v-if="line.key === 'report' && stage.resultState.key === 'unchecked'" class="test-card-summary">{{ t(k('stage.unchecked')) }}</span>
          <span v-if="line.key === 'report' && view?.gate?.applies && !view.gate.passed" class="test-card-danger" data-test="test-spec-gate">{{ t(k('card.gate_blocked')) }}</span>
        </template>
        <template v-if="line.id" #io><DocumentIoGroup :doc-id="line.id" :project-id="target.projectId" :group-id="target.flowId" /></template>
        <template v-if="line.key === 'instruction'">
          <IconButton :label="t('gui.designer.inspector.slot.edit')" :title="canOpen('brief') ? t('gui.designer.inspector.slot.edit') : openReason('brief')" variant="secondary" size="sm" :disabled="!canOpen('brief')" data-test="test-card-brief" @click="openWindow('brief')">✎</IconButton>
        </template>
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
        <InspectorOpenButton v-if="line.key !== 'instruction'" :disabled="!canOpen(line.kind)" :title="openReason(line.kind)" :data-test="line.key === 'report' ? 'test-spec-open' : 'test-card-scenario-open'" @click="openWindow(line.kind)" />
      </InspectorSlotRow>
    </div>
    <InspectorReason v-if="card?.test.failure.value" tone="danger" :text="t(k(card.test.failure.value.key))" data-test="test-spec-failure" />
  </section>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Dropdown from '../../../../../components/ui/Dropdown.vue'
import IconButton from '../../../../../components/ui/IconButton.vue'
import InspectorAiButton from '../../parts/InspectorAiButton.vue'
import InspectorHistoryButton from '../../parts/InspectorHistoryButton.vue'
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
interface Line { key: 'instruction' | 'scenario' | 'report'; kind: TestCardWindowKind; id: string | null; blank: boolean; label: string; tone: TestSpecSlotTone; summary: string }
const lines = computed<Line[]>(() => {
  const v = view.value, state = stage.value, writing = card.value?.writing.value
  const own = (slot: string) => props.target.cardDocuments.find((line) => line.slot === slot)
  const tsId = v?.tsDocId ?? own('scenario')?.docId ?? own('instruction')?.docId ?? (props.target.docType === 'TS' ? props.target.docId : null)
  const reportId = v?.tsrDocId ?? own('report')?.docId ?? null
  // flowgate.v02.0094: the 지시서 · 시나리오 lines show their state badge only (no instruction text or case tally).
  const intro = v?.details?.intro?.trim() ?? ''
  const counts = card.value?.test.counts.value
  const reportSummary = state.stage === 6 ? t(k('card.confirmed_counts'), { pass: counts?.pass ?? 0, total: v?.cases.length ?? 0 })
    : state.stage >= 5 || state.resultState.key === 'stale' ? counts ? t(k('card.counts'), { ...counts }) : ''
    : state.stage === 4 ? t(k('card.run_counts'), { pass: testSpecLiveRun(v)?.passedCases ?? 0, pending: Math.max(0, runState.value.total - runState.value.done) })
    : state.resultState.error ?? ''
  const result: Line[] = [
    { key: 'instruction', kind: 'brief', id: tsId, blank: !intro, label: t(k(intro ? 'card.written' : 'card.absent')), tone: intro ? 'success' : 'neutral', summary: '' },
    { key: 'scenario', kind: 'scenario', id: tsId, blank: !state.hasScenario, label: t(k(writing ? 'card.writing' : state.hasScenario ? 'card.written' : 'stage.waiting')), tone: writing ? 'running' : state.hasScenario ? 'success' : 'neutral', summary: '' },
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
