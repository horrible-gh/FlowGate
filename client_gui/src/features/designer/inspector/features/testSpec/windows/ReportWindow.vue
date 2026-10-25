<template>
  <ModelessShell v-if="card && state && mode" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 120)" :initial-y="geometry('y', 64)" :initial-width="geometry('width', 1080)" :initial-height="geometry('height', 680)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header>{{ t(k('title')) }} · {{ target.title }} <small data-test="report-tag" :data-mode="mode">{{ tag }}</small></template>
    <template #actions>
      <span class="window-actions" @mousedown.stop>
        <Dropdown><template #trigger="{ toggle, triggerAttrs }"><Button size="sm" variant="secondary" v-bind="triggerAttrs" data-test="report-runs" @click="toggle">{{ runLabel }} ▾</Button></template>
          <ul class="runs">
            <li v-if="!card.test.history.value.length">{{ t(k('runs_empty')) }}</li>
            <li v-for="row in card.test.history.value" :key="row.runId" :class="{ current: row.runId === resultRun?.runId }" data-test="report-run-row">
              #{{ runNumber(row.runId) }} · {{ t(`gui.inspector.testSpec.${row.statusKey}`) }} · {{ documentHistoryWhen(row.at) }}<template v-if="row.verdictKey"> · {{ t(`gui.inspector.testSpec.${row.verdictKey}`) }}</template>
            </li>
          </ul>
        </Dropdown>
        <Button v-if="mode === 'complete'" size="sm" :disabled="!card.canOpen('follow')" :title="followReason" data-test="report-follow" @click="card.openWindow('follow')">{{ t(k('follow')) }}</Button>
      </span>
    </template>
    <div ref="body" class="report" data-test="report-window" :data-mode="mode" @keydown.esc="onEscape">
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <h3>① {{ t(k('result')) }} <small data-test="report-summary">{{ resultSummary }}<template v-if="card.presentation.value.resultState.key === 'unchecked'"> · {{ t(k('unchecked')) }}</template></small></h3>
      <div class="sum">
        <div class="big" :class="{ attn: mode === 'confirm' }" data-test="report-big">
          <b>{{ big.value }}<small v-if="big.total !== null">/{{ big.total }}</small></b><span>{{ t(k(big.label)) }}</span>
          <span v-if="mode !== 'running' && defectCount" class="defects">{{ t(k('defects'), { n: defectCount }) }}</span>
        </div>
        <div>
          <div class="bar" data-test="report-bar">
            <button v-for="item in items" :key="item.caseId" type="button" class="cell" :class="{ sel: item.caseId === selectedId, pend: item.pending }" :data-state="item.retrying ? 'running' : item.state"
              :title="`${item.caseId} · ${item.title} · ${t(k(`state_${item.retrying ? 'running' : item.state}`))}`" :aria-label="t(k('select'), { id: item.caseId })" data-test="report-cell" :data-case="item.caseId" @click="pick(item.caseId)" />
          </div>
          <div class="legend" data-test="report-legend"><span v-for="entry in legend" :key="entry.state"><i :data-state="entry.state" />{{ t(k(`state_${entry.state}`)) }} <b>{{ entry.n }}</b></span></div>
        </div>
        <span v-if="mode !== 'running'" class="gate" :class="gate.length ? 'stop' : 'go'" data-test="report-gate">
          <template v-if="gate.length">{{ t(k('gate_stop')) }} <button v-for="item in gate" :key="item.caseId" type="button" class="gate-id" data-test="report-gate-case" @click="pick(item.caseId)">{{ item.caseId }}</button></template>
          <template v-else>{{ t(k('gate_go')) }}</template>
        </span>
      </div>
      <h3>② {{ t(k('cases')) }}
        <InspectorSegment v-if="filterOptions.length" class="filter" :model-value="filter" :options="filterOptions" :aria-label="t(k('cases'))" data-test="report-filter" @update:model-value="setFilter($event as ReportFilter)" />
        <small v-else>{{ t(k('count'), { n: items.length }) }}</small>
      </h3>
      <p v-if="mode === 'previous'" class="note" data-test="report-read-only">{{ t(k('read_only')) }}</p>
      <div class="split">
        <ul class="list" role="listbox" :aria-label="t(k('cases'))" data-test="report-list">
          <li v-for="item in visible" :key="item.caseId">
            <button type="button" class="row" data-report-row role="option" :aria-selected="item.caseId === selectedId" :class="{ on: item.caseId === selectedId, pend: item.pending }" :data-case="item.caseId" data-test="report-row" @click="pick(item.caseId)">
              <span class="glyph"><TestMarkBadge v-if="markOf(item)" :mark="markOf(item)!" /></span><span class="id">{{ item.caseId }}</span><span class="title">{{ item.title }}</span>
              <span v-if="item.retrying" class="right"><TestMarkBadge :mark="TEST_PENDING_MARK" /></span>
              <span v-else-if="item.pending" class="right dot" :aria-label="t(k('filter_pending'))" />
              <span v-else-if="item.decision" class="right label" :data-cause="item.decision.cause">{{ decisionLabel(item) }}</span>
            </button>
          </li>
        </ul>
        <section v-if="selected" class="detail" data-test="report-detail" :data-case="selected.caseId">
          <header><TestMarkBadge v-if="markOf(selected)" :mark="markOf(selected)!" /><span class="id">{{ selected.caseId }}</span><b>{{ selected.title }}</b>
            <span v-if="selected.executionMode && selected.executionMode !== 'automated'" class="tag">{{ t(k(selected.executionMode === 'manual' ? 'mode_manual' : selected.executionMode === 'ai' ? 'mode_ai' : 'mode_external')) }}</span>
            <span v-if="!selected.required" class="tag">{{ t(k('ignore')) }}</span><span class="ref">{{ selected.requirement }}</span></header>
          <div class="cmp"><div><small>{{ t(k('expected')) }}</small><p>{{ selected.expected ?? '—' }}</p></div>
            <div :data-state="selected.state" data-test="report-actual"><small>{{ t(k('actual')) }}</small><p :class="{ mute: !selected.actual }">{{ actualText(selected) }}</p></div></div>
          <div v-if="decisionShown" class="decision" :class="{ done: !!selected.decision }" data-test="report-decision">
            <template v-if="selected.retrying"><div class="line"><TestMarkBadge :mark="TEST_PENDING_MARK" /><span class="note">{{ t(k('retrying')) }}</span></div></template>
            <template v-else-if="selected.decision">
              <div class="line"><span class="label" :data-cause="selected.decision.cause">{{ decisionLabel(selected) }}</span><span class="note">{{ selected.decision.reason }}</span>
                <Button v-if="selected.decision.cause === 'scen'" size="sm" variant="ghost" :disabled="!card.canOpen('case')" data-test="report-edit-case" @click="card.openWindow('case', selected.caseId)">{{ t(k('edit_case')) }}</Button>
                <span class="meta">{{ [selected.decision.by, documentHistoryWhen(selected.decision.at)].filter(Boolean).join(' · ') }}</span></div>
            </template>
            <template v-else>
              <p v-if="selected.retryOutcome" class="retry" data-test="report-retry-outcome">{{ [t(k(`retry_${selected.retryOutcome}`)), selected.retryError].filter(Boolean).join(' · ') }}</p>
              <div v-if="selected.manualCheck" class="line"><span class="who">{{ t(k('manual')) }}</span><span class="note" />
                <span class="acts">
                  <Button size="sm" variant="secondary" class="ok" :disabled="!entry.enabled" :title="entryReason" data-test="report-manual-pass" @click="enter('PASS')">{{ t(k('pass')) }}</Button>
                  <Button size="sm" variant="danger" :disabled="!entry.enabled" :title="entryReason" data-test="report-manual-fail" @click="enter('FAIL')">{{ t(k('fail')) }}</Button>
                </span></div>
              <div v-else-if="causeMode === 'human'" class="line"><span class="who">{{ t(k('cause_title')) }}</span><span class="note" />
                <InspectorSegment :model-value="draft.cause" :options="causeOptions" :aria-label="t(k('cause_title'))" data-test="report-causes" @update:model-value="choose($event as ReportCause)" /></div>
              <div v-else class="line"><span class="note" />
                <span class="acts">
                  <Dropdown><template #trigger="{ toggle, triggerAttrs }"><Button size="sm" variant="ghost" v-bind="triggerAttrs" data-test="report-other" @click="toggle">{{ t(k('other')) }}</Button></template>
                    <template #default="{ close: closeMenu }"><div class="causes">
                      <button v-for="option in causeOptions" :key="option.value" type="button" role="menuitem" class="cause" :disabled="option.disabled" :title="option.title ?? undefined" :data-cause="option.value" data-test="report-cause" @click="closeMenu(); choose(option.value as ReportCause)">
                        <b>{{ option.label }}</b><small>{{ t(k(`cause_${option.value}_to`)) }}</small></button>
                    </div></template>
                  </Dropdown>
                </span></div>
              <InspectorReason v-if="areaReason" :text="areaReason" data-test="report-decision-reason" />
            </template>
          </div>
          <dl class="facts">
            <template v-if="selected.executionMode === 'automated'"><dt>{{ t(k('command')) }}</dt><dd class="mono" data-test="report-command">{{ selected.runCommand ?? '—' }}</dd></template>
            <dt>{{ t(k('evidence')) }}</dt><dd><span v-for="(item, i) in selected.evidence" :key="i" class="evidence" data-test="report-evidence">▤ {{ item.label ?? item.value }}</span><span v-if="!selected.evidence.length" class="mute">—</span></dd>
            <dt>{{ t(k('previous')) }}</dt><dd :class="{ mute: !previousRun }">{{ previousRun ?? '—' }}</dd>
          </dl>
        </section>
      </div>
      <InspectorReason v-if="refusal" tone="danger" :text="refusal" data-test="report-refusal" />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import { TestMarkBadge, testVerdictMark, TEST_PENDING_MARK, documentHistoryWhen } from '@/features/documentWindow'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import InspectorSegment from '../../../parts/InspectorSegment.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import type { TestCardCommand } from '../useTestCard'
