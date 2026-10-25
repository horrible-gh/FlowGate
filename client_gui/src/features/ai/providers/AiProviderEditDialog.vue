<template>
  <ModalShell :open="open" size="md" @close="onClose">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">
          {{ isEdit ? t('gui.ai.providers.edit_title') : t('gui.ai.providers.add_title') }}
        </h2>
      </div>
    </template>

    <div class="dialog-form">
      <div v-if="saveError" class="form-error" role="alert">{{ saveError }}</div>
      <div v-if="validationMessages.length" class="form-error" role="alert">
        <div v-for="message in validationMessages" :key="message">{{ message }}</div>
      </div>

      <div class="form-group">
        <label class="form-label" for="provider-name">{{ t('gui.ai.providers.field_name') }} *</label>
        <Input
          id="provider-name"
          v-model="form.name"
          :placeholder="t('gui.ai.providers.placeholder_name')"
          :disabled="saving"
          :invalid="!!nameError"
          :aria-describedby="nameError ? 'provider-name-error' : undefined"
          @update:model-value="onNameChange"
        />
        <small v-if="nameError" id="provider-name-error" class="field-hint text-warn" role="alert">
          {{ nameError }}
        </small>
      </div>

      <!-- Owner: only when adding, and only when more than one list can take the new AI -->
      <div v-if="!isEdit && ownerChoices.length > 1" class="form-group">
        <span class="form-label">{{ t('gui.ai.owned.field_owner') }}</span>
        <div class="segmented" role="radiogroup" :aria-label="t('gui.ai.owned.field_owner')">
          <button
            v-for="choice in ownerChoices"
            :key="choice"
            type="button"
            role="radio"
            :aria-checked="selectedOwner === choice"
            :class="{ on: selectedOwner === choice }"
            :disabled="saving"
            @click="selectedOwner = choice"
          >
            {{ t(`gui.ai.owned.owner_${choice}`) }}
          </button>
        </div>
      </div>

      <!-- Execution mode: 2x2 (where x how). API is not supported yet and cannot be picked. -->
      <div class="form-group">
        <span class="form-label">{{ t('gui.ai.providers.field_exec_type') }}</span>
        <div class="mode-grid" role="radiogroup" :aria-label="t('gui.ai.providers.field_exec_type')">
          <template v-for="location in EXEC_LOCATIONS" :key="location">
            <button
              v-for="execType in EXEC_TYPES"
              :key="`${location}_${execType}`"
              type="button"
              role="radio"
              class="mode-cell"
              :class="{ on: form.exec_location === location && form.exec_type === execType }"
              :aria-checked="form.exec_location === location && form.exec_type === execType"
              :disabled="saving || !canPick(execType)"
              @click="pickMode(location, execType)"
            >
              <b>{{ t(`gui.ai.modes.${location}_${execType}`) }}</b>
              <small>{{ canPick(execType) ? t(`gui.ai.modes.${location}_hint`) : t('gui.ai.modes.unsupported') }}</small>
            </button>
          </template>
        </div>
      </div>

      <div class="form-group">
        <label class="form-label" for="provider-kind">{{ t('gui.ai.providers.field_kind') }}</label>
        <Select
          id="provider-kind"
          v-model="form.kind"
          :options="kindOptions"
          :disabled="saving"
        />
      </div>

      <!-- CLI settings -->
      <template v-if="form.exec_type === 'cli'">
        <div class="form-group">
          <label class="form-label" for="provider-command">{{ t('gui.ai.providers.field_cli_command') }}</label>
          <Input
            id="provider-command"
            v-model="form.cli_command"
            :placeholder="t('gui.ai.providers.placeholder_cli_command')"
            :disabled="saving"
          />
        </div>
      </template>

      <!-- API settings -->
      <template v-else>
        <div class="form-group">
          <label class="form-label" for="provider-base-url">{{ t('gui.ai.providers.field_api_base_url') }}</label>
          <Input
            id="provider-base-url"
            v-model="form.api_base_url"
            placeholder="https://api.openai.com/v1"
            :disabled="saving"
          />
        </div>

        <div class="form-group">
          <label class="form-label" for="provider-model">{{ t('gui.ai.providers.field_api_model') }}</label>
          <Input
            id="provider-model"
            v-model="form.api_model"
            placeholder="gpt-4o"
            :disabled="saving"
          />
        </div>

        <div class="form-group">
          <label class="form-label" for="provider-api-key">
            {{ t('gui.ai.providers.field_api_key') }}
            <span v-if="form.api_key_set" class="key-hint">({{ t('gui.ai.providers.key_configured') }})</span>
            <span v-else class="key-hint text-warn">({{ t('gui.ai.providers.credential_required') }})</span>
          </label>
          <Input
            id="provider-api-key"
            v-model="form.api_key"
            type="password"
            :placeholder="form.api_key_set ? t('gui.ai.providers.placeholder_keep_key') : t('gui.ai.providers.placeholder_api_key')"
            :disabled="saving || clearApiKey"
          />
          <label v-if="form.api_key_set" class="field-hint">
            <input v-model="clearApiKey" type="checkbox" :disabled="saving" />
            {{ t('gui.ai.providers.clear_api_key') }}
          </label>
          <small v-if="form.exec_type === 'api' && !form.api_key_set" class="field-hint text-warn">
            {{ t('gui.ai.providers.api_key_needed_hint') }}
          </small>
        </div>
      </template>

      <div class="form-group toggle-row">
        <label class="form-label">{{ t('gui.ai.providers.field_enabled') }}</label>
        <Toggle v-model="form.enabled" :disabled="saving" />
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
import Select from '@/components/ui/Select.vue'
import Toggle from '@/components/ui/Toggle.vue'
import type { AiOwnerScope, ExecLocation, ProviderExecType, ProviderItem } from '../types'
import { EXEC_LOCATIONS, EXEC_TYPES, isExecTypeSupported } from './providerDisplay'
import { FIELD_MAX, KINDS, validateProvider } from './providerValidation'

