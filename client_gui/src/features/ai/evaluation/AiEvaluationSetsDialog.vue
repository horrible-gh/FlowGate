<template>
  <ModalShell :open="open" size="lg" @close="onClose">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">{{ t('gui.ai.evaluation.sets_title') }}</h2>
      </div>
    </template>

    <div class="sets-body">
      <aside class="sets-index">
        <button
          v-for="item in sets"
          :key="item.set_id"
          type="button"
          class="sets-item"
          :class="{ active: editingId === item.set_id }"
          @click="edit(item)"
        >
          {{ item.name }}
        </button>
        <div v-if="!sets.length && !loading" class="muted">{{ t('gui.ai.evaluation.sets_empty') }}</div>
        <Button variant="ghost" size="sm" :disabled="saving" @click="edit(null)">＋ {{ t('gui.ai.evaluation.set_add') }}</Button>
      </aside>

      <div class="sets-form">
        <div v-if="error" class="form-error" role="alert">
          <div>{{ error }}</div>
          <ul v-if="errorDetails.length">
            <li v-for="(line, i) in errorDetails" :key="i">{{ line }}</li>
          </ul>
        </div>

        <label>
          <span class="field-head">
            <span>{{ t('gui.ai.evaluation.set_name') }} *</span>
            <small class="counter" :class="{ over: overLimit(form.name, LIMITS.nameMax) }">
              {{ t('gui.ai.evaluation.set_count', { count: length(form.name), max: LIMITS.nameMax }) }}
            </small>
          </span>
          <Input v-model="form.name" :invalid="!!fieldError('name')" :disabled="saving" />
          <small v-if="fieldError('name')" class="field-error">{{ fieldError('name') }}</small>
        </label>

        <label>
          <span class="field-head">
            <span>{{ t('gui.ai.evaluation.set_instruction') }} *</span>
            <small class="counter" :class="{ over: overLimit(form.instruction, LIMITS.instructionMax) }">
              {{ t('gui.ai.evaluation.set_count', { count: length(form.instruction), max: LIMITS.instructionMax }) }}
            </small>
          </span>
          <Textarea v-model="form.instruction" :rows="7" :invalid="!!fieldError('instruction')" :disabled="saving" />
          <small v-if="fieldError('instruction')" class="field-error">{{ fieldError('instruction') }}</small>
        </label>

        <!-- Each constraint is its own entry (it may span several lines). Blank entries are not saved. -->
        <div class="constraints">
          <div class="field-head">
            <span>{{ t('gui.ai.evaluation.set_constraints') }}</span>
            <small class="counter" :class="{ over: filledCount > LIMITS.constraintsMax }">
              {{ t('gui.ai.evaluation.set_count', { count: filledCount, max: LIMITS.constraintsMax }) }}
            </small>
          </div>
          <small class="muted">{{ t('gui.ai.evaluation.set_constraints_hint') }}</small>
          <div v-for="(_item, index) in form.constraints" :key="index" class="constraint-row">
            <span class="constraint-no">{{ index + 1 }}</span>
            <div class="constraint-field">
              <Textarea
                v-model="form.constraints[index]"
                :rows="2"
                :placeholder="index === 0 ? t('gui.ai.evaluation.set_constraints_placeholder') : undefined"
                :invalid="!!constraintError(index)"
                :disabled="saving"
              />
              <small v-if="constraintError(index)" class="field-error">{{ constraintError(index) }}</small>
              <small
                v-else-if="length(form.constraints[index]) > LIMITS.constraintMax * 0.9"
                class="counter"
              >
                {{ t('gui.ai.evaluation.set_count', { count: length(form.constraints[index]), max: LIMITS.constraintMax }) }}
              </small>
            </div>
            <IconButton
              :label="t('gui.ai.evaluation.set_constraint_remove', { n: index + 1 })"
              :title="t('gui.ai.evaluation.set_constraint_remove', { n: index + 1 })"
              size="sm"
              :disabled="saving"
              @click="removeConstraint(index)"
            >
              <AiIcon name="trash" />
            </IconButton>
          </div>
          <small v-if="fieldError('constraints')" class="field-error">{{ fieldError('constraints') }}</small>
          <div>
            <Button
              variant="ghost"
              size="sm"
              :disabled="saving || form.constraints.length >= LIMITS.constraintsMax"
              @click="addConstraint"
            >
              ＋ {{ t('gui.ai.evaluation.set_constraint_add') }}
            </Button>
          </div>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-actions">
        <Button v-if="editingId" variant="danger" :disabled="saving" @click="remove">{{ t('gui.common.delete') }}</Button>
        <span class="spacer" />
        <Button variant="secondary" :disabled="saving" @click="onClose">{{ t('gui.common.close') }}</Button>
        <Button variant="primary" :loading="saving" :disabled="saving || hasLimitError" @click="save">{{ t('gui.common.save') }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Evaluation sets: a fixed task (instruction + constraints) so several AIs can run the same task.
// The dialog checks the same limits as the server (../limits.ts, flowgate.v02.0017 T0004 §5)
// before saving, and shows which field the server rejected and why when it still does.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Input from '@/components/ui/Input.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Textarea from '@/components/ui/Textarea.vue'
import AiIcon from '../AiIcon.vue'
import { AiRequestError, deleteEvaluationSet, listEvaluationSets, saveEvaluationSet, type AiFieldError } from '../api'
import { EVALUATION_SET_LIMITS as LIMITS } from '../limits'
import type { AiEvaluationSet } from '../types'

type SetField = 'name' | 'instruction' | 'constraints'
interface SetErrors {
  fields: Partial<Record<SetField, string>>
  constraints: Record<number, string>
}

const props = defineProps<{ open: boolean; projectId: string }>()
const emit = defineEmits<{ close: []; changed: [sets: AiEvaluationSet[]] }>()
const { t } = useI18n()

const sets = ref<AiEvaluationSet[]>([])
const editingId = ref<string | null>(null)
const form = reactive<{ name: string; instruction: string; constraints: string[] }>({
  name: '',
  instruction: '',
  constraints: [''],
})
const loading = ref(false)
const saving = ref(false)
const error = ref('')
const errorDetails = ref<string[]>([])
// Required-field messages wait for the first save attempt; length/count messages show at once.
const submitted = ref(false)
// Reasons the server gave for the last save, keyed like the client-side checks.
const serverErrors = ref<SetErrors>({ fields: {}, constraints: {} })

// Lengths are counted on the trimmed value, as the server does.
const length = (value: string | undefined) => (value ?? '').trim().length
const overLimit = (value: string | undefined, max: number) => length(value) > max
const filledCount = computed(() => form.constraints.filter((item) => item.trim()).length)

function reasonText(reason: string, max?: number): string {
  return t(`gui.ai.evaluation.set_errors.${reason}`, { max: max ?? '' })
}

function textError(value: string, max: number): string | null {
  if (!value.trim()) return submitted.value ? reasonText('required') : null
  if (value.trim().length > max) return reasonText('too_long', max)
  return null
}

const clientErrors = computed<SetErrors>(() => {
  const fields: SetErrors['fields'] = {}
  const constraints: SetErrors['constraints'] = {}
  const name = textError(form.name, LIMITS.nameMax)
  if (name) fields.name = name
  const instruction = textError(form.instruction, LIMITS.instructionMax)
  if (instruction) fields.instruction = instruction
  form.constraints.forEach((item, index) => {
    if (item.trim().length > LIMITS.constraintMax) constraints[index] = reasonText('too_long', LIMITS.constraintMax)
  })
  if (filledCount.value > LIMITS.constraintsMax) fields.constraints = reasonText('too_many', LIMITS.constraintsMax)
  return { fields, constraints }
})

// Length/count problems block saving outright; an empty required field is reported on save.
const hasLimitError = computed(() =>
  overLimit(form.name, LIMITS.nameMax) ||
  overLimit(form.instruction, LIMITS.instructionMax) ||
  Object.keys(clientErrors.value.constraints).length > 0 ||
  filledCount.value > LIMITS.constraintsMax
)

function fieldError(field: SetField): string | null {
  return clientErrors.value.fields[field] ?? serverErrors.value.fields[field] ?? null
}

function constraintError(index: number): string | null {
  return clientErrors.value.constraints[index] ?? serverErrors.value.constraints[index] ?? null
}

function resetErrors(): void {
  error.value = ''
  errorDetails.value = []
  serverErrors.value = { fields: {}, constraints: {} }
}

function edit(item: AiEvaluationSet | null): void {
  resetErrors()
  submitted.value = false
  editingId.value = item?.set_id ?? null
  form.name = item?.name ?? ''
  form.instruction = item?.instruction ?? ''
  form.constraints = item?.constraints?.length ? [...item.constraints] : ['']
}

function addConstraint(): void {
  if (form.constraints.length < LIMITS.constraintsMax) form.constraints.push('')
}

function removeConstraint(index: number): void {
  form.constraints.splice(index, 1)
  if (!form.constraints.length) form.constraints.push('')
  // Row numbers shifted, so a server reason can no longer be pinned to a row.
  serverErrors.value = { ...serverErrors.value, constraints: {} }
}

async function load(): Promise<void> {
  loading.value = true
  try {
    sets.value = await listEvaluationSets(props.projectId)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    loading.value = false
  }
}

function fieldLabel(item: AiFieldError, rows: number[]): string {
  if (item.field === 'constraints' && item.index !== undefined) {
    return t('gui.ai.evaluation.set_constraint_n', { n: (rows[item.index] ?? item.index) + 1 })
  }
  if (item.field === 'name') return t('gui.ai.evaluation.set_name')
  if (item.field === 'instruction') return t('gui.ai.evaluation.set_instruction')
  return t('gui.ai.evaluation.set_constraints')
}

// Server indexes count the constraints that were sent (blank rows are not sent); `rows` maps
// each one back to its row on screen.
function applyServerErrors(fieldErrors: AiFieldError[], rows: number[]): void {
  const next: SetErrors = { fields: {}, constraints: {} }
  for (const item of fieldErrors) {
    const text = reasonText(item.reason, item.max)
    if (item.field === 'constraints' && item.index !== undefined) {
      next.constraints[rows[item.index] ?? item.index] = text
    } else if (item.field === 'name' || item.field === 'instruction' || item.field === 'constraints') {
      next.fields[item.field] = text
    }
  }
  serverErrors.value = next
  errorDetails.value = fieldErrors.map((item) => `${fieldLabel(item, rows)}: ${reasonText(item.reason, item.max)}`)
}

async function save(): Promise<void> {
  submitted.value = true
  resetErrors()
  if (hasLimitError.value || clientErrors.value.fields.name || clientErrors.value.fields.instruction) return
  const rows: number[] = []
  const constraints: string[] = []
  form.constraints.forEach((item, index) => {
    if (!item.trim()) return
    rows.push(index)
    constraints.push(item.trim())
  })
  saving.value = true
  try {
    const saved = await saveEvaluationSet(props.projectId, editingId.value, {
      name: form.name.trim(),
      instruction: form.instruction.trim(),
      constraints,
    })
    await load()
    emit('changed', sets.value)
    // Show what the server stored (the re-read row), not what was typed.
    edit(sets.value.find((item) => item.set_id === saved.set_id) ?? saved)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
    if (err instanceof AiRequestError && err.fieldErrors.length) applyServerErrors(err.fieldErrors, rows)
  } finally {
    saving.value = false
  }
}

async function remove(): Promise<void> {
  const id = editingId.value
  if (!id) return
  saving.value = true
  resetErrors()
  try {
    await deleteEvaluationSet(props.projectId, id)
    await load()
    emit('changed', sets.value)
    edit(null)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    saving.value = false
  }
}

function onClose(): void {
  if (!saving.value) emit('close')
}

watch(() => props.open, (open) => {
  if (!open) return
  edit(null)
  void load()
}, { immediate: true })
</script>

<style scoped>
.dialog-header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  margin: 0;
  font-size: 17px;
  font-weight: 800;
  color: var(--fg2-text);
}

