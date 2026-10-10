<template>
  <!-- flowgate.v02.0048 T#11 — the test section of the documents tab (D#1 §10.2 T#11 row): the run state of
       the selected TS / TSR with its progress, the Case tally, the result summary (and a TSR's test gate),
       the recent runs, ▶ 실행 / ■ 취소 with the reason when disabled and the refusal the server answered,
       and ↗ 열기 for everything in detail. A thin container: useTestSpec reads and calls, testSpecModel
       decides the form, the state and availability.
       flowgate.v02.0051 stage 2 — the v7 테스트 card (0035 test.*) on top: the three slot lines 지시 (TS) ·
       케이스 (TSC) · 결과 (TSR), built from the common slot list part out of the same test view; the 0048
       tally, buttons, reasons and history stay below as reused content. -->
  <section class="inspector-test-spec" data-test="inspector-test-spec" :data-status="status" :data-form="form">
    <InspectorSlotList :slots="lines" @open="openLine" />

    <details :key="target.docId ?? undefined" class="inspector-test-spec-details" data-test="test-spec-details">
      <summary>{{ t('gui.designer.inspector.pane.details') }}</summary>
    <div class="inspector-test-spec-line">
      <h4 class="inspector-test-spec-heading">{{ t(k('title')) }}</h4>
      <span
        v-if="view && form === 'spec'"
        class="inspector-test-spec-state"
        data-test="test-spec-state"
        :data-state="runState.key"
        :data-tone="runState.tone"
      >{{ t(k(`state.${runState.key}`)) }}<template v-if="runState.active && runState.total"> · {{ t(k('progress'), { done: runState.done, total: runState.total }) }}</template></span>
      <span v-if="runState.overall" class="inspector-test-spec-verdict" data-test="test-spec-verdict">{{ t(k(verdictKey)) }}</span>
    </div>

    <InspectorPaneState v-if="status === 'loading' && !view" state="loading" />
    <InspectorPaneState v-else-if="errorKey && !view" state="error" :message="t(k(errorKey))" retryable @retry="reload" />
    <template v-else-if="view">
      <InspectorPaneState v-if="errorKey" state="error" :message="t(k(errorKey))" retryable @retry="reload" />

      <template v-if="form === 'spec'">
        <p v-if="view.kind === 'TS'" class="inspector-test-spec-sub" data-test="test-spec-tally">{{ t(k('tally'), { ...tally }) }} · {{ t(k('runnable'), { ...tally }) }}</p>
        <p v-if="view.specErrors" class="inspector-test-spec-sub" data-test="test-spec-errors">{{ t(k('spec_errors'), { count: view.specErrors }) }}</p>
        <p v-if="counts" class="inspector-test-spec-sub" data-test="test-spec-counts">{{ t(k('counts'), { ...counts }) }}</p>
        <p
          v-if="view.gate?.applies"
          class="inspector-test-spec-sub"
          data-test="test-spec-gate"
          :data-passed="view.gate.passed"
        >{{ t(k(view.gate.passed ? 'gate.passed' : 'gate.blocked')) }}</p>
      </template>
      <p v-else-if="form === 'legacy'" class="inspector-test-spec-sub" data-test="test-spec-legacy">{{ t(k('legacy')) }}</p>
      <p v-else class="inspector-test-spec-sub" data-test="test-spec-none">{{ t(k('none')) }}</p>

      <div class="inspector-test-spec-line">
        <Button
          v-if="availability.run.visible"
          size="sm"
          variant="primary"
          :disabled="!availability.run.enabled"
          :loading="busy === 'run'"
          :title="t(k('run_hint'))"
          data-test="test-spec-run"
          @click="run"
        >{{ t(k('run')) }}</Button>
        <Button
          v-if="availability.cancel.visible"
          size="sm"
          variant="secondary"
          :disabled="!availability.cancel.enabled"
          :loading="busy === 'cancel'"
          data-test="test-spec-cancel"
          @click="cancel"
        >{{ t(k('cancel')) }}</Button>
        <Button size="sm" variant="ghost" :title="t(k('open_hint'))" data-test="test-spec-open" @click="open(view.docId)">{{ t(k('open')) }}</Button>
        <Button
          v-if="view.kind === 'TSR' && view.tsDocId"
          size="sm"
          variant="ghost"
          :title="t(k('open_hint'))"
          data-test="test-spec-open-spec"
          @click="open(view.tsDocId)"
        >{{ t(k('open_spec')) }}</Button>
        <Button
          v-if="view.kind === 'TS' && view.tsrDocId"
          size="sm"
          variant="ghost"
          :title="t(k('open_hint'))"
          data-test="test-spec-open-report"
          @click="open(view.tsrDocId)"
        >{{ t(k('open_report')) }}</Button>
      </div>
      <div v-if="shownReason" data-test="test-spec-reason">
        <InspectorReason :text="t(k(shownReason))" />
      </div>
      <div v-if="failure" data-test="test-spec-failure">
        <InspectorReason tone="danger" :text="t(k(failure.key))" />
      </div>

      <template v-if="form === 'spec'">
        <h4 class="inspector-test-spec-heading">{{ t(k('history.title')) }}</h4>
        <p v-if="!history.length" class="inspector-test-spec-sub" data-test="test-spec-history-empty">{{ t(k('history.empty')) }}</p>
        <ul v-else class="inspector-test-spec-rows">
          <li v-for="row in history" :key="row.runId" class="inspector-test-spec-row" data-test="test-spec-history-row" :data-run="row.runId">
            <span>{{ t(k(row.statusKey)) }}</span>
            <span v-if="row.verdictKey">{{ t(k(row.verdictKey)) }}</span>
            <span v-if="row.counts" class="inspector-test-spec-sub">{{ t(k('counts'), { ...row.counts }) }}</span>
            <span v-if="row.at" class="inspector-test-spec-sub">{{ row.at }}</span>
          </li>
        </ul>
      </template>
    </template>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { testSpecSlots, testSpecVerdictKey, type TestSpecSlot } from './testSpecModel'
import { useTestSpec } from './useTestSpec'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t, te } = useI18n()

