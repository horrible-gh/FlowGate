<template>
  <!-- F81: the scenario list — the one view of a test card's cases and their recorded results. The inspector's
       scenario / report windows and the document window's scenario / report bodies all draw this component. -->
  <div class="tsl" data-test="test-scenario-list">
    <div class="tsl-tally" data-test="test-scenario-tally">
      <span class="tsl-count">{{ t(k('count'), { n: counts.total }) }}</span>
      <span v-for="word in RESULTS" :key="word" class="tsl-result" :class="`tsl-result--${word}`" :data-test="`test-scenario-tally-${word}`">{{ t(k(`result_${word}`)) }} {{ countOf(word) }}</span>
    </div>
    <div class="tsl-scroll">
      <table>
        <thead><tr>
          <th>{{ t(k('id')) }}</th><th>{{ t(k('title')) }}</th><th>{{ t(k('result')) }}</th><th>{{ t(k('remarks')) }}</th><th /><th v-if="interactive" />
        </tr></thead>
        <tbody>
          <tr v-if="!rows.length"><td :colspan="interactive ? 6 : 5" class="tsl-empty" data-test="test-scenario-empty">{{ t(k('empty')) }}</td></tr>
          <tr v-for="row in rows" :key="row.caseId" data-test="test-scenario-row" :data-case="row.caseId" :data-result="row.result" :class="{ 'tsl-row--open': interactive }" @dblclick="interactive && emit('open', row.caseId)">
            <td class="tsl-id">{{ row.caseId }}</td>
            <td class="tsl-title">{{ row.title }}</td>
            <td><span class="tsl-result" :class="`tsl-result--${pending.has(row.caseId) ? 'running' : row.result}`" :title="resultTip(row)" data-test="test-scenario-result">{{ pending.has(row.caseId) ? t(k('result_running')) : t(k(`result_${row.result}`)) }}</span></td>
            <td class="tsl-remarks">
              <component :is="interactive ? 'button' : 'span'" v-if="row.openRemarks" :type="interactive ? 'button' : undefined" class="tsl-remark" :title="t(k('remarks_tip'), { n: row.openRemarks })" data-test="test-scenario-remarks" @click="interactive && emit('remarks', row.caseId)">💬 {{ row.openRemarks }}</component>
            </td>
            <td class="tsl-evidence"><span v-if="row.evidence.total" :title="t(k('evidence_tip'), { n: row.evidence.total })" data-test="test-scenario-evidence">📎{{ row.evidence.total }}</span></td>
            <td v-if="interactive" class="tsl-open"><button type="button" class="tsl-edit" :aria-label="t(k('open'), { id: row.caseId })" :title="t(k('open'), { id: row.caseId })" data-test="test-scenario-open" @click="emit('open', row.caseId)">✎</button></td>
          </tr>
        </tbody>
      </table>
    </div>
    <section v-if="overallComment" class="tsl-comment" data-test="test-scenario-comment">
      <h4>{{ t(k('comment')) }}</h4>
      <p>{{ overallComment }}</p>
    </section>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { TestCaseResult, TestScenarioCounts, TestScenarioRow } from '@/features/document'

const props = withDefaults(defineProps<{
  rows: readonly TestScenarioRow[]
  /** The tally of the recorded results; counted from the rows when left out. */
  counts?: TestScenarioCounts | null
  /** The AI's few lines over the run (the report); nothing is drawn without it. */
  overallComment?: string | null
  /** Case ids a live run has not reported yet. */
  pendingCaseIds?: readonly string[]
  /** A case can be opened and its remarks shown (the inspector); the document window only reads. */
  interactive?: boolean
}>(), { counts: null, overallComment: null, pendingCaseIds: () => [], interactive: false })
const emit = defineEmits<{ open: [caseId: string]; remarks: [caseId: string] }>()

const { t } = useI18n()
const k = (key: string) => `gui.document_window.test.scenario.${key}`
const RESULTS: readonly TestCaseResult[] = ['pass', 'fail', 'skip', 'not_run']
const pending = computed(() => new Set(props.pendingCaseIds))
const counts = computed<TestScenarioCounts>(() => props.counts ?? {
  total: props.rows.length,
  pass: props.rows.filter((row) => row.result === 'pass').length,
  fail: props.rows.filter((row) => row.result === 'fail').length,
  skip: props.rows.filter((row) => row.result === 'skip').length,
  notRun: props.rows.filter((row) => row.result === 'not_run').length,
})
const countOf = (word: TestCaseResult) => (word === 'not_run' ? counts.value.notRun : counts.value[word])
/** Who recorded the result and, for a skip, why. */
function resultTip(row: TestScenarioRow): string {
  return [row.resultBy?.name, row.result === 'skip' ? row.skipReason : null].filter(Boolean).join(' · ')
}
</script>

<style scoped>
.tsl { display: flex; flex-direction: column; gap: 10px; min-width: 0; color: var(--fg2-text); font-size: 12px; }
.tsl-tally { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
.tsl-count { color: var(--fg2-muted); margin-right: 4px; }
.tsl-scroll { overflow: auto; min-width: 0; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; color: var(--fg2-muted); font-weight: normal; }
th, td { padding: 8px; border-bottom: 1px solid var(--fg2-line); vertical-align: middle; }
.tsl-id { color: var(--fg2-muted); white-space: nowrap; }
.tsl-title { min-width: 180px; width: 100%; }
.tsl-row--open { cursor: default; }
.tsl-result { display: inline-block; padding: 1px 8px; border: 1px solid var(--fg2-line-strong); border-radius: 999px; white-space: nowrap; color: var(--fg2-muted); }
.tsl-result--pass { border-color: var(--fg2-status-success-fg, #3fb27f); color: var(--fg2-status-success-fg, #3fb27f); }
.tsl-result--fail { border-color: var(--fg2-status-danger-fg, #e5677a); color: var(--fg2-status-danger-fg, #e5677a); }
.tsl-result--skip { border-color: var(--fg2-yellow, #d9a441); color: var(--fg2-yellow, #d9a441); }
.tsl-result--running { border-color: var(--fg2-status-info-fg, #5b9cf5); color: var(--fg2-status-info-fg, #5b9cf5); }
.tsl-remarks, .tsl-evidence, .tsl-open { white-space: nowrap; }
.tsl-remark { padding: 0; border: 0; background: none; color: var(--fg2-yellow, #d9a441); font: inherit; }
button.tsl-remark, .tsl-edit { cursor: pointer; }
.tsl-edit { padding: 2px 6px; border: 0; border-radius: 6px; background: none; color: var(--fg2-muted); font: inherit; }
.tsl-edit:hover { color: var(--fg2-text); background: var(--fg2-line); }
.tsl-evidence { color: var(--fg2-muted); }
.tsl-empty { color: var(--fg2-muted); text-align: center; }
.tsl-comment { padding: 10px 12px; border: 1px solid var(--fg2-line); border-radius: 8px; }
.tsl-comment h4 { margin: 0 0 6px; color: var(--fg2-muted); font-size: 11.5px; font-weight: normal; }
.tsl-comment p { margin: 0; white-space: pre-wrap; }
</style>