import { testSpecVerdictKey } from '../testSpecModel'
import { sessionDraft, useScenarioWindow } from './scenarioWindowModel'
import { reportDefaultFilter, reportDefects, reportGate, reportItems, reportKeyAction, reportLegend, reportMode, reportMove,
  reportNextPending, reportPrimary, reportRerunOutcome, reportRunNumber, reportSelection, reportTagKey, reportVisible,
  type ReportCause, type ReportDecision, type ReportDraft, type ReportFilter, type ReportItem } from './reportWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.report.${key}`
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'report')
const body = ref<HTMLElement | null>(null)
// Q-8: the server keeps no case decision record yet, so every confirmation target stays undecided.
const decisions: readonly ReportDecision[] = []
const draft = computed(() => sessionDraft<ReportDraft>(card.value!.drafts, 't3:report', () => ({ caseId: null, filter: null, filterMode: null, passFor: null, reason: '', cause: null })))
const mode = computed(() => card.value ? reportMode(card.value.presentation.value) : null)
const items = computed(() => card.value ? reportItems(card.value.view.value, card.value.cases.value, card.value.rows.value, card.value.presentation.value, decisions) : [])
const filter = computed<ReportFilter>(() => mode.value && draft.value.filter && draft.value.filterMode === mode.value ? draft.value.filter : reportDefaultFilter(mode.value ?? 'previous', items.value))
const visible = computed(() => reportVisible(items.value, mode.value ?? 'previous', filter.value))
const selectedId = computed(() => reportSelection(visible.value, draft.value.caseId))
const selected = computed(() => items.value.find((item) => item.caseId === selectedId.value) ?? null)
const causeMode = computed(() => {
  const settings = card.value?.settings
  const value = settings?.pending.value.result_cause ?? settings?.node.value?.fields.result_cause?.value
  return value === 'human' ? 'human' : 'ai'
})
const gate = computed(() => reportGate(items.value))
const defectCount = computed(() => reportDefects(items.value).defects.length)
const runCount = computed(() => card.value?.view.value?.history.length ?? 0)
const live = computed(() => card.value?.view.value?.activeRun ?? card.value?.view.value?.lastExecution ?? null)
const resultRun = computed(() => mode.value === 'running' ? live.value : card.value?.view.value?.latest ?? null)
/** The shown result's own number in the run list, so the title, the result head and the highlighted row name one run. */
const runNumber = (runId: string | null | undefined) => reportRunNumber(card.value?.view.value?.history ?? [], runId)
const resultNumber = computed(() => runNumber(resultRun.value?.runId))
const resultAt = computed(() => documentHistoryWhen(resultRun.value?.finishedAt ?? resultRun.value?.startedAt ?? null))
const runLabel = computed(() => !runCount.value ? t(k('runs_none'))
  : resultNumber.value === null ? t(k('runs_unnumbered'), { at: resultAt.value }) : t(k('runs'), { n: resultNumber.value, at: resultAt.value }))
