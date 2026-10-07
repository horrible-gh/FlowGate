<template>
  <div class="ts-spec" data-testid="test-spec-panel">
    <!-- A new/empty TS that has not declared a contract yet: start it as a specification. -->
    <div v-if="view.contract_version !== 2" class="ts-spec-start" data-testid="test-spec-start">
      <p class="ts-spec-hint">{{ t('main.test_document.start_hint') }}</p>
      <button type="button" class="btn btn-primary btn-sm" data-testid="test-spec-start-btn" @click="editorOpen = true">
        <AppIcon name="plus" /> {{ t('main.test_document.start_button') }}
      </button>
    </div>
    <template v-else>
      <div class="ts-spec-toolbar">
        <div class="ts-spec-summary">
          <span class="ts-spec-count">{{ t('main.test_document.case_count', { total: cases.length, required: requiredCount }) }}</span>
          <span
            v-if="latest"
            class="badge"
            :class="verdictBadge(view.effective_result?.summary.overall ?? latest.overall)"
            data-testid="test-spec-latest-overall"
          >{{ t('main.test_document.latest_overall', { overall: verdictLabel(view.effective_result?.summary.overall ?? latest.overall) }) }}</span>
          <span v-else class="badge badge-gray">{{ t('main.test_document.no_result') }}</span>
          <span v-if="reportLocked" class="badge badge-green" data-testid="test-spec-report-locked">
            {{ t('main.test_document.report_locked') }}
          </span>
        </div>
        <!-- 0684 T#3 (D#1 §3-8, §6-2): no "run every automated Case" here — that is the action
             bar's [다시 실행]. This panel keeps the per-Case run, result entry for the Cases a
             person records, and the test assets. -->
        <div class="ts-spec-actions">
          <button
            v-if="canEditSpec"
            type="button"
            class="btn btn-secondary btn-sm"
            data-testid="test-spec-edit-btn"
            @click="editorOpen = true"
          >
            <AppIcon name="pencil-simple" /> {{ t('main.test_document.edit') }}
          </button>
          <button
            v-if="canEnterResults"
            type="button"
            class="btn btn-primary btn-sm"
            data-testid="test-spec-results-btn"
            @click="resultsOpen = true"
          >
            <AppIcon name="test-tube" /> {{ t('main.test_document.enter_results') }}
          </button>
        </div>
      </div>

      <div v-if="runState.active" class="ts-progress" data-testid="test-spec-progress">
        <AppIcon name="spinner" spin />
        {{ runStateText }}
      </div>

      <section v-if="view.test_basis" class="ts-basis" data-testid="test-basis">
        <strong>{{ t('main.test_document.basis.title') }}</strong>
        <span class="badge" :class="basisBadge" data-testid="test-basis-verdict">{{ basisVerdictText }}</span>
        <div class="ts-basis-facts">
          <span v-if="fingerprint">{{ t('main.test_document.basis.fingerprint', { value: fingerprint }) }}</span>
          <span v-if="view.basis_source?.git_revision">{{ t('main.test_document.basis.git_revision', { value: shortRevision }) }}</span>
          <span v-if="view.basis_source?.source_dirty != null">
            {{ view.basis_source?.source_dirty ? t('main.test_document.basis.dirty') : t('main.test_document.basis.clean') }}
          </span>
          <span v-if="measuredAt">{{ t('main.test_document.basis.measured_at', { value: measuredAt }) }}</span>
          <span>{{ t('main.test_document.basis.assets', { count: view.test_basis.test_assets.asset_count, hash: short(view.test_basis.test_assets.manifest_hash) }) }}</span>
        </div>
        <div v-if="view.basis_valid === false" class="ts-basis-stale" data-testid="test-basis-stale">
          {{ t('main.test_document.stale_band') }}
          <span v-if="view.basis_verdict?.reasons?.length"> {{ t('main.test_document.reasons', { reasons: view.basis_verdict.reasons.join(', ') }) }}</span>
        </div>
      </section>
      <div v-if="assetNotice" class="ts-basis-stale" role="status" data-testid="test-asset-edited">{{ t('main.test_document.asset_edited') }}</div>
      <div v-if="actionError" class="ts-spec-errors" role="alert" data-testid="test-spec-action-error">{{ actionError }}</div>
      <section v-if="view.test_basis?.manifest?.length" class="ts-assets" data-testid="test-asset-manifest">
        <h4>{{ t('main.test_document.assets_title') }}</h4>
        <ul><li v-for="asset in view.test_basis.manifest" :key="asset.path">
          <button type="button" class="btn btn-secondary btn-sm"
            @click="openAsset(asset.path)">{{ asset.path }}</button>
          <span>{{ asset.role }} · {{ short(asset.content_hash) }}</span>
        </li></ul>
        <div v-if="assetPath" class="ts-asset-editor" data-testid="test-asset-editor">
          <strong>{{ assetPath }}</strong>
          <textarea v-model="assetContent" class="form-ctrl" rows="14" :aria-label="t('main.test_document.asset_content_aria')" />
          <button type="button" class="btn btn-primary btn-sm" data-testid="test-asset-save" :disabled="assetBusy || reportLocked"
            @click="saveAsset">{{ t('main.test_document.asset_save') }}</button>
          <button type="button" class="btn btn-secondary btn-sm" @click="assetPath = ''">{{ t('main.test_document.asset_close') }}</button>
        </div>
      </section>

      <div v-if="errors.length" class="ts-spec-errors" role="alert" data-testid="test-spec-errors">
        <strong><AppIcon name="warning-circle" /> {{ t('main.test_document.errors_title', { count: errors.length }) }}</strong>
        <ul>
          <li v-for="(err, idx) in errors" :key="idx">{{ err.message }}</li>
        </ul>
      </div>

      <p v-if="view.intro" class="ts-spec-intro">{{ view.intro }}</p>
      <p v-if="!cases.length" class="ts-spec-hint">{{ t('main.test_document.no_cases') }}</p>

      <article
        v-for="c in cases"
        :key="c.case_id"
        class="ts-case"
        :data-case-id="c.case_id"
        data-testid="test-spec-case"
      >
        <header class="ts-case-hd">
          <span class="ts-case-id">{{ c.case_id }}</span>
          <span class="ts-case-title">{{ c.title }}</span>
          <span class="badge badge-info">{{ categoryLabel(c.category) }}</span>
          <span class="badge badge-gray">{{ modeLabel(c.execution_mode) }}</span>
          <span class="badge" :class="c.required ? 'badge-blue' : 'badge-gray'">
            {{ c.required ? t('main.test_document.required') : t('main.test_document.optional') }}
          </span>
          <span class="badge badge-gray" data-testid="test-spec-case-capability" :data-capability="capabilityOf(c)">
            {{ capabilityLabel(c) }}
          </span>
          <button v-if="capabilityOf(c) === 'case_selectable'"
            type="button" class="btn btn-secondary btn-sm" :disabled="!canRun"
            data-testid="test-spec-run-case" @click="runCase(c.case_id)">
            <AppIcon :name="runningCase === c.case_id ? 'spinner' : 'play'" :spin="runningCase === c.case_id" />
            {{ t('main.test_document.run_case') }}
          </button>
          <span v-else-if="capabilityOf(c) === 'runner_unsupported'"
            class="ts-case-note" data-testid="test-spec-runner-unsupported">{{ t('main.test_document.capability.runner_unsupported') }}</span>
          <span
            v-if="isPending(c.case_id)"
            class="badge badge-info ts-case-verdict"
            data-testid="test-spec-case-pending"
          ><AppIcon name="spinner" spin /> {{ t('main.test_document.pending') }}</span>
          <span
            v-else-if="resultFor(c.case_id)"
            class="badge ts-case-verdict"
            :class="verdictBadge(resultFor(c.case_id)?.case_status)"
            data-testid="test-spec-case-verdict"
          >{{ verdictLabel(resultFor(c.case_id)?.case_status) }}</span>
        </header>
        <div v-if="effectiveFor(c.case_id)" class="ts-case-effective">
          {{ t('main.test_document.effective', { status: verdictLabel(effectiveFor(c.case_id)?.status) }) }}
          <span v-if="staleFor(c.case_id)"> · {{ t('main.test_document.stale_previous', { status: verdictLabel(staleFor(c.case_id)?.case_status) }) }}</span>
          <div v-for="(e, index) in effectiveFor(c.case_id)?.evidence || []" :key="index">
            {{ e.kind }}: {{ e.value }}
          </div>
        </div>
        <dl class="ts-case-fields">
          <template v-for="field in FIELDS" :key="field">
            <template v-if="c[field]">
              <dt>{{ t(`main.test_document.field.${field}`) }}</dt>
              <dd>{{ c[field] }}</dd>
            </template>
          </template>
        </dl>
      </article>
    </template>

    <TestSpecEditorDialog
      v-if="editorOpen"
      :open="editorOpen"
      :doc-id="docId"
      :title="view.title || view.doc_title || ''"
      :intro="view.intro || ''"
      :cases="cases"
      @close="editorOpen = false"
      @saved="onSaved"
    />
    <TestResultEntryDialog
      v-if="resultsOpen"
      :open="resultsOpen"
      :doc-id="docId"
      :cases="entryCases"
      :latest="latest"
      @close="resultsOpen = false"
      @submitted="onSubmitted"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { getRequest, putRequest } from '@shared/api'

