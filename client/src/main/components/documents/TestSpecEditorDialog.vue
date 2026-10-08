<template>
  <DialogShell ref="shellRef" :open="open" variant="workflow-large" :busy="saving" @request-close="onCancel">
    <template #header>
      <DialogHeader :title="t('main.test_document.editor.title')" @close="shellRef?.requestClose('header')">
        <template #icon><AppIcon name="list-checks" style="color:var(--primary);" /></template>
      </DialogHeader>
    </template>
    <div class="tse" data-testid="test-spec-editor">
      <div class="form-group">
        <label class="form-label req" for="tse-title">{{ t('main.test_document.editor.doc_title') }}</label>
        <input id="tse-title" v-model="title" class="form-ctrl" type="text" data-testid="tse-title" />
      </div>
      <div class="form-group">
        <label class="form-label" for="tse-intro">{{ t('main.test_document.editor.intro') }}</label>
        <textarea id="tse-intro" v-model="intro" class="form-ctrl" rows="2" data-testid="tse-intro" />
      </div>

      <div v-if="serverErrors.length" class="tse-errors" role="alert" data-testid="tse-errors">
        <strong>{{ t('main.test_document.editor.validation_failed') }}</strong>
        <ul><li v-for="(err, idx) in serverErrors" :key="idx">{{ err }}</li></ul>
      </div>

      <section
        v-for="(c, index) in draft"
        :key="c.key"
        class="tse-case"
        data-testid="tse-case"
      >
        <div class="tse-case-hd">
          <strong>{{ t('main.test_document.editor.case_n', { n: index + 1 }) }}</strong>
          <div class="tse-case-tools">
            <button type="button" class="btn btn-secondary btn-sm btn-icon" :disabled="index === 0"
              :title="t('main.test_document.editor.move_up')" :aria-label="t('main.test_document.editor.move_up')"
              data-testid="tse-move-up" @click="move(index, -1)">
              <AppIcon name="arrow-up" />
            </button>
            <button type="button" class="btn btn-secondary btn-sm btn-icon" :disabled="index === draft.length - 1"
              :title="t('main.test_document.editor.move_down')" :aria-label="t('main.test_document.editor.move_down')"
              data-testid="tse-move-down" @click="move(index, 1)">
              <AppIcon name="arrow-down" />
            </button>
            <button type="button" class="btn btn-secondary btn-sm btn-icon"
              :title="t('main.test_document.editor.remove')" :aria-label="t('main.test_document.editor.remove')"
              data-testid="tse-remove" @click="remove(index)">
              <AppIcon name="trash" />
            </button>
          </div>
        </div>
        <div class="form-row-3">
          <div class="form-group">
            <label class="form-label req">{{ t('main.test_document.editor.case_id') }}</label>
            <input v-model="c.case_id" class="form-ctrl" type="text" data-testid="tse-case-id" />
          </div>
          <div class="form-group tse-span-2">
            <label class="form-label req">{{ t('main.test_document.editor.case_title') }}</label>
            <input v-model="c.title" class="form-ctrl" type="text" data-testid="tse-case-title" />
          </div>
        </div>
        <div class="form-row-3">
          <div class="form-group">
            <label class="form-label req">{{ t('main.test_document.editor.category') }}</label>
            <select v-model="c.category" class="form-ctrl" data-testid="tse-category">
              <option v-for="cat in TEST_CATEGORIES" :key="cat" :value="cat">{{ categoryLabel(cat) }}</option>
            </select>
          </div>
          <div class="form-group">
            <label class="form-label req">{{ t('main.test_document.editor.execution_mode') }}</label>
            <select v-model="c.execution_mode" class="form-ctrl" data-testid="tse-mode">
              <option v-for="m in TEST_EXECUTION_MODES" :key="m" :value="m">{{ modeLabel(m) }}</option>
            </select>
          </div>
          <div class="form-group tse-required">
            <label class="form-label">{{ t('main.test_document.editor.required') }}</label>
            <label class="tse-check">
              <input v-model="c.required" type="checkbox" data-testid="tse-required" />
              {{ c.required ? t('main.test_document.required') : t('main.test_document.optional') }}
            </label>
          </div>
        </div>
        <div v-for="field in TEXT_FIELDS" :key="field" class="form-group">
          <label class="form-label" :class="{ req: REQUIRED_TEXT.includes(field) }">
            {{ t(`main.test_document.field.${field}`) }}
          </label>
          <textarea v-model="c[field]" class="form-ctrl" :rows="field === 'procedure' || field === 'expected' ? 3 : 1"
            :data-testid="`tse-${field}`" />
        </div>
      </section>

      <button type="button" class="btn btn-secondary btn-sm" data-testid="tse-add" @click="add">
        <AppIcon name="plus" /> {{ t('main.test_document.editor.add_case') }}
      </button>
      <p class="form-hint">{{ t('main.test_document.editor.hint') }}</p>
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
import { putRequest } from '@shared/api'

