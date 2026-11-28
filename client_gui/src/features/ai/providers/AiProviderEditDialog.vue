<template>
  <ModalShell :open="open" size="md" @close="onClose">
    <template #header="{ titleId }">
      <div class="dialog-header">
        <h2 :id="titleId" class="dialog-title">
          {{ isEdit ? t('gui.ai.providers.edit_title') : t('gui.ai.providers.add_title') }}
        </h2>
        <!-- 사용 여부 lives in the title row (F108-4): the state word, then the switch. -->
        <div class="header-switch">
          <span class="header-switch-state" :class="{ on: form.enabled }">
            {{ form.enabled ? t('gui.ai.providers.connection_on') : t('gui.ai.providers.connection_off') }}
          </span>
          <Toggle v-model="form.enabled" :aria-label="t('gui.ai.providers.field_enabled')" :disabled="saving" />
        </div>
      </div>
    </template>

    <!-- Body order (F108-4): 이름 → 실행 방식 → 모델 구성 → 커맨드. -->
    <div class="dialog-form">
      <div v-if="saveError" class="form-error" role="alert">{{ saveError }}</div>
      <div v-if="validationMessages.length" class="form-error" role="alert">
        <div v-for="message in validationMessages" :key="message">{{ message }}</div>
      </div>
      <div v-if="composeNotice" class="form-error" role="alert">{{ composeNotice }}</div>

      <div class="form-group">
        <div class="label-row">
          <label class="form-label" for="provider-name">{{ t('gui.ai.providers.field_name') }} *</label>
          <span class="label-spacer" />
          <button v-if="showNameReset" type="button" class="link-btn" :disabled="saving" @click="resetName">
            {{ t('gui.ai.providers.name_auto_reset') }}
          </button>
        </div>
        <!-- data-modal-autofocus: the title-row switch is the first focusable, but opening starts on the name. -->
        <Input
          id="provider-name"
          v-model="form.name"
          data-modal-autofocus
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

      <!-- 실행 방식 takes the full row; only a remote CLI splits it with the target Agent. -->
      <div class="form-row">
        <div class="form-group" :class="{ 'span-all': !isRemoteCli }">
          <label class="form-label" for="provider-exec">{{ t('gui.ai.providers.field_exec_type') }}</label>
          <Select
            id="provider-exec"
            :model-value="execValue"
            :options="execOptions"
            :disabled="saving"
            @update:model-value="onExecChange"
          />
        </div>
        <div
          v-if="form.exec_type === 'cli'"
          class="form-group"
          :class="{ ghost: !isRemoteCli }"
          :aria-hidden="isRemoteCli ? undefined : 'true'"
        >
          <label class="form-label" for="provider-agent">{{ t('gui.ai.providers.field_target_agent') }} *</label>
          <Select
            id="provider-agent"
            v-model="form.target_agent_id"
            :options="agentOptions"
            placeholder=" "
            :disabled="saving || !isRemoteCli || agentsState !== 'ready'"
            :invalid="isRemoteCli && agentMissing"
          />
          <small v-if="isRemoteCli && agentNote" class="field-hint text-warn" role="alert">{{ agentNote }}</small>
        </div>
      </div>

      <template v-if="form.exec_type === 'cli'">
        <!-- 모델 구성: 종류 on the first line, 버전 · 모델 · 추론도 on the second. -->
        <section class="model-group" role="group" aria-labelledby="provider-model-group">
          <h3 id="provider-model-group" class="group-title">{{ t('gui.ai.providers.group_model') }}</h3>
          <div class="form-group">
            <label class="form-label" for="provider-kind">{{ t('gui.ai.providers.field_kind') }}</label>
            <Select
              id="provider-kind"
              :model-value="uiKind"
              :options="cliKindOptions"
              :disabled="saving || preparing"
              @update:model-value="onKindChange"
            />
          </div>
          <div v-if="entry" class="model-row">
            <div class="form-group">
              <label class="form-label" for="provider-version">{{ t('gui.ai.providers.field_version') }}</label>
              <Input
                id="provider-version"
                :model-value="selection.version"
                list="provider-version-suggestions"
                maxlength="64"
                :disabled="saving"
                @update:model-value="setSelection('version', $event)"
              />
              <datalist id="provider-version-suggestions">
                <option v-for="value in entry.versions" :key="value" :value="value">{{ value }}</option>
              </datalist>
            </div>
            <div class="form-group">
              <label class="form-label" for="provider-model-name">{{ t('gui.ai.providers.field_model') }}</label>
              <Input
                id="provider-model-name"
                :model-value="selection.model"
                list="provider-model-name-suggestions"
                maxlength="64"
                :disabled="saving"
                @update:model-value="setSelection('model', $event)"
              />
              <datalist id="provider-model-name-suggestions">
                <option v-for="value in entry.models" :key="value" :value="value">{{ kindLabel(value) }}</option>
              </datalist>
            </div>
            <div class="form-group">
              <label class="form-label" for="provider-effort">{{ t('gui.ai.providers.field_effort') }}</label>
              <Select
                id="provider-effort"
                :model-value="selection.effort"
                :options="entry.efforts.map(plainOption)"
                :disabled="saving"
                @update:model-value="setSelection('effort', $event)"
              />
            </div>
          </div>
        </section>

        <!-- 커맨드: the one-line preview is always shown; 직접 편집 opens the editor below it
             (사용자 정의 keeps the editor open). The two conditions are separate on purpose. -->
        <div class="form-group command-block">
          <div class="label-row">
            <span id="provider-command-preview-label" class="form-label">{{ t('gui.ai.providers.command_preview') }}</span>
            <span class="command-chip" :class="{ edited: !isCustom && !commandAuto }">{{ commandChip }}</span>
            <span class="label-spacer" />
            <button v-if="showCommandReset" type="button" class="link-btn" :disabled="saving" @click="resetCommand">
              {{ t('gui.ai.providers.command_reset') }}
            </button>
            <button
              v-if="!isCustom"
              type="button"
              class="link-btn command-edit-btn"
              :aria-expanded="commandVisible"
              :disabled="saving"
              @click="showCommand = !showCommand"
            >
              {{ showCommand ? t('gui.ai.providers.command_edit_close') : t('gui.ai.providers.command_edit') }}
            </button>
          </div>
          <div
            class="command-preview"
            role="textbox"
            aria-readonly="true"
            aria-labelledby="provider-command-preview-label"
            :title="form.cli_command"
          >{{ form.cli_command || '—' }}</div>
          <div v-if="commandVisible" class="command-editor">
            <div v-if="entry" class="form-group">
              <label class="form-label" for="provider-executable">{{ t('gui.ai.providers.field_executable') }}</label>
              <Input
                id="provider-executable"
                class="mono"
                :model-value="selection.executable"
                :disabled="saving"
                :invalid="composeState === 'invalid_executable'"
                @update:model-value="setExecutable"
              />
            </div>
            <div class="form-group">
              <label class="form-label" for="provider-command">{{ t('gui.ai.providers.field_command') }}</label>
              <textarea
                id="provider-command"
                v-model="form.cli_command"
                class="command-input"
                spellcheck="false"
                :disabled="saving"
                @input="onCommandInput"
              />
            </div>
          </div>
        </div>
      </template>

      <!-- An AI saved as API before API was retired keeps its own fields so editing it does not lose them. -->
      <template v-else>
        <section class="model-group" role="group" aria-labelledby="provider-model-group">
          <h3 id="provider-model-group" class="group-title">{{ t('gui.ai.providers.group_model') }}</h3>
          <div class="form-group">
            <label class="form-label" for="provider-kind">{{ t('gui.ai.providers.field_kind') }}</label>
            <Select id="provider-kind" v-model="form.kind" :options="apiKindOptions" :disabled="saving" />
          </div>
          <div class="form-group">
            <label class="form-label" for="provider-model">{{ t('gui.ai.providers.field_api_model') }}</label>
            <Input id="provider-model" v-model="form.api_model" :disabled="saving" />
          </div>
        </section>
        <div class="form-group">
          <label class="form-label" for="provider-base-url">{{ t('gui.ai.providers.field_api_base_url') }}</label>
          <Input id="provider-base-url" v-model="form.api_base_url" :disabled="saving" />
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
            :disabled="saving || clearApiKey"
          />
          <label v-if="form.api_key_set" class="field-hint">
            <input v-model="clearApiKey" type="checkbox" :disabled="saving" />
            {{ t('gui.ai.providers.clear_api_key') }}
          </label>
        </div>
      </template>
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
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import Select from '@/components/ui/Select.vue'
import Toggle from '@/components/ui/Toggle.vue'
import { agentState } from '@/features/settings/agentState'
import { listPersonalAgents, listSystemAgents } from '@/features/settings/api'
import type { SystemAgent } from '@/features/settings/types'
import { AiRequestError, composeCliPreset, fetchCliModelCatalog, parseCliPreset } from '../api'
import type { AiOwnerScope, CliModelCatalog, ExecLocation, ProviderExecType, ProviderItem } from '../types'
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