.sets-body {
  display: grid;
  grid-template-columns: 200px 1fr;
  gap: 16px;
  padding: 16px 20px;
  min-height: 320px;
}

.sets-index {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-right: 1px solid var(--fg2-line);
  padding-right: 12px;
}

.sets-item {
  text-align: left;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: transparent;
  color: var(--fg2-text);
  cursor: pointer;
  font-size: 13px;
}

.sets-item:hover,
.sets-item.active {
  background: rgba(40, 110, 201, 0.14);
  border-color: rgba(40, 110, 201, 0.4);
}

.sets-form {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.sets-form label {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.muted {
  font-size: 12px;
  color: var(--fg2-muted);
}

.form-error {
  padding: 8px 12px;
  border-radius: 6px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 12px;
}

.dialog-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--fg2-line);
}

.spacer {
  flex: 1;
}

.sets-form {
  max-height: 64vh;
  overflow-y: auto;
  padding-right: 4px;
}

.field-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 8px;
}

.counter {
  font-size: 11px;
  font-weight: 500;
  color: var(--fg2-muted);
}

.counter.over,
.field-error {
  color: #fca5a5;
}

.field-error {
  font-size: 11.5px;
  font-weight: 500;
}

.form-error ul {
  margin: 4px 0 0;
  padding-left: 18px;
}

.constraints {
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 650;
  color: var(--fg2-muted);
}

.constraint-row {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr) auto;
  gap: 8px;
  align-items: start;
}

.constraint-no {
  padding-top: 10px;
  font-size: 11.5px;
  text-align: right;
}

.constraint-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}
</style>
