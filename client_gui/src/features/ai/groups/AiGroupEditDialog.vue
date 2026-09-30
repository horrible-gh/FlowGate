<template>
  <ModalShell :open="open" size="md" @close="onClose">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">
          {{ isEdit ? t('gui.ai.groups.edit_title') : t('gui.ai.groups.add_title') }}
        </h2>
      </div>
    </template>

    <div class="dialog-form">
      <div v-if="error" class="form-error">{{ error }}</div>

      <div class="form-group">
        <label class="form-label" for="group-name">{{ t('gui.ai.groups.field_name') }} *</label>
        <Input
          id="group-name"
          v-model="form.name"
          maxlength="200"
          :placeholder="t('gui.ai.groups.placeholder_name')"
          :disabled="saving"
        />
      </div>

      <div class="form-group">
        <label class="form-label" for="group-desc">{{ t('gui.ai.groups.field_desc') }}</label>
        <textarea
          id="group-desc"
          v-model="form.description"
          class="form-textarea"
          maxlength="2000"
          rows="3"
          :placeholder="t('gui.ai.groups.placeholder_desc')"
          :disabled="saving"
        />
      </div>

      <div class="form-group">
        <label class="form-label" for="group-work-executor">
          {{ t('gui.ai.groups.role_executor') }} (Supervisor) *
        </label>
        <Select
          id="group-work-executor"
          v-model="form.work_executor_provider_id"
          :options="executorOptions"
          :disabled="saving"
        />
      </div>

      <div class="form-group">
        <label class="form-label" for="group-reviewer">
          {{ t('gui.ai.groups.role_reviewer') }} (Reviewer)
        </label>
        <Select
          id="group-reviewer"
          v-model="form.reviewer_provider_id"
          :options="optionalRoleOptions(form.reviewer_provider_id)"
          :disabled="saving"
        />
      </div>

      <div class="form-group">
        <label class="form-label" for="group-reworker">
          {{ t('gui.ai.groups.role_reworker') }} (Reworker)
        </label>
        <Select
          id="group-reworker"
          v-model="form.reworker_provider_id"
          :options="optionalRoleOptions(form.reworker_provider_id)"
          :disabled="saving"
        />
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
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import type { AiGroup, AiGroupInput, ProviderItem } from '../types'

const props = defineProps<{
  open: boolean
  group: AiGroup | null
  providers: ProviderItem[]
  saving?: boolean
  error?: string | null
}>()

const emit = defineEmits<{
  close: []
  save: [input: AiGroupInput]
}>()

const { t } = useI18n()

const isEdit = computed(() => !!props.group?.ai_group_id)

interface FormState {
  name: string
  description: string
  work_executor_provider_id: string
  reviewer_provider_id: string
  reworker_provider_id: string
}

const form = ref<FormState>({
  name: '',
  description: '',
  work_executor_provider_id: '',
  reviewer_provider_id: '',
  reworker_provider_id: '',
})

watch(
  () => props.group,
  (g) => {
    if (g) {
      form.value = {
        name: g.name,
        description: g.description ?? '',
        work_executor_provider_id: g.work_executor?.provider_id ?? '',
        reviewer_provider_id: g.reviewer?.provider_id ?? '',
        reworker_provider_id: g.reworker?.provider_id ?? '',
      }
    } else {
      form.value = {
        name: '',
        description: '',
        work_executor_provider_id: selectableProviders.value[0]?.id ?? props.providers.find((p) => p.enabled !== false)?.id ?? '',
        reviewer_provider_id: '',
        reworker_provider_id: '',
      }
    }
  },
  { immediate: true }
)

const selectableProviders = computed(() => (props.providers ?? []).filter((p) => p.enabled !== false))

function getBrokenRoleLabel(currentVal: string): string {
  const existing = props.providers.find((p) => p.id === currentVal)
  if (existing) return `${existing.name} (${existing.kind})`
  if (props.group?.work_executor?.provider_id === currentVal && props.group?.work_executor?.provider_name) {
    return `${props.group.work_executor.provider_name} (${currentVal})`
  }
  if (props.group?.reviewer?.provider_id === currentVal && props.group?.reviewer?.provider_name) {
    return `${props.group.reviewer.provider_name} (${currentVal})`
  }
  if (props.group?.reworker?.provider_id === currentVal && props.group?.reworker?.provider_name) {
    return `${props.group.reworker.provider_name} (${currentVal})`
  }
  return currentVal
}

const executorOptions = computed<SelectOption[]>(() => {
  const currentVal = form.value.work_executor_provider_id
  const opts: SelectOption[] = selectableProviders.value.map((p) => ({
    value: p.id,
    label: `${p.name} (${p.kind})`,
  }))
  if (currentVal && !selectableProviders.value.some((p) => p.id === currentVal)) {
    const displayLabel = getBrokenRoleLabel(currentVal)
    opts.unshift({
      value: currentVal,
      label: `${displayLabel} — ${t('gui.ai.groups.unavailable_provider')}`,
    })
  }
  return opts
})

function optionalRoleOptions(currentVal: string): SelectOption[] {
  const opts: SelectOption[] = [
    { value: '', label: t('gui.ai.common.none') },
    ...selectableProviders.value.map((p) => ({
      value: p.id,
      label: `${p.name} (${p.kind})`,
    })),
  ]
  if (currentVal && !selectableProviders.value.some((p) => p.id === currentVal)) {
    const displayLabel = getBrokenRoleLabel(currentVal)
    opts.push({
      value: currentVal,
      label: `${displayLabel} — ${t('gui.ai.groups.unavailable_provider')}`,
    })
  }
  return opts
}

const isValid = computed(() => {
  return form.value.name.trim().length > 0 && form.value.work_executor_provider_id.trim().length > 0
})

function onClose() {
  if (props.saving) return
  emit('close')
}

function onSave() {
  if (!isValid.value) return
  emit('save', {
    name: form.value.name.trim(),
    description: form.value.description.trim() || null,
    work_executor_provider_id: form.value.work_executor_provider_id,
    reviewer_provider_id: form.value.reviewer_provider_id || null,
    reworker_provider_id: form.value.reworker_provider_id || null,
  })
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

.form-textarea {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font-size: 13px;
  font-family: inherit;
  resize: vertical;
}

.form-textarea:focus-visible {
  outline: 2px solid var(--fg2-focus);
}

.form-textarea:disabled {
  opacity: 0.5;
  cursor: not-allowed;
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