import type { TestDocumentView, TestReportCase, TestSpecCase } from '../../types/testRun'
import TestResultEntryDialog from './TestResultEntryDialog.vue'
import TestSpecEditorDialog from './TestSpecEditorDialog.vue'
import { useTestVerdictLabels } from './testVerdict'
import { specErrorKey, specRunState, startSpecRun } from './testSpecRun'

const props = defineProps<{
  view: TestDocumentView
  docId: string
  readOnly: boolean
  canEdit: boolean
}>()

const emit = defineEmits<{ changed: [] }>()

const { t } = useI18n()
const { verdictLabel, verdictBadge, categoryLabel, modeLabel } = useTestVerdictLabels()

const FIELDS = [
  'requirement', 'precondition', 'input', 'procedure', 'expected', 'check_points', 'automation_ref', 'test_assets',
] as const satisfies readonly (keyof TestSpecCase)[]

const SERVER_RUN = new Set(['case_selectable', 'suite_only'])

const editorOpen = ref(false)
const resultsOpen = ref(false)
const actionError = ref('')
const assetPath = ref('')
const assetContent = ref('')
const assetHash = ref('')
const assetBusy = ref(false)
const assetNotice = ref(false)
const runningCase = ref<string | null>(null)

const cases = computed<TestSpecCase[]>(() => props.view.cases ?? [])
const errors = computed(() => props.view.errors ?? [])
const latest = computed(() => props.view.latest_result ?? null)
const requiredCount = computed(() => cases.value.filter((c) => c.required).length)
const runState = computed(() => specRunState(props.view))
const runStateText = computed(() => t(`main.test_document.run_state.${runState.value.key}`,
  { done: runState.value.done, total: runState.value.total }))