const CUSTOM = 'custom'
const nameTouched = ref(false)
const clearApiKey = ref(false)

const isEdit = computed(() => !!props.provider?.id)

interface FormState {
  id: string
  name: string
  exec_type: ProviderExecType
  exec_location: ExecLocation
  // The kind that is saved. It stays the stored kind (copilot, a hand-edited claude, ...) while the
  // screen shows such a connection as 사용자 정의, so opening and saving never changes how it runs.
  kind: string
  enabled: boolean
  cli_command: string
  api_base_url: string
  api_model: string
  api_key: string
  api_key_set: boolean
  target_agent_id: string
}

function emptyForm(): FormState {
  return {
    id: '', name: '', exec_type: 'cli', exec_location: 'local', kind: CUSTOM, enabled: true,
    cli_command: '', api_base_url: '', api_model: '', api_key: '', api_key_set: false, target_agent_id: '',
  }
}

const form = ref<FormState>(emptyForm())
const selectedOwner = ref<AiOwnerScope>(props.owner)

// ── Model catalog and composition (server: GET cli-model-catalog, POST cli-preset-compose/-parse) ──
// The lists, the command templates and the connection names all come from the server; nothing
// about a CLI's flags or model ids is written in this screen.
interface Selection { version: string; model: string; effort: string; executable: string }