const tag = computed(() => {
  const c = card.value, m = mode.value
  if (!c || !m) return ''
  return t(k(reportTagKey(m, c.presentation.value.resultState.key)), { n: c.presentation.value.pending, done: c.test.runState.value.done, total: c.test.runState.value.total || items.value.length })
})
const resultSummary = computed(() => {
  const code = items.value.filter((item) => item.executionMode === 'automated').length
  return resultNumber.value === null ? t(k('result_summary_unnumbered'), { at: resultAt.value, code }) : t(k('result_summary'), { n: resultNumber.value, at: resultAt.value, code })
})
const big = computed(() => {
  const c = card.value, n = items.value.length
  if (mode.value === 'running') return { value: c?.test.runState.value.done ?? 0, total: c?.test.runState.value.total || n, label: 'big_running' }
  if (mode.value === 'confirm') return { value: c?.presentation.value.pending ?? 0, total: null, label: 'big_confirm' }
  return { value: items.value.filter((item) => item.state === 'pass').length, total: n, label: 'big_pass' }
})
const legend = computed(() => {
  const counts = reportLegend(items.value)
  if (mode.value === 'running') {
    const run = card.value?.test.runState.value
    return [{ state: 'pass', n: live.value?.passedCases ?? 0 }, { state: 'running', n: Math.max(0, (run?.total ?? 0) - (run?.done ?? 0)) }, { state: 'queued', n: counts.queued }]
  }
  return (['pass', 'fail', 'blocked', 'not_run'] as const).map((state) => ({ state, n: counts[state] }))
})
const filterOptions = computed(() => mode.value !== 'confirm' && mode.value !== 'complete' ? [] : [
  { value: 'flagged', label: `${t(k(mode.value === 'confirm' ? 'filter_pending' : 'filter_decided'))} ${mode.value === 'confirm' ? items.value.filter((item) => item.pending).length : items.value.filter((item) => item.flagged || item.decision).length}` },
  { value: 'all', label: `${t(k('filter_all'))} ${items.value.length}` },
])
const previousRun = computed(() => {
  const history = card.value?.view.value?.history ?? []
  const index = history.findIndex((run) => run.runId === card.value?.view.value?.latest?.runId)
  const run = index >= 0 ? history[index + 1] : null
  return run ? `#${history.length - index - 1} · ${t(`gui.inspector.testSpec.${testSpecVerdictKey(run.overall)}`)} · ${documentHistoryWhen(run.finishedAt ?? run.startedAt)}` : null
})
const decisionShown = computed(() => (mode.value === 'confirm' || mode.value === 'complete') && !!selected.value && (selected.value.flagged || !!selected.value.decision))
const followReason = computed(() => { const key = card.value?.openReason('follow'); return key ? t(`gui.inspector.testSpec.${key}`) : '' })
const reason = (op: TestCardCommand, caseId?: string) => { const key = card.value?.can(op, caseId).reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' }
const entry = computed(() => card.value?.can('resultEntry', selected.value?.caseId) ?? { enabled: false, reason: 'reason.busy' })
const entryReason = computed(() => reason('resultEntry', selected.value?.caseId))
const causeOptions = computed(() => (['env'] as const).map((cause) => {
  // 통과 처리 only opens its reason input; recording it is the guarded change.
  const why = reason('rerunCase', selected.value?.caseId)
  return { value: cause, label: t(k(`cause_${cause}`)), disabled: !!why, title: why || null }
}))
const areaReason = computed(() => {
  const item = selected.value
  if (!item || item.retrying) return ''
  if (item.manualCheck) return entryReason.value
  return ''
})
const refusal = computed(() => {
  const failure = card.value?.test.failure.value
  return failure && (failure.operation === 'rerunCase' || failure.operation === 'resultEntry') ? t(`gui.inspector.testSpec.${failure.key}`) : ''
})
function markOf(item: ReportItem) {
  if (item.retrying || item.state === 'running') return TEST_PENDING_MARK
  return item.state === 'queued' ? null : testVerdictMark(item.status)
}
function actualText(item: ReportItem) {
  // A full run's cells are its progress: no earlier actual is shown as this run's.
  if (item.state === 'running') return t(k('actual_running'))
  if (item.state === 'queued') return '—'
  if (item.actual) return item.actual
  return item.retrying ? t(k('actual_running')) : t(k('actual_none'))
}
function decisionLabel(item: ReportItem) {
  const cause = item.decision?.cause
  return cause ? t(k(cause === 'manual' || cause === 'manualF' ? `decided_${cause}` : `cause_${cause}`)) : ''
}

watch(() => [state.value?.open, state.value?.zIndex] as const, () => {
  if (state.value?.open && state.value.caseId) pick(state.value.caseId)
}, { immediate: true })
function pick(caseId: string) {
  const d = draft.value
  if (!visible.value.some((item) => item.caseId === caseId)) { d.filter = 'all'; d.filterMode = mode.value }
  if (d.caseId !== caseId) { d.passFor = null; d.cause = null }
  d.caseId = caseId
}
function setFilter(value: ReportFilter) {
  const d = draft.value
  d.filter = value; d.filterMode = mode.value
  if (!visible.value.some((item) => item.caseId === d.caseId)) d.caseId = visible.value[0]?.caseId ?? null
}
function choose(cause: ReportCause) {
  const item = selected.value
  if (item && cause === 'env') void rerun(item.caseId)
}
function cancelPass() { draft.value.passFor = null; draft.value.reason = ''; draft.value.cause = null }
async function rerun(caseId: string) {
  if (!current() || !card.value?.can('rerunCase', caseId).enabled) return
  // The shown selection is kept as the explicit one, so its end can be told from a case picked meanwhile.
  if (selectedId.value === caseId) draft.value.caseId = caseId
  const ok = await card.value.rerunCase(caseId)
  if (ok && current()) draft.value.rerunFor = caseId
}
// A rerun is judged when it has ended, not when it was accepted: a pass is a decision (the next unchecked case is
// selected, from the one after it, once around); a failure, a cancel or a failed run keeps the case selected.
watch(() => {
  const caseId = card.value ? draft.value.rerunFor : null
  return caseId && card.value ? reportRerunOutcome(items.value, caseId, card.value.presentation.value.runScope !== null) : null
}, (outcome) => {
  const d = card.value ? draft.value : null, caseId = d?.rerunFor
  if (!d || !caseId || !outcome || outcome === 'running') return
  d.rerunFor = null
  // Only while the rerun case is still the selection: a case picked meanwhile stays picked.
  if (outcome === 'passed' && d.caseId === caseId) { const next = reportNextPending(items.value, caseId); if (next) pick(next) }
}, { immediate: true })
async function enter(status: 'PASS' | 'FAIL') {
  const c = card.value, item = selected.value
  if (!current() || !c || !item || !c.can('resultEntry', item.caseId).enabled) return
  const ok = await c.enterResult({ case_id: item.caseId, status })
  // After a decision the next unchecked case is selected, from the one after it, once around.
  if (ok && current()) { const next = reportNextPending(items.value, item.caseId); if (next) pick(next) }
}
function runPrimary() {
  const item = selected.value
  const primary = reportPrimary(item, causeMode.value, null)
  if (primary === 'rerun' && item) void rerun(item.caseId)
}
function onEscape(event: KeyboardEvent) {
  if (draft.value.passFor) { event.stopPropagation(); cancelPass() }
}
function onKeydown(event: KeyboardEvent) {
  const element = event.target instanceof Element ? event.target : null
  const panel = body.value?.closest('.ui-modeless-panel') ?? null
  const otherPanel = element?.closest('.ui-modeless-panel') ?? null
  const action = reportKeyAction({ key: event.key, open: !!state.value?.open, minimized: !!state.value?.minimized,
    modifier: event.ctrlKey || event.metaKey || event.altKey,
    typing: !!element && (/^(INPUT|TEXTAREA|SELECT)$/.test(element.tagName) || (element as HTMLElement).isContentEditable),
    otherWindow: !!otherPanel && otherPanel !== panel,
    onButton: !!element?.closest('button, a, [role="menuitem"], [role="radio"]') && !element.closest('[data-report-row]') })
  if (!action || !current()) return
  if (action === 'escape') { if (draft.value.passFor) cancelPass(); return }
  event.preventDefault()
  if (action === 'primary') { runPrimary(); return }
  const next = reportMove(visible.value, selectedId.value, action === 'next' ? 1 : -1)
  if (next) pick(next)
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>
<style scoped>
.report { display: flex; flex-direction: column; gap: 10px; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.window-actions { display: flex; gap: 4px; flex-wrap: wrap; }
small { color: var(--fg2-muted); font-size: 12px; font-weight: normal; }
h3 { display: flex; gap: 14px; align-items: center; margin: 4px 0 0; font-size: 13px; } h3 small, h3 .filter { margin-left: auto; }
.sum { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 26px; padding: 12px 14px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); }
.big { display: flex; align-items: baseline; gap: 7px; min-width: 110px; } .big b { font-size: 26px; line-height: 1; font-variant-numeric: tabular-nums; }
.big b small { font-size: 14px; } .big span { color: var(--fg2-muted); font-size: 12px; } .big.attn b { color: var(--fg2-yellow); }
.big .defects { align-self: center; color: var(--fg2-status-danger-fg); font-weight: 700; }
.bar { display: flex; flex-wrap: wrap; gap: 3px; }
.cell { position: relative; width: 24px; height: 16px; padding: 0; border: 1px solid var(--fg2-line); border-radius: 3px; background: var(--fg2-panel); cursor: pointer; }
.cell[data-state='pass'] { background: var(--fg2-status-success-fg); border-color: var(--fg2-status-success-border); }
.cell[data-state='fail'] { background: var(--fg2-status-danger-fg); border-color: var(--fg2-status-danger-border); }
.cell[data-state='blocked'] { background: var(--fg2-orange); border-color: var(--fg2-orange); }
.cell[data-state='not_run'] { background: var(--fg2-muted); border-color: var(--fg2-muted); }
.cell[data-state='running'] { background: var(--fg2-status-info-bg); border-color: var(--fg2-status-info-border); }
.cell[data-state='queued'] { border: 1px dashed var(--fg2-line-strong); }
.cell.sel { outline: 2px solid var(--fg2-focus); outline-offset: 1px; }
.cell.pend::after { content: ''; position: absolute; left: 50%; bottom: -6px; margin-left: -2px; width: 4px; height: 4px; border-radius: 50%; background: var(--fg2-yellow); }
.legend { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 7px; color: var(--fg2-muted); font-size: 11px; } .legend span { display: inline-flex; align-items: center; gap: 4px; }
.legend b { color: var(--fg2-text); } .legend i { width: 8px; height: 8px; border-radius: 2px; border: 1px solid var(--fg2-line); }
.legend i[data-state='pass'] { background: var(--fg2-status-success-fg); } .legend i[data-state='fail'] { background: var(--fg2-status-danger-fg); }
.legend i[data-state='blocked'] { background: var(--fg2-orange); } .legend i[data-state='not_run'] { background: var(--fg2-muted); }
.legend i[data-state='running'] { background: var(--fg2-status-info-bg); border-color: var(--fg2-status-info-border); } .legend i[data-state='queued'] { border-style: dashed; }
.gate { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 7px; font-size: 12px; font-weight: 650; }
.gate.stop { color: var(--fg2-status-danger-fg); } .gate.go { color: var(--fg2-status-success-fg); }
/* flowgate.v02.0094: a stopping case id in the line's own type like every case id here (no monospace of its own), the danger colour kept. */
.gate-id { padding: 1px 6px; border: 1px solid var(--fg2-status-danger-border); border-radius: var(--fg2-control-radius-sm); background: var(--fg2-status-danger-bg); color: var(--fg2-status-danger-fg); font-family: inherit; font-size: inherit; font-weight: 400; cursor: pointer; }
.note { margin: 0; color: var(--fg2-muted); font-size: 12px; }
.split { display: grid; grid-template-columns: minmax(250px, 320px) minmax(0, 1fr); gap: 10px; align-items: start; }
.list { margin: 0; padding: 0; list-style: none; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); overflow: hidden; }
.row { display: grid; grid-template-columns: 22px 56px minmax(0, 1fr) auto; gap: 7px; align-items: center; width: 100%; height: 34px; padding: 0 10px; border: 0; border-bottom: 1px solid var(--fg2-line); background: transparent; color: var(--fg2-text); text-align: left; cursor: pointer; }
.row.on { background: var(--fg2-status-info-bg); box-shadow: inset 3px 0 0 var(--fg2-status-info-fg); } .row.pend .title { font-weight: 700; }
.row:focus-visible { outline: 2px solid var(--fg2-focus); outline-offset: -2px; }
.title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; }
/* flowgate.v02.0094: a case id is written in the list's own type, muted; only code (the automation ref) is monospace. */
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 11.5px; } .id { color: var(--fg2-muted); font-size: 12px; white-space: nowrap; }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--fg2-yellow); }
.label { font-size: 11px; font-weight: 700; color: var(--fg2-muted); } .label[data-cause='impl'], .label[data-cause='manualF'] { color: var(--fg2-status-danger-fg); }
.label[data-cause='scen'] { color: var(--fg2-purple); } .label[data-cause='env'] { color: var(--fg2-orange); } .label[data-cause='pass'], .label[data-cause='manual'] { color: var(--fg2-status-success-fg); }
.detail { display: flex; flex-direction: column; gap: 12px; min-width: 0; padding: 13px 14px 14px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); }
.detail header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } .detail header b { font-size: 14px; }
.tag { padding: 0 7px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-pill); color: var(--fg2-muted); font-size: 10.5px; }
.ref { margin-left: auto; color: var(--fg2-muted); font-size: 11px; }
.cmp { display: grid; grid-template-columns: 1fr 1fr; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); overflow: hidden; }
.cmp > div { min-width: 0; padding: 9px 11px 11px; } .cmp > div + div { border-left: 1px solid var(--fg2-line); }
.cmp small { display: block; margin-bottom: 3px; font-size: 10.5px; } .cmp p { margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.cmp [data-state='fail'] { box-shadow: inset 3px 0 0 var(--fg2-status-danger-fg); } .cmp [data-state='blocked'] { box-shadow: inset 3px 0 0 var(--fg2-orange); }
.cmp [data-state='pass'] { box-shadow: inset 3px 0 0 var(--fg2-status-success-fg); }
.mute { color: var(--fg2-muted); }
.decision { display: flex; flex-direction: column; gap: 8px; padding: 9px 11px; border: 1px solid var(--fg2-yellow); border-radius: var(--fg2-control-radius); }
.decision.done { border-color: var(--fg2-line-strong); }
.line { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; } .who { color: var(--fg2-yellow); font-size: 10.5px; font-weight: 700; }
.line .note { flex: 1; min-width: 120px; } .acts { display: flex; flex-wrap: wrap; gap: 6px; margin-left: auto; align-items: center; } .meta { color: var(--fg2-muted); font-size: 11px; }
.ok { color: var(--fg2-status-success-fg); } .retry { margin: 0; color: var(--fg2-orange); font-size: 12px; }
.reason-row { display: flex; gap: 6px; align-items: center; }
.causes { display: flex; flex-direction: column; min-width: 280px; padding: 4px; }
.cause { display: grid; grid-template-columns: 1fr auto; gap: 8px; padding: 7px 9px; border: 0; border-radius: var(--fg2-control-radius-sm); background: transparent; color: var(--fg2-text); text-align: left; cursor: pointer; }
.cause:hover:not(:disabled) { background: var(--fg2-status-info-bg); } .cause:disabled { cursor: not-allowed; opacity: 0.5; } .cause small { color: var(--fg2-muted); }
.facts { display: grid; grid-template-columns: 70px minmax(0, 1fr); gap: 6px 12px; margin: 0; font-size: 12px; } .facts dt { color: var(--fg2-muted); }
.facts dd { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-width: 0; margin: 0; overflow-wrap: anywhere; }
.evidence { padding: 1px 7px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); font-size: 11px; }
.runs { margin: 0; padding: 8px; max-height: 320px; overflow: auto; list-style: none; color: var(--fg2-text); font-size: 12px; } .runs li { padding: 4px; } .runs .current { color: var(--fg2-status-info-fg); }
@media (max-width: 760px) { .split, .cmp { grid-template-columns: minmax(0, 1fr); } .cmp > div + div { border-left: 0; border-top: 1px solid var(--fg2-line); } }
</style>
