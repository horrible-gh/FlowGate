<template>
  <ModalShell :open="open" size="md" @close="onClose">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">{{ title }}</h2>
      </div>
    </template>

    <div class="dialog-form">
      <div v-if="error" class="form-error">{{ error }}</div>

      <div class="form-group">
        <label class="form-label" for="ai-group-edit-name">
          {{ kind === 'group' ? t('gui.ai.groups.field_name') : t('gui.ai.groups.field_role_name') }} *
        </label>
        <Input
          id="ai-group-edit-name"
          v-model="form.name"
          :maxlength="kind === 'group' ? 200 : 100"
          :placeholder="kind === 'group' ? t('gui.ai.groups.placeholder_name') : t('gui.ai.groups.placeholder_role_name')"
          :disabled="saving"
        />
      </div>

      <div class="form-group">
        <label class="form-label" for="ai-group-edit-desc">{{ t('gui.ai.groups.field_desc') }}</label>
        <Textarea
          id="ai-group-edit-desc"
          v-model="form.description"
          :rows="3"
          maxlength="2000"
          :placeholder="kind === 'group' ? t('gui.ai.groups.placeholder_desc') : t('gui.ai.groups.placeholder_role_desc')"
          :disabled="saving"
        />
      </div>

      <!-- Role only: criteria the role relies on; the member fit is computed from them. -->
      <div v-if="kind === 'role'" class="form-group">
        <span class="form-label">{{ t('gui.ai.groups.field_criteria') }}</span>
        <div class="criteria-chips">
          <label v-for="key in EVALUATION_CRITERIA" :key="key" class="criteria-chip" :class="{ on: form.criteria.includes(key) }">
            <input v-model="form.criteria" type="checkbox" :value="key" :disabled="saving" />
            {{ t(`gui.ai.evaluation.criteria.${key}`) }}
          </label>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="dialog-actions">
        <Button variant="secondary" :disabled="saving" @click="onClose">
          {{ t('gui.common.cancel') }}
        </Button>
        <Button variant="primary" :loading="saving" :disabled="!isValid" @click="onSave">
          {{ t('gui.common.save') }}
        </Button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup lang="ts">
// Name + optional description. Used for a team (AI Group) and for one of its custom roles
// (plus the criteria the role relies on). Members, roles and runtime slots are all optional.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { EVALUATION_CRITERIA, type AiGroupRoleInput } from '../types'

const props = withDefaults(
  defineProps<{
    open: boolean
    kind?: 'group' | 'role'
    initial?: AiGroupRoleInput | null
    saving?: boolean
    error?: string | null
  }>(),
  { kind: 'group', initial: null, saving: false, error: null },
)

const emit = defineEmits<{
  close: []
  save: [input: AiGroupRoleInput]
}>()

const { t } = useI18n()

const isEdit = computed(() => !!props.initial)
const title = computed(() => {
  if (props.kind === 'role') return isEdit.value ? t('gui.ai.groups.role_edit_title') : t('gui.ai.groups.role_add_title')
  return isEdit.value ? t('gui.ai.groups.edit_title') : t('gui.ai.groups.add_title')
})

const form = ref<{ name: string; description: string; criteria: string[] }>({ name: '', description: '', criteria: [] })

watch(
  () => [props.open, props.initial] as const,
  ([open, initial]) => {
    if (!open) return
    form.value = { name: initial?.name ?? '', description: initial?.description ?? '', criteria: [...(initial?.criteria ?? [])] }
  },
  { immediate: true }
)

const isValid = computed(() => form.value.name.trim().length > 0)

function onClose() {
  if (props.saving) return
  emit('close')
}

function onSave() {
  if (!isValid.value) return
  const input: AiGroupRoleInput = { name: form.value.name.trim(), description: form.value.description.trim() || null }
  if (props.kind === 'role') input.criteria = [...form.value.criteria]
  emit('save', input)
}
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

.dialog-form {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.criteria-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.criteria-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid var(--fg2-line);
  font-size: 12px;
  color: var(--fg2-muted);
  cursor: pointer;
}

.criteria-chip.on {
  border-color: rgba(40, 110, 201, 0.6);
  color: var(--fg2-text);
}

.criteria-chip input {
  margin: 0;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-label {
  font-size: 12.5px;
  font-weight: 650;
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
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 20px;
  border-top: 1px solid var(--fg2-line);
}
</style>