const catalog = ref<CliModelCatalog>({})
const catalogState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')
const uiKind = ref<string>(CUSTOM)
const selection = ref<Selection>({ version: '', model: '', effort: '', executable: '' })
const nameAuto = ref(false)
const commandAuto = ref(false)
const showCommand = ref(false)
const composed = ref<{ name: string; cli_command: string } | null>(null)
const composeState = ref<'idle' | 'pending' | 'failed' | 'invalid_executable'>('idle')
const preparing = ref(false)
// Gemini only: a saved --print-timeout is carried over when the selection is composed again.
let timeoutSeconds: number | undefined
let openSeq = 0
let composeSeq = 0
let executableTimer: ReturnType<typeof setTimeout> | null = null

const entry = computed(() => (form.value.exec_type === 'cli' ? catalog.value[uiKind.value] ?? null : null))
const isCustom = computed(() => !entry.value)
const commandVisible = computed(() => isCustom.value || showCommand.value)

const kindLabel = (kind: string) => kind.charAt(0).toUpperCase() + kind.slice(1)
const plainOption = (value: string) => ({ value, label: value })

const cliKindOptions = computed(() => [
  ...Object.keys(catalog.value).map((value) => ({ value, label: kindLabel(value) })),
  { value: CUSTOM, label: t('gui.ai.providers.kind_custom') },
])
const apiKindOptions = computed(() => KINDS.api.map((value) => ({
  value,
  label: value === CUSTOM ? t('gui.ai.providers.kind_custom') : value === 'claude' ? 'Claude (Anthropic)' : 'OpenAI',
})))

async function loadCatalog() {
  if (catalogState.value === 'ready') return
  catalogState.value = 'loading'
  try {
    catalog.value = await fetchCliModelCatalog()
    catalogState.value = 'ready'
  } catch {
    catalog.value = {}
    catalogState.value = 'failed'
  }
}

