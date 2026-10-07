<template>
  <div class="tsr" data-testid="test-report-panel">
    <div v-if="!report" class="tsr-muted">{{ t('main.test_document.no_result') }}</div>
    <template v-else>
      <!-- 0684 T#3 (D#1 §6-3): while a run is live, its phase and Case count lead the report;
           Cases it has not reported yet read "pending", never a verdict. -->
      <div v-if="runState.active" class="tsr-progress" data-testid="tsr-progress">
        <AppIcon name="spinner" spin /> {{ runStateText }}
      </div>
      <div v-else-if="view.basis_valid === false" class="tsr-stale-band" role="alert" data-testid="tsr-stale-band">
        <AppIcon name="warning" />
        <span>
          {{ t('main.test_document.stale_band') }}
          <template v-if="view.basis_verdict?.reasons?.length"> {{ t('main.test_document.reasons', { reasons: view.basis_verdict.reasons.join(', ') }) }}</template>
        </span>
        <button
          v-if="canRerun"
          type="button"
          class="btn btn-primary btn-sm"
          data-testid="tsr-rerun"
          :disabled="rerunning"
          @click="onRerun"
        >
          <AppIcon :name="rerunning ? 'spinner' : 'arrow-clockwise'" :spin="rerunning" /> {{ t('main.test_document.rerun') }}
        </button>
      </div>
      <div v-else-if="view.basis_verdict?.state === 'unchecked'" class="tsr-unchecked" data-testid="tsr-unchecked">
        {{ t('main.test_document.unchecked_band') }}
      </div>
      <div v-if="rerunError" class="tsr-stale-band" role="alert" data-testid="tsr-rerun-error">{{ rerunError }}</div>
      <!-- Summary first: the reviewer's question is "did it pass, and what did not". -->
      <div v-if="!runState.active" class="tsr-overall" :class="`tsr-overall--${overallKey}`" data-testid="tsr-overall">
        <AppIcon :name="gatePassed ? 'seal-check' : 'prohibit'" />
        <div>
          <div class="tsr-overall-verdict">
            {{ t('main.test_document.overall') }}: <strong>{{ verdictLabel(report.overall) }}</strong>
          </div>
          <div class="tsr-overall-gate">
            {{ gatePassed ? t('main.test_document.gate_passed') : t('main.test_document.gate_blocked') }}
          </div>
        </div>
      </div>
      <section v-if="view.test_basis" class="tsr-basis" data-testid="tsr-basis">
        <strong>{{ t('main.test_document.basis.title') }}</strong>
        <span class="tsr-basis-facts">
          <span v-if="fingerprint">{{ t('main.test_document.basis.fingerprint', { value: fingerprint }) }}</span>
          <span v-if="view.basis_source?.git_revision || view.source_identity?.git_revision">
            {{ t('main.test_document.basis.git_revision', { value: short(view.basis_source?.git_revision || view.source_identity?.git_revision) }) }}
          </span>
          <span v-if="view.basis_source?.source_dirty != null">
            {{ view.basis_source?.source_dirty ? t('main.test_document.basis.dirty') : t('main.test_document.basis.clean') }}
          </span>
          <span v-if="view.basis_source?.measured_at">{{ t('main.test_document.basis.measured_at', { value: view.basis_source.measured_at }) }}</span>
          <span v-if="view.test_asset_identity">{{ t('main.test_document.basis.assets', { count: view.test_asset_identity.asset_count, hash: short(view.test_asset_identity.manifest_hash) }) }}</span>
        </span>
      </section>
      <section v-if="view.stale_previous_result" class="tsr-stale" data-testid="tsr-stale">
        <strong>{{ t('main.test_document.stale_previous_title') }}</strong>
        <div>{{ t('main.test_document.stale_previous_run', { run: view.stale_previous_result.run_id, overall: verdictLabel(view.stale_previous_result.overall) }) }}</div>
        <div v-for="row in view.stale_previous_result.cases || []" :key="row.case_no || ''">
          {{ row.case_no }}: {{ row.case_status }}
        </div>
      </section>
      <div class="tsr-tiles" data-testid="tsr-tiles">
        <div v-for="tile in tiles" :key="tile.key" class="tsr-tile" :class="`tsr-tile--${tile.key}`" :data-testid="`tsr-tile-${tile.key}`">
          <div class="tsr-tile-n">{{ tile.value }}</div>
          <div class="tsr-tile-l">{{ tile.label }}</div>
        </div>
      </div>
      <p class="tsr-scope">
        {{ t('main.test_document.required_scope', { ...requiredCounts }) }}
      </p>

      <table class="tbl tsr-tbl" data-testid="tsr-table">
        <thead>
          <tr>
            <th>{{ t('main.test_document.results.col_case') }}</th>
            <th>{{ t('main.test_document.results.col_status') }}</th>
            <th>{{ t('main.test_document.field.expected') }}</th>
            <th>{{ t('main.test_document.field.actual') }}</th>
            <th>{{ t('main.test_document.field.evidence') }}</th>
            <th>{{ t('main.test_document.field.source') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="row in rows"
            :key="row.case_no ?? ''"
            :class="{ 'tsr-row-bad': row.required && row.case_status !== 'PASS' && !isPending(row.case_no) }"
            data-testid="tsr-row"
            :data-case-id="row.case_no"
          >
            <td class="tsr-case">
              <strong>{{ row.case_no }}</strong>
              <div class="tsr-sub">{{ row.case_title }}</div>
              <div class="tsr-sub">
                {{ modeLabel(row.execution_mode) }} ·
                {{ row.required ? t('main.test_document.required') : t('main.test_document.optional') }}
              </div>
            </td>
            <td>
              <span v-if="isPending(row.case_no)" class="badge badge-info" data-testid="tsr-row-pending">
                <AppIcon name="spinner" spin /> {{ t('main.test_document.pending') }}
              </span>
              <span v-else class="badge" :class="verdictBadge(row.case_status)" data-testid="tsr-row-verdict">
                {{ verdictLabel(row.case_status) }}
              </span>
              <div v-if="row.mapping_conflict" class="tsr-sub tsr-warn">{{ t('main.test_document.mapping_conflict') }}</div>
              <div v-if="row.defect_ref" class="tsr-sub">{{ t('main.test_document.field.defect_ref') }}: {{ row.defect_ref }}</div>
            </td>
            <td class="tsr-text">{{ row.expect || '-' }}</td>
            <td class="tsr-text">{{ row.actual || '-' }}</td>
            <td class="tsr-text">
              <div v-for="(ev, idx) in row.evidence ?? []" :key="idx" class="tsr-ev">
                <span class="badge badge-gray">{{ ev.kind }}</span>
                <a v-if="ev.kind === 'url'" :href="ev.value" target="_blank" rel="noopener noreferrer">{{ ev.value }}</a>
                <span v-else>{{ ev.label ? `${ev.label}: ` : '' }}{{ ev.value }}</span>
              </div>
              <span v-if="!(row.evidence ?? []).length">-</span>
            </td>
            <td class="tsr-text tsr-sub">
              <div v-if="row.source_name">{{ row.source_name }}</div>
              <div v-for="(value, key) in row.source_identity ?? {}" :key="key">{{ key }}: {{ value }}</div>
              <div v-if="row.checked_by || row.checked_at">{{ [row.checked_by, row.checked_at].filter(Boolean).join(' / ') }}</div>
              <div v-if="row.carried_from_run_id">{{ t('main.test_document.carried', { run: row.carried_from_run_id }) }}</div>
            </td>
          </tr>
        </tbody>
      </table>

      <section v-if="view.run_history?.length" class="tsr-extra" data-testid="tsr-run-history">
        <h4>{{ t('main.test_document.run_history_title') }}</h4>
        <ul><li v-for="run in view.run_history" :key="run.run_id || ''">
          {{ run.run_id }} · {{ run.run_kind }} · {{ run.status }} · {{ run.overall }} · {{ run.basis_id }}
        </li></ul>
      </section>
      <section class="tsr-extra" data-testid="tsr-unmapped">
        <h4>{{ t('main.test_document.unmapped_title', { count: unmapped.length }) }}</h4>
        <p v-if="!unmapped.length" class="tsr-muted">{{ t('main.test_document.none') }}</p>
        <ul v-else>
          <li v-for="(item, idx) in unmapped" :key="idx">
            {{ item.case_id || '-' }} · {{ verdictLabel(item.status) }} · {{ item.source_name || '-' }}
          </li>
        </ul>
      </section>
      <section class="tsr-extra" data-testid="tsr-conflicts">
        <h4>{{ t('main.test_document.conflicts_title', { count: conflicts.length }) }}</h4>
        <p v-if="!conflicts.length" class="tsr-muted">{{ t('main.test_document.none') }}</p>
        <ul v-else>
          <li v-for="item in conflicts" :key="item.case_id">
            {{ item.case_id }}: {{ item.result_count }} → {{ item.statuses.join(', ') }}
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

import type { TestCounts, TestDocumentView, TestResultRecord } from '../../types/testRun'
import { useTestVerdictLabels } from './testVerdict'
import { specErrorKey, specRunState, startSpecRun } from './testSpecRun'

const props = defineProps<{ view: TestDocumentView }>()
const emit = defineEmits<{ changed: [] }>()

const { t } = useI18n()
const { verdictLabel, verdictBadge, modeLabel } = useTestVerdictLabels()

// ── 0684 T#3 (D#1 §6-3): progress, PENDING Cases, a result the current source moved past ──
const runState = computed(() => specRunState(props.view))
const runStateText = computed(() => t(`main.test_document.run_state.${runState.value.key}`,
  { done: runState.value.done, total: runState.value.total }))
const pendingIds = computed(() => new Set(props.view.active_run ? props.view.pending_case_ids ?? [] : []))
const isPending = (caseId: string | null | undefined) => !!caseId && pendingIds.value.has(caseId)
const rerunning = ref(false)
const rerunError = ref('')
// The same admission as the action bar's [다시 실행]: an approved TS whose report is not approved.
const canRerun = computed(() => !!props.view.target_ts && props.view.ts_review_status === 'approved'
  && props.view.doc_review_status !== 'approved')

function short(value: string | null | undefined): string {
  return value ? String(value).slice(0, 12) : ''
}
const fingerprint = computed(() => props.view.basis_source?.fingerprint_prefix
  || short(props.view.source_identity?.content_fingerprint))

async function onRerun() {
  if (!props.view.target_ts) return
  rerunning.value = true
  rerunError.value = ''
  try {
    await startSpecRun(props.view.target_ts)
    emit('changed')
  } catch (error) {
    rerunError.value = t(`main.test_document.errors.${specErrorKey(error)}`)
  } finally {
    rerunning.value = false
  }
}

const report = computed<TestResultRecord | null>(() => {
  const stored = props.view.report ?? null
  const effective = props.view.effective_result
  if (!stored || !effective) return stored
  return {
    ...stored,
    overall: effective.summary.overall,
    summary: effective.summary,
    cases: effective.cases.map((row) => ({
      ...row,
      case_no: row.case_id,
      case_title: row.title,
      expect: row.expected,
      case_status: row.status,
    })),
  }
})
const rows = computed(() => report.value?.cases ?? [])
const unmapped = computed(() => report.value?.unmapped ?? [])
const conflicts = computed(() => report.value?.conflicts ?? [])
// The gate is the server's: `gate.passed` (recomputed from the record) wins over anything.
const gatePassed = computed(() => props.view.gate?.passed ?? report.value?.gate_passed ?? false)
const overallKey = computed(() => String(report.value?.overall ?? 'NOT_RUN').toLowerCase())

const EMPTY: TestCounts = { total: 0, pass: 0, fail: 0, blocked: 0, not_run: 0 }
const counts = computed<TestCounts>(() => report.value?.summary?.counts ?? EMPTY)
const requiredCounts = computed<TestCounts>(() => report.value?.summary?.required_counts ?? EMPTY)

const tiles = computed(() => [
  { key: 'total', label: t('main.test_document.total'), value: counts.value.total },
  { key: 'pass', label: verdictLabel('PASS'), value: counts.value.pass },
  { key: 'fail', label: verdictLabel('FAIL'), value: counts.value.fail },
  { key: 'blocked', label: verdictLabel('BLOCKED'), value: counts.value.blocked },
  { key: 'not_run', label: verdictLabel('NOT_RUN'), value: counts.value.not_run },
])
</script>

<style scoped>
.tsr-overall { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border-radius: var(--r); margin-bottom: 12px; font-size: .875rem; border: 1px solid var(--border); }
.tsr-overall :deep(.app-icon) { font-size: 1.6rem; }
.tsr-overall--pass { background: var(--success-l); color: var(--success); border-color: var(--success); }
.tsr-overall--fail { background: var(--danger-l); color: var(--danger); border-color: var(--danger); }
.tsr-overall--blocked, .tsr-overall--not_run { background: var(--warning-l); color: var(--warning); border-color: var(--warning); }
.tsr-overall-gate { font-size: .78rem; opacity: .9; }
.tsr-tiles { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin-bottom: 8px; }
.tsr-tile { border: 1px solid var(--border); border-radius: var(--r); padding: 8px 10px; text-align: center; background: var(--surface); }
.tsr-tile-n { font-size: 1.25rem; font-weight: 700; }
.tsr-tile-l { font-size: .72rem; color: var(--text-s); }
.tsr-tile--pass .tsr-tile-n { color: var(--success); }
.tsr-tile--fail .tsr-tile-n { color: var(--danger); }
.tsr-tile--blocked .tsr-tile-n, .tsr-tile--not_run .tsr-tile-n { color: var(--warning); }
.tsr-scope { font-size: .75rem; color: var(--text-s); margin: 0 0 12px; }
.tsr-tbl td { vertical-align: top; }
.tsr-case { min-width: 140px; }
.tsr-text { white-space: pre-wrap; word-break: break-word; max-width: 280px; font-size: .78rem; }
.tsr-sub { font-size: .72rem; color: var(--text-m); }
.tsr-warn { color: var(--warning); }
.tsr-row-bad td { background: var(--danger-l); }
.tsr-ev { display: flex; gap: 4px; align-items: baseline; margin-bottom: 2px; }
.tsr-ev .badge { white-space: nowrap; flex: 0 0 auto; }
.tsr-tbl th:nth-child(5), .tsr-tbl td:nth-child(5) { min-width: 180px; }
.tsr-extra { margin-top: 14px; font-size: .8rem; }
.tsr-extra h4 { font-size: .8125rem; margin: 0 0 6px; }
.tsr-extra ul { margin: 0 0 0 18px; padding: 0; }
.tsr-muted { color: var(--text-m); font-size: .8rem; }
.tsr-basis, .tsr-stale { border: 1px solid var(--border); border-radius: var(--r); padding: 10px; margin-bottom: 10px; font-size: .8rem; overflow-wrap: anywhere; }
.tsr-basis-facts { display: inline-flex; flex-wrap: wrap; gap: 4px 14px; margin-left: 8px; color: var(--text-s); }
.tsr-progress { display: flex; align-items: center; gap: 6px; border: 1px solid var(--primary); color: var(--primary); border-radius: var(--r); padding: 8px 12px; margin-bottom: 10px; font-size: .8rem; }
.tsr-stale-band { display: flex; align-items: center; gap: 8px; border: 1px solid var(--warning); background: var(--warning-l); color: var(--warning); border-radius: var(--r); padding: 8px 12px; margin-bottom: 10px; font-size: .8rem; }
.tsr-stale-band span { flex: 1; }
.tsr-unchecked { color: var(--text-s); font-size: .78rem; margin-bottom: 10px; }

.tsr-stale { border-color: var(--warning); }
</style>
