<template>
  <div class="tsr" data-test="test-report-panel">
    <div class="tsr-summary" data-test="test-report-summary">
      <!-- While a run is live its progress leads: the previous verdict and gate are not this run's. -->
      <div
        v-if="!running"
        class="tsr-overall"
        :class="`tsr-overall--${overall.tone}`"
        :title="`${t(k('test.overall'))}: ${t(k(overall.key))}`"
        data-test="test-report-overall"
        :data-tone="overall.tone"
      >
        <span class="tsr-overall-glyph" aria-hidden="true">{{ overall.glyph }}</span>
        <span class="tsr-overall-word">{{ t(k(overall.key)) }}</span>
      </div>
      <TestMarkBadge v-if="gate && !running" :mark="gate" data-test="test-report-gate" />
      <TestMarkBadge
        v-for="flag in flags"
        :key="flag.mark.key"
        :mark="flag.mark"
        :text="flag.text"
        :tooltip="t(k(flag.mark.key), flag.params)"
      />
    </div>

    <div class="tsr-tiles" data-test="test-report-tiles">
      <div v-for="tile in tiles" :key="tile.key" class="tsr-tile" :class="`tsr-tile--${tile.tone}`" :title="tile.label" :data-test="`test-report-tile-${tile.key}`">
        <span class="tsr-tile-n">{{ tile.value }}</span>
        <span class="tsr-tile-l">{{ tile.glyph }}</span>
      </div>
    </div>
    <p v-if="doc.requiredCounts" class="tsr-scope" data-test="test-report-scope">
      {{ t(k('test.required_scope'), { pass: doc.requiredCounts.pass, total: doc.requiredCounts.total }) }}
    </p>

    <table v-if="rows?.length" class="tsr-table" data-test="test-report-table">
      <thead>
        <tr>
          <th />
          <th>{{ t(k('test.field.expected')) }}</th>
          <th>{{ t(k('test.field.actual')) }}</th>
          <th>{{ t(k('test.field.evidence')) }}</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="row.caseId"
          :class="{ 'tsr-row--bad': row.required && verdictOf(row) !== 'PASS' && !pending.has(row.caseId) }"
          data-test="test-report-row"
          :data-case-id="row.caseId"
        >
          <td class="tsr-case">
            <div class="tsr-case-hd">
              <TestMarkBadge v-if="pending.has(row.caseId)" :mark="TEST_PENDING_MARK" data-test="test-report-row-pending" />
              <TestMarkBadge v-else :mark="testVerdictMark(verdictOf(row))" data-test="test-report-row-verdict" />
              <span class="tsr-case-id">{{ row.caseId }}</span>
              <TestMarkBadge v-if="testModeMark(row.executionMode)" :mark="testModeMark(row.executionMode)!" />
              <TestMarkBadge :mark="row.required ? TEST_REQUIRED_MARK : TEST_OPTIONAL_MARK" />
            </div>
            <div v-if="row.title" class="tsr-case-title">{{ row.title }}</div>
            <div v-if="row.defectRef" class="tsr-sub" :title="t(k('test.field.defectRef'))">⚑ {{ row.defectRef }}</div>
          </td>
          <td class="tsr-text">{{ row.expected ?? '—' }}</td>
          <td class="tsr-text">{{ row.actual ?? '—' }}</td>
          <td class="tsr-text">
            <div v-for="(item, index) in row.evidence" :key="index" class="tsr-evidence">
              <a v-if="testEvidenceLink(item)" :href="testEvidenceLink(item)!" target="_blank" rel="noopener noreferrer" data-test="test-report-evidence-link">{{ item.label ?? item.value }}</a>
              <span v-else>{{ item.label ? `${item.label}: ` : '' }}{{ item.value }}</span>
            </div>
            <span v-if="!row.evidence.length">—</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0053 T#4 — the test report's view: the v0.1 TestReportPanel