function capabilityOf(c: TestSpecCase): string {
  return props.view.case_capabilities?.[c.case_id] || c.execution_mode || ''
}

function capabilityLabel(c: TestSpecCase): string {
  const capability = capabilityOf(c)
  if (SERVER_RUN.has(capability) || capability === 'runner_unsupported' || capability === 'unbound') {
    return t(`main.test_document.capability.${capability}`)
  }
  return t('main.test_document.capability.entry')
}

// 0684 T#3 (D#1 §3-8, CH S6): [enter results] records manual/external Cases only. Every
// automated Case — one without a server runner too — gets its result from a server run only.
const ENTRY_MODES = new Set(['manual', 'external'])
const entryIds = computed(() => new Set(
  props.view.result_entry_case_ids
    ?? cases.value.filter((c) => ENTRY_MODES.has(c.execution_mode ?? '')).map((c) => c.case_id),
))
const entryCases = computed(() => cases.value.filter((c) => entryIds.value.has(c.case_id)))

// 0684 T#1 (D#1 §3-2): a run is admitted without a stored or valid Basis -- it measures the
// source itself, and a stale result is exactly when a re-run is needed.
const canRun = computed(() => !props.readOnly && !reportLocked.value && runningCase.value === null
  && !runState.value.active && props.view.doc_review_status === 'approved'
  && !errors.value.length)
const effectiveFor = (id: string) => props.view.effective_result?.cases.find((row) => row.case_id === id)
const staleFor = (id: string) => props.view.stale_previous_result?.cases?.find((row) => row.case_no === id)
const pendingIds = computed(() => new Set(props.view.active_run ? props.view.pending_case_ids ?? [] : []))
const isPending = (id: string) => pendingIds.value.has(id)

function short(value: string | null | undefined): string {
  return value ? String(value).slice(0, 12) : ''
}
const fingerprint = computed(() => props.view.basis_source?.fingerprint_prefix
  || short(props.view.test_basis?.source?.content_fingerprint))
const shortRevision = computed(() => short(props.view.basis_source?.git_revision))
const measuredAt = computed(() => props.view.basis_source?.measured_at || props.view.basis_source?.captured_at || '')
const basisState = computed(() => props.view.basis_verdict?.state
  ?? (props.view.basis_valid === false ? 'stale' : props.view.basis_valid ? 'valid' : 'unchecked'))
const basisVerdictText = computed(() => t(`main.test_document.basis.${basisState.value}`))
const BASIS_BADGE: Record<string, string> = {
  valid: 'badge-green', stale: 'badge-yellow', unverifiable: 'badge-red',
}
const basisBadge = computed(() => BASIS_BADGE[basisState.value] ?? 'badge-gray')

async function runCase(caseId: string) {
  actionError.value = ''
  runningCase.value = caseId
  try {
    await startSpecRun(props.docId, caseId)
    emit('changed')
  } catch (error) {
    actionError.value = t(`main.test_document.errors.${specErrorKey(error)}`)
  } finally {
    runningCase.value = null
  }
}

