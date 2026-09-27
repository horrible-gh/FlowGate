<template>
  <DialogShell ref="shellRef" :open="open" variant="workflow-large" :busy="submitting" @request-close="onCancel">
    <template #header>
      <DialogHeader :title="t('main.test_document.results.title')" @close="shellRef?.requestClose('header')">
        <template #icon><AppIcon name="test-tube" style="color:var(--primary);" /></template>
      </DialogHeader>
    </template>
    <div class="tre" data-testid="test-result-entry">
      <p class="form-hint tre-hint">{{ t('main.test_document.results.hint') }}</p>

      <div v-if="errorText" class="tre-error" role="alert" data-testid="tre-error">{{ errorText }}</div>

      <table class="tbl tre-tbl">
        <thead>
          <tr>
            <th>{{ t('main.test_document.results.col_case') }}</th>
            <th>{{ t('main.test_document.results.col_status') }}</th>
            <th>{{ t('main.test_document.field.actual') }}</th>
            <th>{{ t('main.test_document.field.evidence') }}</th>
            <th>{{ t('main.test_document.field.defect_ref') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.case_id" data-testid="tre-row" :data-case-id="row.case_id">
            <td class="tre-case">
              <strong>{{ row.case_id }}</strong>
              <div class="tre-sub">{{ row.title }}</div>
              <div class="tre-sub">
                {{ modeLabel(row.mode) }} · {{ row.required ? t('main.test_document.required') : t('main.test_document.optional') }}
              </div>
              <div v-if="row.previous" class="tre-sub">
                {{ t('main.test_document.results.previous', { verdict: verdictLabel(row.previous) }) }}
              </div>
            </td>
            <td>
              <select v-model="row.status" class="form-ctrl" data-testid="tre-status">
                <option value="">{{ t('main.test_document.results.status_unset') }}</option>
                <option v-for="v in TEST_VERDICTS" :key="v" :value="v">{{ verdictLabel(v) }}</option>
              </select>
            </td>
            <td><textarea v-model="row.actual" class="form-ctrl" rows="2" data-testid="tre-actual" /></td>
            <td>
              <textarea v-model="row.evidence" class="form-ctrl" rows="2"
                :placeholder="t('main.test_document.results.evidence_placeholder')" data-testid="tre-evidence" />
            </td>
            <td><input v-model="row.defect_ref" class="form-ctrl" type="text" data-testid="tre-defect" /></td>
          </tr>
        </tbody>
      </table>

      <div class="form-group tre-junit">
        <label class="form-label" for="tre-junit">{{ t('main.test_document.results.junit_label') }}</label>
        <input id="tre-junit" type="file" accept=".xml,text/xml,application/xml" data-testid="tre-junit" @change="onJunitSelected" />
        <div v-if="junitName" class="form-hint" data-testid="tre-junit-loaded">
          {{ t('main.test_document.results.junit_loaded', { name: junitName }) }}
        </div>
      </div>
      <label class="tre-check">
        <input v-model="replace" type="checkbox" data-testid="tre-replace" />
        {{ t('main.test_document.results.replace') }}
      </label>
    </div>
    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { postRequest } from '@shared/api'

import DialogFooter from '../dialogs/DialogFooter.vue'
import DialogHeader from '../dialogs/DialogHeader.vue'
import DialogShell from '../dialogs/DialogShell.vue'
import type { DialogAction } from '../dialogs/dialogTypes'
import type { TestResultRecord, TestSpecCase } from '../../types/testRun'
import { TEST_VERDICTS, useTestVerdictLabels } from './testVerdict'

const props = defineProps<{
  open: boolean
  docId: string
  cases: TestSpecCase[]
  latest: TestResultRecord | null
}>()

const emit = defineEmits<{ close: []; submitted: [overall: string | null] }>()

const { t } = useI18n()
const { verdictLabel, modeLabel } = useTestVerdictLabels()

interface Row {
  case_id: string
  title: string
  mode: string | null
  required: boolean
  previous: string | null
  status: string
  actual: string
  evidence: string
  defect_ref: string
}

const previousByCase = new Map<string, string>()
for (const c of props.latest?.cases ?? []) {
  if (c.case_no && c.case_status && c.result_origin !== 'missing') previousByCase.set(c.case_no, c.case_status)
}

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)
const rows = ref<Row[]>(props.cases.map((c) => ({
  case_id: c.case_id,
  title: c.title,
  mode: c.execution_mode,
  required: c.required,
  previous: previousByCase.get(c.case_id) ?? null,
  status: '',
  actual: '',
  evidence: '',
  defect_ref: '',
})))
const junitText = ref<string | null>(null)
const junitName = ref('')
const replace = ref(false)
const submitting = ref(false)
const errorText = ref('')

async function onJunitSelected(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) {
    junitText.value = null
    junitName.value = ''
    return
  }
  junitText.value = await file.text()
  junitName.value = file.name
}

function evidenceItems(text: string) {
  return text
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => ({ kind: /^https?:\/\//i.test(line) ? 'url' : 'manual_note', value: line }))
}

const filled = computed(() => rows.value.filter((row) => row.status))

async function submit() {
  if (submitting.value) return
  if (!filled.value.length && !junitText.value) {
    errorText.value = t('main.test_document.results.no_input')
    return
  }
  submitting.value = true
  errorText.value = ''
  try {
    const res = await postRequest<{ overall?: string | null }>('/api/v1/documents/test-results', {
      doc_id: props.docId,
      replace: replace.value,
      results: filled.value.map((row) => ({
        case_id: row.case_id,
        status: row.status,
        actual: row.actual,
        evidence: evidenceItems(row.evidence),
        defect_ref: row.defect_ref,
        execution_mode: row.mode,
      })),
      junit_xml: junitText.value ?? undefined,
    })
    emit('submitted', res.data?.overall ?? null)
  } catch (e: any) {
    const data = e?.response?.data
    errorText.value = data?.detail || data?.error_message || data?.error || t('main.test_document.results.submit_failed')
  } finally {
    submitting.value = false
  }
}

function onCancel() {
  if (submitting.value) return
  emit('close')
}

const actions = computed<DialogAction[]>(() => [
  { id: 'cancel', label: t('common.cancel'), role: 'cancel', onSelect: onCancel },
  {
    id: 'submit',
    label: t('main.test_document.results.submit'),
    role: 'primary',
    loading: submitting.value,
    disabled: submitting.value,
    onSelect: submit,
  },
])
</script>

<style scoped>
.tre { padding: 16px 20px; }
.tre-hint { margin: 0 0 12px; }
.tre-tbl td { vertical-align: top; }
.tre-case { min-width: 160px; }
.tre-sub { font-size: .72rem; color: var(--text-m); }
.tre-junit { margin-top: 14px; }
.tre-check { display: inline-flex; align-items: center; gap: 6px; font-size: .8125rem; }
.tre-error { border: 1px solid var(--danger); background: var(--danger-l); color: var(--danger); border-radius: var(--r); padding: 10px 14px; margin-bottom: 12px; font-size: .8rem; }
</style>