// (client/src/main/components/documents/TestReportPanel.vue) read only. Kept: the overall verdict and the
// gate first, the count tiles, the required scope and one row per Case (verdict or "pending" while a run is
// live, id, title, mode, required, expected, actual, evidence, defect), with a required Case that did not
// pass marked. While a run is live its progress leads and the verdict and gate are not shown (v0.1
// `v-if="!runState.active"`): they are the previous run's. The verdict, the gate, the tiles and the scope are
// the server record's (the valid result on the live source first, as in v0.1); the rows are the report as
// saved (TestDocumentBody → testReportRows), so a ✎ edit shows here — none when the saved text is not in the
// report's shape (the window shows it as written under this summary) — but each row's verdict is the server
// record's too, never the text's word (0053 Q #419, testReportVerdicts). A link is a url item's web address
// only (testEvidenceLink); any other evidence is its text. Not carried over: [다시 실행] (the window shows the document; runs belong to the test card), the Basis facts
// (fingerprints, revisions, hashes), run ids and the run history — and the bands and paragraphs, which are
// marks with the words in their tooltips.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestSpecDocument, TestSpecResultRow } from '@/features/document'
import TestMarkBadge from './TestMarkBadge.vue'
import {
  TEST_OPTIONAL_MARK,
  TEST_PENDING_MARK,
  TEST_REQUIRED_MARK,
  testDocumentFlags,
  testEvidenceLink,
  testGateMark,
  testCaseKey,
  testModeMark,
  testReportVerdicts,
  testVerdictMark,
  type TestMarkTone,
} from './specialDocumentModel'

const props = defineProps<{
  doc: TestSpecDocument
  /** The Case rows of the saved report; null: not in the report's shape. */
  rows: readonly TestSpecResultRow[] | null
}>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.${key}`

const running = computed(() => props.doc.view.activeRun !== null)
const overall = computed(() => testVerdictMark(props.doc.overall))
// The gate is the server's (`gate.passed`); a report whose gate does not apply shows none.
const gate = computed(() => (props.doc.view.gate?.applies ? testGateMark(props.doc.view.gate.passed) : null))
const pending = computed(() => new Set(props.doc.pendingCaseIds))
const flags = computed(() => testDocumentFlags(props.doc))
const verdicts = computed(() => testReportVerdicts(props.doc))
/** A row's verdict: the record's for its Case (null — not run — when the record has none). */
const verdictOf = (row: TestSpecResultRow) => verdicts.value.get(testCaseKey(row.caseId)) ?? null

const tiles = computed(() => {
  const counts = props.doc.counts ?? { total: 0, pass: 0, fail: 0, blocked: 0, notRun: 0 }
  const tile = (key: string, value: number, glyph: string, tone: TestMarkTone, label: string) => ({ key, value, glyph, tone, label })
  return [
    tile('total', counts.total, 'Σ', 'neutral', t(k('test.total'))),
    tile('pass', counts.pass, '✓', 'success', t(k('test.verdict.PASS'))),
    tile('fail', counts.fail, '✕', 'danger', t(k('test.verdict.FAIL'))),
    tile('blocked', counts.blocked, '!', 'warning', t(k('test.verdict.BLOCKED'))),
    tile('not_run', counts.notRun, '–', 'neutral', t(k('test.verdict.NOT_RUN'))),
  ]
})
</script>

<style scoped>
.tsr {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 14px 18px;
  color: var(--fg2-text);
  font-size: 12.5px;
}

.tsr-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.tsr-overall {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  font-weight: 600;
}

.tsr-overall-glyph {
  font-size: 16px;
}

.tsr-overall--success {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.tsr-overall--danger {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.tsr-overall--warning,
.tsr-overall--neutral {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.tsr-tiles {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 8px;
}

.tsr-tile {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 6px 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-panel);
}

.tsr-tile-n {
  font-size: 16px;
  font-weight: 700;
}

.tsr-tile-l {
  color: var(--fg2-muted);
  font-size: 11px;
}

.tsr-tile--success .tsr-tile-n {
  color: var(--fg2-status-success-fg);
}

.tsr-tile--danger .tsr-tile-n {
  color: var(--fg2-status-danger-fg);
}

.tsr-tile--warning .tsr-tile-n {
  color: var(--fg2-status-warning-fg);
}

.tsr-scope {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}

.tsr-table {
  width: 100%;
  border-collapse: collapse;
}

.tsr-table th {
  padding: 4px 6px;
  border-bottom: 1px solid var(--fg2-line-strong);
  color: var(--fg2-muted);
  font-size: 11.5px;
  font-weight: 600;
  text-align: left;
}

.tsr-table td {
  padding: 6px;
  border-bottom: 1px solid var(--fg2-line);
  vertical-align: top;
}

.tsr-row--bad td {
  background: var(--fg2-status-danger-bg);
}

.tsr-case {
  min-width: 140px;
}

.tsr-case-hd {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
}

.tsr-case-id {
  font-family: 'JetBrains Mono', ui-monospace, monospace;
  font-size: 11.5px;
  font-weight: 600;
}

.tsr-case-title {
  margin-top: 2px;
}

.tsr-sub {
  color: var(--fg2-muted);
  font-size: 11px;
}

.tsr-text {
  max-width: 260px;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 12px;
}

.tsr-evidence a {
  color: var(--fg2-blue);
}
</style>