function clearExecutableTimer() {
  if (executableTimer) clearTimeout(executableTimer)
  executableTimer = null
}

async function compose() {
  clearExecutableTimer()
  if (!entry.value) return
  const seq = ++composeSeq
  composeState.value = 'pending'
  try {
    const result = await composeCliPreset({
      kind: uiKind.value,
      version: selection.value.version,
      model: selection.value.model,
      effort: selection.value.effort,
      executable: selection.value.executable.trim(),
      ...(timeoutSeconds ? { timeout_seconds: timeoutSeconds } : {}),
    })
    if (seq !== composeSeq) return
    composed.value = { name: result.name, cli_command: result.cli_command }
    composeState.value = 'idle'
    if (nameAuto.value) form.value.name = result.name
    if (commandAuto.value) form.value.cli_command = result.cli_command
  } catch (error) {
    if (seq !== composeSeq) return
    composed.value = null
    composeState.value = error instanceof AiRequestError && error.fieldErrors.some((e) => e.field === 'executable')
      ? 'invalid_executable'
      : 'failed'
  }
}

function dropCompose() {
  composeSeq += 1
  clearExecutableTimer()
  composeState.value = 'idle'
}

// Picking a kind starts from its defaults and turns automatic name and command back on (v7).
function applyKind(kind: string) {
  const next = catalog.value[kind]
  if (!next) return
  const previousDefault = entry.value?.defaults.executable
  const executable = selection.value.executable.trim()
  uiKind.value = kind
  form.value.kind = kind
  selection.value = {
    version: next.defaults.version,
    model: next.defaults.model,
    effort: next.defaults.effort,
    executable: !executable || executable === previousDefault ? next.defaults.executable : executable,
  }
  nameAuto.value = true
  commandAuto.value = true
  void compose()
}

function onKindChange(kind: string) {
  if (kind === uiKind.value) return
  if (kind === CUSTOM || !catalog.value[kind]) {
    dropCompose()
    uiKind.value = CUSTOM
    form.value.kind = CUSTOM
    nameAuto.value = false
    commandAuto.value = false
    return
  }
  applyKind(kind)
}

function setSelection(field: 'version' | 'model' | 'effort', value: string) {
  selection.value = { ...selection.value, [field]: value }
  if (field === 'effort') void compose()
  else scheduleCompose()
}

function setExecutable(value: string) {
  selection.value = { ...selection.value, executable: value }
  scheduleCompose()
}

function scheduleCompose() {
  clearExecutableTimer()
  composeSeq += 1 // a typed character makes any composition in flight stale
  composeState.value = 'pending'
  executableTimer = setTimeout(() => void compose(), 300)
}

const showNameReset = computed(() => form.value.exec_type === 'cli' && !!entry.value && !nameAuto.value && !!composed.value)
const showCommandReset = computed(() => !!entry.value && !commandAuto.value && !!composed.value)
const commandChip = computed(() => {
  if (isCustom.value) return t('gui.ai.providers.command_custom')
  return commandAuto.value ? t('gui.ai.providers.command_auto') : t('gui.ai.providers.command_edited')
})

function resetName() {
  if (!composed.value) return
  nameAuto.value = true
  form.value.name = composed.value.name
  emit('nameChange')
}

function resetCommand() {
  if (!composed.value) return
  commandAuto.value = true
  form.value.cli_command = composed.value.cli_command
}

function onCommandInput() {
  if (entry.value) commandAuto.value = false
}

const GEMINI_TIMEOUT_RE = /--print-timeout\s+(\d+)m(?:\s|$)/
function savedTimeoutSeconds(command: string): number | undefined {
  const minutes = Number(GEMINI_TIMEOUT_RE.exec(command)?.[1])
  return Number.isInteger(minutes) && minutes > 0 ? minutes * 60 : undefined
}

function openAsCustom() {
  uiKind.value = CUSTOM
  nameAuto.value = false
  commandAuto.value = false
}