const props = withDefaults(defineProps<{
  open: boolean
  provider: ProviderItem | null
  // Whose list the AI belongs to; ownerChoices lists what an add may pick (admin: both).
  owner?: AiOwnerScope
  ownerChoices?: AiOwnerScope[]
  saving?: boolean
  existingProvidersByOwner?: Record<AiOwnerScope, ProviderItem[]>
  saveError?: string
  nameSaveError?: string
}>(), {
  owner: 'user',
  ownerChoices: () => ['user'],
  existingProvidersByOwner: () => ({ system: [], user: [] }),
})

const emit = defineEmits<{
  close: []
  save: [item: ProviderItem, owner: AiOwnerScope]
  nameChange: []
}>()

const { t } = useI18n()

const nameTouched = ref(false)
const clearApiKey = ref(false)

const isEdit = computed(() => !!props.provider?.id)

interface FormState {
  id: string
  name: string
  exec_type: ProviderExecType
  exec_location: ExecLocation
  kind: string
  enabled: boolean
  cli_command: string
  api_base_url: string
  api_model: string
  api_key: string
  api_key_set: boolean
}

const form = ref<FormState>({
  id: '',
  name: '',
  exec_type: 'cli',
  exec_location: 'local',
  kind: 'claude',
  enabled: true,
  cli_command: '',
  api_base_url: '',
  api_model: '',
  api_key: '',
  api_key_set: false,
})

const selectedOwner = ref<AiOwnerScope>(props.owner)

// An AI already saved as API keeps its cell pickable so editing it does not force a change.
function canPick(execType: ProviderExecType): boolean {
  return isExecTypeSupported(execType) || props.provider?.exec_type === execType
}

function pickMode(location: ExecLocation, execType: ProviderExecType) {
  if (!canPick(execType)) return
  form.value.exec_location = location
  form.value.exec_type = execType
  if (!KINDS[execType].includes(form.value.kind)) form.value.kind = KINDS[execType][0]
}

// flowgate.v02.0088 F08: product names stay as they are; only the generic "Custom" kind is a UI
// word, shown in the current language. The sent kind value stays 'custom'.
const kindOptions = computed(() => KINDS[form.value.exec_type].map((value) => ({
  value,
  label: value === 'custom' ? t('gui.ai.providers.kind_custom') : value === 'claude' ? 'Claude (Anthropic)' : value === 'openai' ? 'OpenAI' : value === 'copilot' ? 'Copilot' : 'Codex',
})))