const k = (key: string) => `gui.inspector.testSpec.${key}`

const { view, form, tally, runState, counts, history, availability, status, errorKey, busy, failure, run, cancel, open, reload } = useTestSpec(
  () => props.target,
)

const verdictKey = computed(() => testSpecVerdictKey(runState.value.overall))

/** One reason line: the disabled operation that is shown (run or cancel). */
const shownReason = computed(() => {
  const { run: start, cancel: stop } = availability.value
  if (start.visible && start.reason) return start.reason
  if (stop.visible && stop.reason) return stop.reason
  return null
})

/** The pill text: the Frame's wording for a review / document state, this feature's for a run state, the raw value otherwise. */
function stateLabel(state: NonNullable<TestSpecSlot['state']>): string {
  const key = state.key.startsWith('state.') ? k(state.key) : `gui.designer.inspector.${state.key}`
  return te(key) ? t(key) : state.raw
}

function summaryOf(slot: TestSpecSlot): string | null {
  switch (slot.summary) {
    case 'tally':
      return t(k('tally'), { ...tally.value })
    case 'counts':
      return counts.value ? t(k('counts'), { ...counts.value }) : null
    case 'progress':
      return t(k('progress'), { done: runState.value.done, total: runState.value.total })
    default:
      return null
  }
}

const lines = computed<InspectorSlotView[]>(() =>
  testSpecSlots(view.value, props.target).map((slot) => ({
    key: slot.key,
    kind: slot.kind,
    form: slot.form,
    name: t(k(`slot.${slot.key}`)),
    id: slot.id,
    empty: slot.empty,
    emptyText: slot.emptyKey ? t(k(slot.emptyKey)) : null,
    state: slot.state ? { label: stateLabel(slot.state), tone: slot.state.tone } : null,
    summary: summaryOf(slot),
    progress: slot.progress,
    focus: slot.focus,
    danger: slot.state?.tone === 'danger',
    open: slot.open,
    write: slot.write,
    writeReason: slot.write === 'disabled' ? t(k('write_unconnected')) : null,
  })),
)

function openLine(line: InspectorSlotView) {
  if (line.id) open(line.id)
}
</script>

<style scoped>
.inspector-test-spec {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 12px;
}

.inspector-test-spec-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.inspector-test-spec-heading {
  margin: 0;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--fg2-muted);
}

.inspector-test-spec-state[data-tone='info'] {
  color: var(--fg2-status-info-fg, inherit);
}

.inspector-test-spec-state[data-tone='success'] {
  color: var(--fg2-status-success-fg, inherit);
}

.inspector-test-spec-state[data-tone='warning'] {
  color: var(--fg2-status-warning-fg, inherit);
}

.inspector-test-spec-state[data-tone='danger'] {
  color: var(--fg2-status-danger-fg, inherit);
}

.inspector-test-spec-verdict {
  font-weight: 600;
}

.inspector-test-spec-sub {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.inspector-test-spec-rows {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.inspector-test-spec-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}
</style>