// Fills the combos from what was saved: a command that is exactly the composed form opens as its
// selection, anything else (copilot, custom, a hand-edited command) opens as 사용자 정의 and is
// saved as it is.
async function prepare(seq: number, provider: ProviderItem | null) {
  if (form.value.exec_type !== 'cli') return
  preparing.value = true
  try {
    await loadCatalog()
    if (seq !== openSeq) return
    if (!provider) {
      const first = Object.keys(catalog.value)[0]
      if (first) applyKind(first)
      else openAsCustom()
      return
    }
    const command = provider.cli_command ?? ''
    if (!catalog.value[provider.kind] || !command.trim()) { openAsCustom(); return }
    let parsed: Awaited<ReturnType<typeof parseCliPreset>> | null = null
    try {
      parsed = await parseCliPreset(command, provider.kind)
    } catch {
      parsed = null
    }
    if (seq !== openSeq) return
    const picked = parsed?.selection
    if (!picked || !catalog.value[picked.kind]) { openAsCustom(); return }
    uiKind.value = picked.kind
    selection.value = { version: picked.version, model: picked.model, effort: picked.effort, executable: picked.executable }
    timeoutSeconds = savedTimeoutSeconds(command)
    nameAuto.value = false
    commandAuto.value = false
    await compose()
    if (seq !== openSeq || !composed.value) return
    // A name with something added (e.g. "Claude Opus 5.5 (AG)") stays as typed.
    nameAuto.value = composed.value.name === form.value.name
    commandAuto.value = composed.value.cli_command === form.value.cli_command
  } finally {
    if (seq === openSeq) preparing.value = false
  }
}

// Re-filled on every open, so a second add does not start from the first add's values.
watch(
  () => [props.provider, props.open] as const,
  ([p, open]) => {
    const seq = ++openSeq
    dropCompose()
    form.value = p
      ? {
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
          target_agent_id: p.target_agent_id ?? '',
        }
      : emptyForm()
    nameTouched.value = false
    clearApiKey.value = false
    uiKind.value = CUSTOM
    selection.value = { version: '', model: '', effort: '', executable: '' }
    nameAuto.value = false
    commandAuto.value = false
    showCommand.value = false
    composed.value = null
    timeoutSeconds = undefined
    preparing.value = false
    if (open) void prepare(seq, p)
  },
  { immediate: true }
)

onBeforeUnmount(() => {
  openSeq += 1
  dropCompose()
})

// Reopening the dialog (another card, or another add) starts from the owner it was opened for.
watch(() => [props.open, props.owner] as const, () => {
  selectedOwner.value = props.owner
})

// ── Execution mode: local CLI → remote CLI → local API → remote API; API cannot be picked ──
const EXEC_ORDER = EXEC_TYPES.flatMap((execType) => EXEC_LOCATIONS.map((location) => ({ location, execType })))

// An AI already saved as API keeps its entry pickable so editing it does not force a change.
function canPick(execType: ProviderExecType): boolean {
  return isExecTypeSupported(execType) || props.provider?.exec_type === execType
}

const execValue = computed(() => `${form.value.exec_location}_${form.value.exec_type}`)
const execOptions = computed(() => EXEC_ORDER.map(({ location, execType }) => ({
  value: `${location}_${execType}`,
  label: t(`gui.ai.modes.${location}_${execType}`),
  disabled: !canPick(execType),
})))

async function onExecChange(value: string) {
  const picked = EXEC_ORDER.find(({ location, execType }) => `${location}_${execType}` === value)
  if (!picked || !canPick(picked.execType)) return
  const typeChanged = picked.execType !== form.value.exec_type
  form.value.exec_location = picked.location
  form.value.exec_type = picked.execType
  if (!typeChanged) return
  if (picked.execType === 'api') {
    dropCompose()
    if (!KINDS.api.includes(form.value.kind)) form.value.kind = KINDS.api[0]
    return
  }
  const seq = openSeq
  await loadCatalog()
  if (seq !== openSeq || form.value.exec_type !== 'cli') return
  if (catalog.value[form.value.kind]) {
    applyKind(form.value.kind)
  } else {
    openAsCustom()
    if (!KINDS.cli.includes(form.value.kind)) form.value.kind = CUSTOM
  }
}