// Re-filled on every open, so a second add does not start from the first add's values.
watch(
  () => [props.provider, props.open] as const,
  ([p]) => {
    if (p) {
      form.value = {
        id: p.id,
        name: p.name,
        exec_type: p.exec_type,
        exec_location: p.exec_location ?? 'local',
        kind: p.kind,
        enabled: p.enabled,
        cli_command: p.cli_command ?? '',
        api_base_url: p.api_base_url ?? '',
        api_model: p.api_model ?? '',
        api_key: '',
        api_key_set: !!p.api_key_set,
      }
    } else {
      form.value = {
        id: '',
        name: '',
        exec_type: 'cli',
        exec_location: 'local',
        kind: 'claude',
        enabled: true,
        cli_command: '',
        api_base_url: '',
        api_model: '',
        api_key: '',
        api_key_set: false,
      }
    }
    nameTouched.value = false
    clearApiKey.value = false
  },
  { immediate: true }
)

// Reopening the dialog (another card, or another add) starts from the owner it was opened for.
watch(() => [props.open, props.owner] as const, () => {
  selectedOwner.value = props.owner
})

const normalizedName = computed(() => form.value.name.trim())
const duplicateName = computed(() => props.existingProvidersByOwner[isEdit.value ? props.owner : selectedOwner.value].some(
  (provider) => provider.id !== form.value.id && provider.name.trim().toLowerCase() === normalizedName.value.toLowerCase(),
))
const nameError = computed(() => {
  if (nameTouched.value && !normalizedName.value) return t('gui.ai.providers.name_required')
  if (normalizedName.value && duplicateName.value) return t('gui.ai.providers.name_duplicate')
  return props.nameSaveError || ''
})
const fieldIssues = computed(() => validateProvider({ ...form.value, api_key: clearApiKey.value ? '' : form.value.api_key }))
const validationMessages = computed(() => fieldIssues.value.map(({ field, reason }) => t('settings.ai.saveerr_field', {
  field: t(`settings.ai.field.${field}`),
  reason: t(`settings.ai.reason.${reason}`, { max: FIELD_MAX[field] }),
})))
const isValid = computed(() => fieldIssues.value.length === 0 && !duplicateName.value)

function onNameChange() {
  nameTouched.value = true
  emit('nameChange')
}

function onClose() {
  if (props.saving) return
  emit('close')
}

function onSave() {
  if (props.saving || !isValid.value) return
  emit('save', {
    id: form.value.id || ('prv_' + Math.random().toString(36).substring(2, 9)),
    name: form.value.name.trim(),
    exec_type: form.value.exec_type,
    exec_location: form.value.exec_location,
    kind: form.value.kind,
    enabled: form.value.enabled,
    cli_command: form.value.exec_type === 'cli' ? form.value.cli_command : null,
    api_base_url: form.value.exec_type === 'api' ? form.value.api_base_url : null,
    api_model: form.value.exec_type === 'api' ? form.value.api_model : null,
    api_key: clearApiKey.value ? '' : form.value.api_key || null,
  }, isEdit.value ? props.owner : selectedOwner.value)
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

.form-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
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

.key-hint {
  font-size: 11px;
  color: #79d799;
  margin-left: 4px;
}

.text-warn {
  color: var(--fg2-amber-dim, #d97706);
  font-weight: 600;
}

.field-hint {
  display: block;
  margin-top: 4px;
  font-size: 11px;
}

.mode-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}

.mode-cell {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #0c1b2c;
  color: #dce7f3;
  text-align: left;
  cursor: pointer;
  transition: border-color 0.16s ease, background 0.16s ease;
}

.mode-cell b {
  font-size: 13px;
}

.mode-cell small {
  font-size: 11px;
  color: var(--fg2-muted);
}

.mode-cell.on {
  border-color: #55a9ff;
  background: #132a44;
}

.mode-cell:disabled {
  opacity: 0.45;
  border-style: dashed;
  cursor: not-allowed;
}

.segmented {
  display: inline-flex;
  align-self: flex-start;
  padding: 3px;
  border-radius: 8px;
  border: 1px solid var(--fg2-line);
  background: #0a1726;
}

.segmented button {
  height: 28px;
  padding: 0 14px;
  border: 0;
  border-radius: 6px;
  background: none;
  color: #9fb3c8;
  font-size: 12.5px;
  font-weight: 650;
  cursor: pointer;
}

.segmented button.on {
  background: linear-gradient(180deg, #1c4e84, #153b65);
  color: #edf6ff;
}

.toggle-row {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  padding-top: 6px;
}

.form-error {
  white-space: pre-line;
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