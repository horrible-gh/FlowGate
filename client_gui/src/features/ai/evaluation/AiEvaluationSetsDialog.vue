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
        <div v-if="error" class="form-error" role="alert">{{ error }}</div>
        <label>
          <span>{{ t('gui.ai.evaluation.set_name') }} *</span>
          <Input v-model="form.name" maxlength="200" :disabled="saving" />
        </label>
        <label>
          <span>{{ t('gui.ai.evaluation.set_instruction') }} *</span>
          <Textarea v-model="form.instruction" :rows="7" maxlength="12000" :disabled="saving" />
        </label>
        <label>
          <span>{{ t('gui.ai.evaluation.set_constraints') }}</span>
          <Textarea v-model="form.constraints" :rows="4" :placeholder="t('gui.ai.evaluation.set_constraints_placeholder')" :disabled="saving" />
        </label>
      </div>
    </div>

    <template #footer>
      <div class="dialog-actions">
        <Button v-if="editingId" variant="danger" :disabled="saving" @click="remove">{{ t('gui.common.delete') }}</Button>
        <span class="spacer" />
        <Button variant="secondary" :disabled="saving" @click="onClose">{{ t('gui.common.close') }}</Button>
        <Button variant="primary" :loading="saving" :disabled="!isValid" @click="save">{{ t('gui.common.save') }}</Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Evaluation sets: a fixed task (instruction + constraints) so several AIs can run the same task.
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { deleteEvaluationSet, listEvaluationSets, saveEvaluationSet } from '../api'
import type { AiEvaluationSet } from '../types'

const props = defineProps<{ open: boolean; projectId: string }>()
const emit = defineEmits<{ close: []; changed: [sets: AiEvaluationSet[]] }>()
const { t } = useI18n()

const sets = ref<AiEvaluationSet[]>([])
const editingId = ref<string | null>(null)
const form = reactive({ name: '', instruction: '', constraints: '' })
const loading = ref(false)
const saving = ref(false)
const error = ref('')

const isValid = computed(() => !!form.name.trim() && !!form.instruction.trim() && !saving.value)

function edit(item: AiEvaluationSet | null): void {
  error.value = ''
  editingId.value = item?.set_id ?? null
  form.name = item?.name ?? ''
  form.instruction = item?.instruction ?? ''
  form.constraints = (item?.constraints ?? []).join('\n')
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

async function mutate(action: () => Promise<unknown>): Promise<void> {
  saving.value = true
  error.value = ''
  try {
    await action()
    await load()
    emit('changed', sets.value)
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.evaluation.load_failed')
  } finally {
    saving.value = false
  }
}

async function save(): Promise<void> {
  if (!isValid.value) return
  const input = {
    name: form.name.trim(),
    instruction: form.instruction.trim(),
    constraints: form.constraints.split('\n').map((line) => line.trim()).filter(Boolean),
  }
  await mutate(async () => {
    const saved = await saveEvaluationSet(props.projectId, editingId.value, input)
    editingId.value = saved.set_id
  })
}

async function remove(): Promise<void> {
  const id = editingId.value
  if (!id) return
  await mutate(async () => {
    await deleteEvaluationSet(props.projectId, id)
    edit(null)
  })
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
</style>