// Remote CLI: the target Agent comes from the list of whose AI this is (system AI = system Agents,
// my AI = my Agents). Nothing is picked for the user; only an online Agent that runs CLI can be chosen.
const isRemoteCli = computed(() => form.value.exec_type === 'cli' && form.value.exec_location === 'remote')
const effectiveOwner = computed(() => (isEdit.value ? props.owner : selectedOwner.value))
const agents = ref<SystemAgent[]>([])
const agentsState = ref<'idle' | 'loading' | 'ready' | 'failed'>('idle')
let agentsRequest = 0

async function loadAgents() {
  const request = ++agentsRequest
  agentsState.value = 'loading'
  // A new connection never keeps the previous owner's list or pick while the current owner's list is pending.
  if (!isEdit.value) {
    agents.value = []
    form.value.target_agent_id = ''
  }
  try {
    const list = (await (effectiveOwner.value === 'system' ? listSystemAgents() : listPersonalAgents())) ?? []
    if (request !== agentsRequest) return
    agents.value = list.filter((agent) => agent.location === 'remote')
    agentsState.value = 'ready'
    if (!isEdit.value && !agents.value.some((agent) => agent.agent_id === form.value.target_agent_id)) {
      form.value.target_agent_id = ''
    }
  } catch {
    if (request !== agentsRequest) return
    agents.value = []
    agentsState.value = 'failed'
  }
}

watch(
  () => [props.open, isRemoteCli.value, effectiveOwner.value] as const,
  ([open, remote]) => {
    if (open && remote) { void loadAgents(); return }
    agentsRequest += 1 // drop any in-flight list so a late response cannot refill a stale pick
    agents.value = []
    agentsState.value = 'idle'
    if (!isEdit.value) form.value.target_agent_id = ''
  },
  { immediate: true },
)

// Name only; online/offline is the dot in front of it.
const agentOptions = computed(() => agents.value.map((agent) => {
  const online = agentState(agent) === 'online'
  return {
    value: agent.agent_id,
    label: `${online ? '●' : '○'} ${agent.name}`,
    disabled: !online || !agent.effective_capabilities.ai_cli,
    muted: !online,
  }
}))

const agentMissing = computed(() => agentsState.value === 'ready' && !!form.value.target_agent_id
  && !agents.value.some((agent) => agent.agent_id === form.value.target_agent_id))

const agentNote = computed(() => {
  if (agentsState.value === 'failed') return t('gui.ai.providers.agent_load_failed')
  if (agentsState.value === 'ready' && !agents.value.length) return t('gui.ai.providers.agent_none')
  if (agentMissing.value) return t('gui.ai.providers.agent_missing')
  return ''
})

const composeNotice = computed(() => {
  if (form.value.exec_type !== 'cli') return ''
  if (catalogState.value === 'failed') return t('gui.ai.providers.catalog_load_failed')
  if (!entry.value) return ''
  if (composeState.value === 'invalid_executable') return t('gui.ai.providers.executable_invalid')
  if (composeState.value === 'failed') return t('gui.ai.providers.compose_failed')
  return ''
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
// A new remote CLI can only be saved once the current owner's list arrived (loading/failed/idle block it).
const agentPending = computed(() => isRemoteCli.value && !isEdit.value && agentsState.value !== 'ready')
// While a value is still filled automatically, it must be the composition of the current selection.
const composeBlocked = computed(() => form.value.exec_type === 'cli' && (preparing.value
  || (!!entry.value && (nameAuto.value || commandAuto.value) && composeState.value !== 'idle')))
const isValid = computed(() => fieldIssues.value.length === 0 && !duplicateName.value
  && !agentPending.value && !(isRemoteCli.value && agentMissing.value) && !composeBlocked.value)

function onNameChange() {
  nameTouched.value = true
  if (entry.value) nameAuto.value = false
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
    ...(isRemoteCli.value ? { target_agent_id: form.value.target_agent_id } : {}),
  }, isEdit.value ? props.owner : selectedOwner.value)
}
</script>