async function openAsset(path: string) {
  actionError.value = ''
  try {
    const res = await getRequest<{ content: string; content_hash: string }>(
      '/api/v1/documents/' + encodeURIComponent(props.docId)
      + '/test-spec/assets/' + encodeURIComponent(path))
    assetPath.value = path
    assetContent.value = res.data.content
    assetHash.value = res.data.content_hash
  } catch (error) {
    actionError.value = t(`main.test_document.errors.${specErrorKey(error)}`)
  }
}

async function saveAsset() {
  assetBusy.value = true
  actionError.value = ''
  try {
    await putRequest('/api/v1/documents/' + encodeURIComponent(props.docId)
      + '/test-spec/assets/' + encodeURIComponent(assetPath.value),
      { expected_hash: assetHash.value, content: assetContent.value })
    assetPath.value = ''
    // D#1 §3-7 / §6-2: an edit starts no run; the result is stale until the next one.
    assetNotice.value = true
    emit('changed')
  } catch (error) {
    actionError.value = t(`main.test_document.errors.${specErrorKey(error)}`)
  } finally {
    assetBusy.value = false
  }
}

// The spec is the approved test basis: it is edited before approval, not after.
const canEditSpec = computed(
  () => props.canEdit && !props.readOnly && props.view.doc_review_status !== 'approved',
)
// Results are recorded against the APPROVED spec (the server enforces the same rule).
// An approved test report is the record the workflow moved on with; the server refuses new
// results under it (tsr_already_approved), so the entry point is replaced by that fact.
const reportLocked = computed(() => props.view.tsr_review_status === 'approved')
const canEnterResults = computed(
  () => !props.readOnly && !errors.value.length && entryCases.value.length > 0 && !reportLocked.value
    && ['approved', 'pending_review', 'revised'].includes(props.view.doc_review_status ?? ''),
)

const resultByCase = computed(() => {
  const map = new Map<string, TestReportCase>()
  for (const row of latest.value?.cases ?? []) {
    if (row.case_no) map.set(row.case_no, row)
  }
  return map
})

function resultFor(caseId: string): TestReportCase | undefined {
  return resultByCase.value.get(caseId)
}

function onSaved() {
  editorOpen.value = false
  emit('changed')
}

function onSubmitted() {
  resultsOpen.value = false
  emit('changed')
}
</script>

<style scoped>
.ts-spec-toolbar { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px; flex-wrap: wrap; }
.ts-spec-summary { display: flex; align-items: center; gap: 8px; }
.ts-spec-count { font-size: .8125rem; color: var(--text-s); }
.ts-spec-actions { display: flex; gap: 6px; }
.ts-spec-hint { font-size: .8125rem; color: var(--text-m); margin: 0 0 10px; }
.ts-spec-start { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.ts-spec-intro { font-size: .8125rem; color: var(--text); white-space: pre-wrap; margin: 0 0 12px; }
.ts-spec-errors { border: 1px solid var(--danger); background: var(--danger-l); color: var(--danger); border-radius: var(--r); padding: 10px 14px; margin-bottom: 14px; font-size: .8rem; }
.ts-spec-errors ul { margin: 6px 0 0 18px; padding: 0; }
.ts-progress { display: flex; align-items: center; gap: 6px; border: 1px solid var(--primary); color: var(--primary); border-radius: var(--r); padding: 8px 12px; margin-bottom: 10px; font-size: .8rem; }
.ts-case { border: 1px solid var(--border); border-radius: var(--r); padding: 12px 14px; margin-bottom: 10px; background: var(--surface); }
.ts-case-hd { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
.ts-case-id { font-family: var(--mono, monospace); font-weight: 700; font-size: .8125rem; }
.ts-case-title { font-weight: 600; font-size: .8125rem; margin-right: 4px; }
.ts-case-verdict { margin-left: auto; }
.ts-case-fields { display: grid; grid-template-columns: max-content 1fr; gap: 4px 14px; margin: 0; font-size: .8rem; }
.ts-case-fields dt { color: var(--text-s); font-weight: 500; }
.ts-case-fields dd { margin: 0; white-space: pre-wrap; word-break: break-word; }
.ts-basis, .ts-assets { border: 1px solid var(--border); border-radius: var(--r); padding: 10px; margin: 10px 0; font-size: .8rem; }
.ts-basis strong { margin-right: 6px; }
.ts-basis-facts { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 6px; color: var(--text-s); overflow-wrap: anywhere; }
.ts-basis-stale { color: var(--warning); margin: 6px 0; font-size: .8rem; }
.ts-assets ul { padding-left: 18px; }
.ts-assets li { margin: 4px 0; overflow-wrap: anywhere; }
.ts-assets li span { margin-left: 8px; color: var(--text-s); }
.ts-asset-editor textarea { width: 100%; margin: 8px 0; font-family: monospace; }
.ts-case-note, .ts-case-effective { font-size: .75rem; color: var(--text-s); }
</style>
