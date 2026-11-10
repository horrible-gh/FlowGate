<template>
  <!-- flowgate.chat.0017 CH0002 — limits of AI attachment upload requests. System: the defaults every project inherits.
       Project: an optional override per item — an empty field inherits the system value (shown as its placeholder).
       The server clamps everything to its safety ceilings and validates again; these limits apply to AI requests only
       (a person's own upload keeps its fixed limits). -->
  <div class="fgs-sec" data-card="attachment-requests">
    <div class="fgs-sec-head">
      <h3>{{ t('gui.attachment_request.settings.title') }}</h3>
      <div class="meta">{{ t(scope === 'system' ? 'gui.attachment_request.settings.system_hint' : 'gui.attachment_request.settings.project_hint') }}</div>
      <button
        v-if="!embedded"
        type="button"
        class="fgs-link"
        :aria-expanded="opened"
        data-testid="ar-settings-toggle"
        @click="toggle"
      >{{ opened ? t('gui.attachment_request.settings.close') : t('gui.attachment_request.settings.open') }}</button>
    </div>
    <div v-if="opened || embedded" class="fgs-rows">
      <div v-if="loading" class="fgs-row"><span class="fgs-muted">{{ t('gui.common.loading') }}</span></div>
      <div v-else-if="loadError" class="fgs-row">
        <span class="fgs-error" role="alert" data-testid="ar-settings-load-error">{{ loadError }}</span>
        <button type="button" class="fgs-btn" @click="load">{{ t('gui.common.retry') }}</button>
      </div>
      <template v-else>
        <div v-for="field in FIELDS" :key="field.key" class="fgs-row" :data-field="field.key">
          <div class="fgs-row-label">
            <label :for="`ar-${scope}-${field.key}`">{{ t(`gui.attachment_request.settings.fields.${field.key}`) }}</label>
            <small>
              {{ rangeText(field) }}
              <template v-if="scope === 'project'"> · {{ t(`gui.attachment_request.settings.source.${sources[field.key] ?? 'default'}`) }}</template>
            </small>
          </div>
          <div class="fgs-row-ctrl">
            <input
              :id="`ar-${scope}-${field.key}`"
              v-model="inputs[field.key]"
              class="fgs-input num"
              :class="{ dirty: inputs[field.key] !== loaded[field.key] }"
              inputmode="numeric"
              autocomplete="off"
              :placeholder="placeholderOf(field)"
              :disabled="saving"
              :data-testid="`ar-settings-${field.key}`"
            />
            <span class="fgs-muted">{{ t(`gui.attachment_request.settings.units.${field.unit}`) }}</span>
          </div>
        </div>
        <div class="fgs-row" data-field="blocked_extensions">
          <div class="fgs-row-label">
            <label :for="`ar-${scope}-ext`">{{ t('gui.attachment_request.settings.fields.blocked_extensions') }}</label>
            <!-- System: empty blocks nothing. Project: empty follows the system list; "차단 없음" overrides it with none. -->
            <small>{{ t(scope === 'project' ? 'gui.attachment_request.settings.extensions_hint_project' : 'gui.attachment_request.settings.extensions_hint') }}</small>
          </div>
          <div class="fgs-row-ctrl">
            <input
              :id="`ar-${scope}-ext`"
              v-model="inputs.blocked_extensions"
              class="fgs-input"
              autocomplete="off"
              :placeholder="scope === 'project' ? extensionsText(inherited.blocked_extensions) || t('gui.attachment_request.settings.none') : 'exe, bat'"
              :disabled="saving || (scope === 'project' && noBlock)"
              data-testid="ar-settings-blocked_extensions"
            />
            <label v-if="scope === 'project'" class="fgs-muted">
              <input v-model="noBlock" type="checkbox" :disabled="saving" data-testid="ar-settings-no-block" />
              {{ t('gui.attachment_request.settings.no_block') }}
            </label>
          </div>
        </div>
        <div v-if="problem" class="fgs-row sub"><span class="fgs-error" data-testid="ar-settings-invalid">{{ problem }}</span></div>
        <div v-if="saveError" class="fgs-row sub"><span class="fgs-error" role="alert" data-testid="ar-settings-error">{{ saveError }}</span></div>
        <div class="fgs-row sub">
          <span v-if="saved && !dirty" class="fgs-muted" data-testid="ar-settings-saved">{{ t('gui.settings.saved') }}</span>
          <button
            v-if="scope === 'project'"
            type="button"
            class="fgs-btn"
            :disabled="saving"
            data-testid="ar-settings-inherit-all"
            @click="inheritAll"
          >{{ t('gui.attachment_request.settings.inherit_all') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="!canSave" data-testid="ar-settings-save" @click="save">
            {{ saving ? t('gui.settings.saving') : t('gui.settings.general.save') }}
          </button>
        </div>
        <!-- System only: a project's optional overrides (an empty item inherits the values above). -->
        <div v-if="scope === 'system'" class="fgs-row" data-field="project-overrides">
          <div class="fgs-row-label">
            <label :for="`ar-${scope}-project`">{{ t('gui.attachment_request.settings.project_overrides') }}</label>
            <small>{{ t('gui.attachment_request.settings.project_hint') }}</small>
          </div>
          <div class="fgs-row-ctrl">
            <span v-if="projectsError" class="fgs-error" data-testid="ar-settings-projects-error">{{ projectsError }}</span>
            <select
              v-else
              :id="`ar-${scope}-project`"
              v-model="projectChoice"
              class="fgs-input"
              data-testid="ar-settings-project"
            >
              <option value="">{{ t('gui.attachment_request.settings.choose_project') }}</option>
              <option v-for="p in projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
            </select>
          </div>
        </div>
      </template>
    </div>
    <AttachmentRequestSettingsCard
      v-if="scope === 'system' && opened && projectChoice"
      :key="projectChoice"
      scope="project"
      :project-id="projectChoice"
      embedded
    />
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  attachmentRequestProjectSettings,
  attachmentRequestSaveProjectSettings,
  attachmentRequestSaveSystemSettings,
  attachmentRequestSystemSettings,
  type AttachmentRequestBounds,
  type AttachmentRequestLimits,
} from '@/features/document/api/attachmentRequests'
import { listAllProjects } from '../api'
import type { ProjectRow } from '../types'
import { toProjectRows } from './useProjectList'

const props = defineProps<{
  scope: 'system' | 'project'
  projectId?: string | null
  /** Drawn inside the system card (a project's overrides): always open, no toggle of its own. */
  embedded?: boolean
}>()

const { t } = useI18n()

const MIB = 1024 * 1024

interface Field {
  readonly key:
    | 'max_file_bytes'
    | 'max_attachments_per_doc'
    | 'max_pending_per_target'
    | 'max_requests_per_run'
    | 'max_pending_bytes_per_project'
    | 'max_filename_bytes'
    | 'request_retention_days'
    | 'backup_retention_days'
  readonly unit: 'mb' | 'count' | 'bytes' | 'days'
}

const FIELDS: readonly Field[] = [
  { key: 'max_file_bytes', unit: 'mb' },
  { key: 'max_attachments_per_doc', unit: 'count' },
  { key: 'max_pending_per_target', unit: 'count' },
  { key: 'max_requests_per_run', unit: 'count' },
  { key: 'max_pending_bytes_per_project', unit: 'mb' },
  { key: 'max_filename_bytes', unit: 'bytes' },
  { key: 'request_retention_days', unit: 'days' },
  { key: 'backup_retention_days', unit: 'days' },
]

type Inputs = Record<Field['key'] | 'blocked_extensions', string>

const emptyInputs = (): Inputs => ({
  max_file_bytes: '', max_attachments_per_doc: '', max_pending_per_target: '', max_requests_per_run: '',
  max_pending_bytes_per_project: '', max_filename_bytes: '', request_retention_days: '', backup_retention_days: '',
  blocked_extensions: '',
})

const inputs = reactive<Inputs>(emptyInputs())
const loaded = reactive<Inputs>(emptyInputs())
const bounds = ref<AttachmentRequestBounds>({})
const inherited = ref<AttachmentRequestLimits['values']>({
  max_file_bytes: 0, max_attachments_per_doc: 0, max_pending_per_target: 0, max_requests_per_run: 0,
  max_pending_bytes_per_project: 0, max_filename_bytes: 0, request_retention_days: 0, backup_retention_days: 0,
  blocked_extensions: [],
})
const sources = ref<Record<string, string>>({})
/** Project only: the override "block no extension" (saved as an empty list), apart from an empty field (inherit). */
const noBlock = ref(false)
const loadedNoBlock = ref(false)
const loading = ref(false)
const loadError = ref('')
const saving = ref(false)
const saved = ref(false)
const saveError = ref('')

const toDisplay = (field: Field, value: number): string => (field.unit === 'mb' ? String(Math.round(value / MIB)) : String(value))
const fromDisplay = (field: Field, text: string): number => (field.unit === 'mb' ? Number(text) * MIB : Number(text))
const extensionsText = (list: readonly string[]): string => list.join(', ')

function rangeText(field: Field): string {
  const b = bounds.value[field.key] ?? {}
  if (typeof b.min !== 'number' || typeof b.max !== 'number') return ''
  return t('gui.attachment_request.settings.range', { min: toDisplay(field, b.min), max: toDisplay(field, b.max) })
}

function placeholderOf(field: Field): string {
  if (props.scope === 'project') return toDisplay(field, inherited.value[field.key])
  const b = bounds.value[field.key]
  return typeof b?.default === 'number' ? toDisplay(field, b.default) : ''
}

/** Puts server values into the inputs; a key the object does not carry (a project's inherited item) stays empty. */
function fill(values: Readonly<Record<string, unknown>>) {
  for (const field of FIELDS) {
    const value = values[field.key]
    const text = typeof value === 'number' ? toDisplay(field, value) : ''
    inputs[field.key] = text
    loaded[field.key] = text
  }
  const ext = values.blocked_extensions
  const extText = Array.isArray(ext) ? extensionsText(ext.filter((v): v is string => typeof v === 'string')) : ''
  inputs.blocked_extensions = extText
  loaded.blocked_extensions = extText
  // A project override that is an empty list blocks nothing; a missing key inherits (both show an empty field).
  const none = props.scope === 'project' && Array.isArray(ext) && ext.length === 0
  noBlock.value = none
  loadedNoBlock.value = none
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    if (props.scope === 'system') {
      const view = await attachmentRequestSystemSettings()
      bounds.value = view.bounds
      fill(view.values)
    } else if (props.projectId) {
      const view = await attachmentRequestProjectSettings(props.projectId)
      bounds.value = view.bounds
      inherited.value = view.inherited
      sources.value = { ...view.sources }
      fill(view.overrides)
    }
  } catch {
    loadError.value = t('gui.attachment_request.settings.load_failed')
  } finally {
    loading.value = false
  }
}

const dirty = computed(
  () => noBlock.value !== loadedNoBlock.value || (Object.keys(inputs) as (keyof Inputs)[]).some((key) => inputs[key] !== loaded[key]),
)

const problem = computed(() => {
  for (const field of FIELDS) {
    const text = inputs[field.key].trim()
    if (!text) {
      if (props.scope === 'system') return t('gui.attachment_request.settings.required', { name: t(`gui.attachment_request.settings.fields.${field.key}`) })
      continue
    }
    if (!/^\d+$/.test(text)) return t('gui.attachment_request.settings.integer', { name: t(`gui.attachment_request.settings.fields.${field.key}`) })
    const b = bounds.value[field.key] ?? {}
    const value = fromDisplay(field, text)
    if ((typeof b.min === 'number' && value < b.min) || (typeof b.max === 'number' && value > b.max)) {
      return t('gui.attachment_request.settings.out_of_range', { name: t(`gui.attachment_request.settings.fields.${field.key}`) })
    }
  }
  return ''
})

const canSave = computed(() => !saving.value && dirty.value && !problem.value)

function payload(): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const field of FIELDS) {
    const text = inputs[field.key].trim()
    out[field.key] = text ? fromDisplay(field, text) : null
  }
  const exts = inputs.blocked_extensions
    .split(/[\s,]+/)
    .map((v) => v.trim().replace(/^\./, '').toLowerCase())
    .filter(Boolean)
  if (props.scope === 'project') out.blocked_extensions = noBlock.value ? [] : inputs.blocked_extensions.trim() ? exts : null
  else out.blocked_extensions = exts
  if (props.scope === 'system') for (const key of Object.keys(out)) if (out[key] === null) delete out[key]
  return out
}