import DialogFooter from '../dialogs/DialogFooter.vue'
import DialogHeader from '../dialogs/DialogHeader.vue'
import DialogShell from '../dialogs/DialogShell.vue'
import type { DialogAction } from '../dialogs/dialogTypes'
import type { TestSpecCase } from '../../types/testRun'
import { TEST_CATEGORIES, TEST_EXECUTION_MODES, useTestVerdictLabels } from './testVerdict'

const props = defineProps<{
  open: boolean
  docId: string
  title: string
  intro: string
  cases: TestSpecCase[]
}>()

const emit = defineEmits<{ close: []; saved: [] }>()

const { t, locale } = useI18n()
const { categoryLabel, modeLabel } = useTestVerdictLabels()

const TEXT_FIELDS = [
  'requirement', 'precondition', 'input', 'procedure', 'expected', 'check_points', 'automation_ref', 'test_assets',
] as const
const REQUIRED_TEXT: readonly string[] = ['requirement', 'procedure', 'expected', 'check_points']

type DraftCase = TestSpecCase & { key: number }

let keySeq = 0
function toDraft(c: Partial<TestSpecCase>): DraftCase {
  keySeq += 1
  return {
    key: keySeq,
    case_id: c.case_id ?? '',
    title: c.title ?? '',
    category: c.category || 'normal',
    requirement: c.requirement ?? '',
    execution_mode: c.execution_mode || 'automated',
    required: c.required ?? true,
    precondition: c.precondition ?? '',
    input: c.input ?? '',
    procedure: c.procedure ?? '',
    expected: c.expected ?? '',
    check_points: c.check_points ?? '',
    automation_ref: c.automation_ref ?? '',
    test_assets: c.test_assets ?? '',
  }
}

const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)
const title = ref(props.title)
const intro = ref(props.intro)
const draft = ref<DraftCase[]>(props.cases.length ? props.cases.map(toDraft) : [toDraft({ case_id: 'TC-001' })])
const saving = ref(false)
const serverErrors = ref<string[]>([])

function nextCaseId(): string {
  const used = new Set(draft.value.map((c) => c.case_id.toUpperCase()))
  for (let n = draft.value.length + 1; n < 10000; n += 1) {
    const candidate = `TC-${String(n).padStart(3, '0')}`
    if (!used.has(candidate)) return candidate
  }
  return ''
}

function add() {
  draft.value.push(toDraft({ case_id: nextCaseId() }))
}

function remove(index: number) {
  draft.value.splice(index, 1)
}

function move(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= draft.value.length) return
  const list = draft.value.slice()
  const [item] = list.splice(index, 1)
  list.splice(target, 0, item)
  draft.value = list
}

async function save() {
  if (saving.value) return
  saving.value = true
  serverErrors.value = []
  try {
    await putRequest(`/api/v1/documents/${encodeURIComponent(props.docId)}/test-spec`, {
      title: title.value,
      intro: intro.value,
      locale: String(locale.value || 'ko'),
      cases: draft.value.map(({ key: _key, ...rest }) => rest),
    })
    emit('saved')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    const list = Array.isArray(detail?.errors)
      ? detail.errors.map((err: { message?: string }) => err?.message ?? '')
      : []
    serverErrors.value = list.length
      ? list
      : [typeof detail === 'string' ? detail : detail?.message ?? t('main.test_document.editor.save_failed')]
  } finally {
    saving.value = false
  }
}

function onCancel() {
  if (saving.value) return
  emit('close')
}

const actions = computed<DialogAction[]>(() => [
  { id: 'cancel', label: t('common.cancel'), role: 'cancel', onSelect: onCancel },
  {
    id: 'save',
    label: t('main.test_document.editor.save'),
    role: 'primary',
    loading: saving.value,
    disabled: saving.value || !draft.value.length || !title.value.trim(),
    onSelect: save,
  },
])
</script>

<style scoped>
.tse { padding: 16px 20px; }
.tse-case { border: 1px solid var(--border); border-radius: var(--r); padding: 12px 14px 4px; margin-bottom: 12px; }
.tse-case-hd { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.tse-case-tools { display: flex; gap: 4px; }
.tse-span-2 { grid-column: span 2; }
.tse-check { display: inline-flex; align-items: center; gap: 6px; font-size: .8125rem; }
.tse-errors { border: 1px solid var(--danger); background: var(--danger-l); color: var(--danger); border-radius: var(--r); padding: 10px 14px; margin-bottom: 14px; font-size: .8rem; }
.tse-errors ul { margin: 6px 0 0 18px; padding: 0; }
</style>