<style scoped>
/* F108-4: spacing follows ModalShell (24px side gutter of .ui-modal-body), type follows the shared
   --fg2-fs-* scale and every colour is a --fg2-* token; nothing below hardcodes a hex value. */
.dialog-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 24px 14px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  flex: 1;
  min-width: 0;
  margin: 0;
  font-size: var(--fg2-fs-title);
  font-weight: 800;
  color: var(--fg2-text);
}

.header-switch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.header-switch-state {
  font-size: var(--fg2-fs-sub);
  font-weight: 650;
  color: var(--fg2-text-3);
  white-space: nowrap;
}

.header-switch-state.on {
  color: var(--fg2-status-success-fg);
}

.dialog-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px 0 20px;
}

.form-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.form-row > .span-all { grid-column: 1 / -1; }

/* Local CLI: the target Agent field stays in the DOM (its state is kept) but takes no room. */
.ghost { display: none; }

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.form-label {
  font-size: var(--fg2-fs-sub);
  font-weight: 650;
  color: var(--fg2-muted);
  white-space: nowrap;
}

.label-row {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 18px;
  white-space: nowrap;
}

.label-spacer { flex: 1; }

.link-btn {
  padding: 0;
  border: 0;
  background: none;
  color: var(--fg2-focus);
  font-size: var(--fg2-fs-sub);
  cursor: pointer;
}

.link-btn:hover:not(:disabled) { text-decoration: underline; }

.link-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* 모델 구성: one soft card around 종류 / 버전 · 모델 · 추론도. */
.model-group {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin: 0;
  padding: 12px 14px 14px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-inset-radius);
  background: var(--fg2-inset-bg);
}

.group-title {
  margin: 0;
  font-size: var(--fg2-fs-xs);
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg2-text-3);
}

.model-row {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 10px;
}

.command-chip {
  padding: 1px 7px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: var(--fg2-control-radius-pill);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font-size: var(--fg2-fs-xs);
  font-weight: 600;
}

.command-chip.edited {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
  color: var(--fg2-status-warning-fg);
}

.mono,
.command-preview,
.command-input {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: var(--fg2-fs-sub);
}

.command-preview {
  height: var(--fg2-control-h-md);
  line-height: calc(var(--fg2-control-h-md) - 2px);
  padding: 0 var(--fg2-control-pad-x);
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
  color: var(--fg2-text-2);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* 직접 편집 opens under the preview, which stays visible. */
.command-editor {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 6px;
}

.command-input {
  width: 100%;
  min-height: 76px;
  padding: 8px var(--fg2-control-pad-x);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  line-height: 1.45;
  resize: vertical;
}

.command-input:hover:not(:disabled) {
  border-color: var(--fg2-control-hover-border);
}

.command-input:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.command-input:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.key-hint {
  font-size: var(--fg2-fs-xs);
  color: var(--fg2-status-success-fg);
  margin-left: 4px;
}

.text-warn {
  color: var(--fg2-status-warning-fg);
  font-weight: 600;
}

.field-hint {
  display: block;
  font-size: var(--fg2-fs-xs);
}

.segmented {
  display: inline-flex;
  align-self: flex-start;
  padding: 3px;
  border-radius: var(--fg2-control-radius);
  border: 1px solid var(--fg2-line);
  background: var(--fg2-inset-bg);
}

.segmented button {
  height: var(--fg2-control-h-sm);
  padding: 0 14px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: none;
  color: var(--fg2-text-2);
  font-size: var(--fg2-fs-sub);
  font-weight: 650;
  cursor: pointer;
}

.segmented button.on {
  background: var(--fg2-status-info-bg);
  box-shadow: inset 0 0 0 1px var(--fg2-status-info-border);
  color: var(--fg2-text);
}

.form-error {
  white-space: pre-line;
  padding: 8px 12px;
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-status-danger-bg);
  border: 1px solid var(--fg2-status-danger-border);
  color: var(--fg2-status-danger-fg);
  font-size: var(--fg2-fs-sub);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 14px 24px;
  border-top: 1px solid var(--fg2-line);
}
</style>