async function save() {
  if (!canSave.value) return
  saving.value = true
  saveError.value = ''
  saved.value = false
  try {
    if (props.scope === 'system') {
      const view = await attachmentRequestSaveSystemSettings(payload())
      bounds.value = view.bounds
      fill(view.values)
    } else if (props.projectId) {
      const view = await attachmentRequestSaveProjectSettings(props.projectId, payload())
      inherited.value = view.inherited
      sources.value = { ...view.sources }
      fill(view.overrides)
    }
    saved.value = true
  } catch {
    saveError.value = t('gui.attachment_request.settings.save_failed')
  } finally {
    saving.value = false
  }
}

function inheritAll() {
  for (const key of Object.keys(inputs) as (keyof Inputs)[]) inputs[key] = ''
  noBlock.value = false
}

// Closed until opened: the screens it sits on keep their own reads, and nothing is read for it before that.
const opened = ref(false)
let loadedOnce = false

const projects = ref<ProjectRow[]>([])
const projectsError = ref('')
const projectChoice = ref('')

async function loadProjects() {
  projectsError.value = ''
  try {
    projects.value = (toProjectRows(await listAllProjects()) ?? []).filter((p) => p.is_active)
  } catch {
    projectsError.value = t('gui.attachment_request.settings.projects_failed')
  }
}

function toggle() {
  opened.value = !opened.value
  if (opened.value && !loadedOnce) {
    loadedOnce = true
    void load()
    if (props.scope === 'system') void loadProjects()
  }
}

if (props.embedded) {
  loadedOnce = true
  void load()
}
</script>
