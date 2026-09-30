<template>
  <div class="ai-providers-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.providers.title') }}</div>
        <div class="page-sub">{{ t('gui.ai.providers.subtitle') }}</div>
      </div>
      <div class="detail-actions">
        <Button
          v-if="settings.mode === 'custom'"
          variant="primary"
          @click="openAddDialog"
        >
          ＋ {{ t('gui.ai.providers.add_provider') }}
        </Button>
      </div>
    </div>

    <!-- Project AI mode: system settings / project settings / AI off for this project -->
    <div class="mode-card">
      <div class="mode-info">
        <b>{{ t('gui.ai.providers.mode_setting') }}</b>
      </div>
      <div class="mode-options" role="radiogroup" :aria-label="t('gui.ai.providers.mode_setting')">
        <label
          v-for="option in modeOptions"
          :key="option.value"
          class="mode-radio"
          :class="{ 'is-selected': settings.mode === option.value }"
        >
          <input
            type="radio"
            name="ai-mode"
            :value="option.value"
            :checked="settings.mode === option.value"
            :disabled="saving"
            @change="changeMode(option.value)"
          />
          <span>{{ option.label }}</span>
        </label>
      </div>
    </div>

    <!-- What the current mode means -->
    <div v-if="settings.mode === 'inherit'" class="mode-banner mode-banner--info">
      <div class="mode-banner-text">
        <b>{{ t('gui.ai.providers.inherit_banner_title') }}</b>
        <small>{{ t('gui.ai.providers.inherit_banner_desc', { count: effectiveCount }) }}</small>
      </div>
      <Button variant="secondary" size="sm" :disabled="saving" @click="changeMode('custom')">
        {{ t('gui.ai.providers.switch_to_project') }}
      </Button>
    </div>
    <div v-else-if="settings.mode === 'disabled'" class="mode-banner mode-banner--off">
      <div class="mode-banner-text">
        <b>{{ t('gui.ai.providers.disabled_banner_title') }}</b>
        <small>{{ t('gui.ai.providers.disabled_banner_desc') }}</small>
      </div>
      <Button variant="secondary" size="sm" :disabled="saving" @click="changeMode('inherit')">
        {{ t('gui.ai.providers.enable_with_system') }}
      </Button>
    </div>

    <div v-if="error" class="panel-error-alert">{{ error }}</div>
    <div v-if="settings.mode === 'custom' && hasMissingCredentials" class="panel-warning-alert">
      ⚠ {{ t('gui.ai.providers.credentials_setup_notice') }}
    </div>

    <!-- AI connection list -->
    <section class="detail-panel" :class="{ 'is-project-off': settings.mode === 'disabled' }">
      <div class="detail-toolbar">
        <label class="detail-search">
          ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.providers.search_placeholder')" />
        </label>
        <span class="detail-hint">
          {{
            settings.mode === 'inherit'
              ? t('gui.ai.providers.inherited_hint')
              : settings.mode === 'disabled'
              ? t('gui.ai.providers.disabled_hint')
              : t('gui.ai.providers.custom_hint')
          }}
        </span>
      </div>

      <div class="provider-table">
        <div class="provider-table-head">
          <span></span>
          <span>{{ t('gui.ai.providers.col_default') }}</span>
          <span>{{ t('gui.ai.providers.col_name') }}</span>
          <span>{{ t('gui.ai.providers.col_exec_type') }}</span>
          <span>{{ t('gui.ai.providers.col_kind') }}</span>
          <span>{{ t('gui.ai.providers.col_status') }}</span>
          <span>{{ t('gui.ai.providers.col_actions') }}</span>
        </div>

        <div v-if="filteredProviders.length === 0" class="empty-row">
          {{ t('gui.ai.providers.empty_list') }}
        </div>

        <div
          v-for="(item, idx) in filteredProviders"
          :key="item.id"
          class="provider-detail-row"
        >
          <span class="drag-cell">
            <template v-if="settings.mode === 'custom'">
              <button
                type="button"
                class="icon-order-btn"
                :disabled="Boolean(searchQuery.trim()) || idx === 0 || saving"
                :title="searchQuery.trim() ? t('gui.ai.providers.reorder_disabled_search') : undefined"
                @click="moveProvider(item.id, -1)"
              >
                ▲
              </button>
              <button
                type="button"
                class="icon-order-btn"
                :disabled="Boolean(searchQuery.trim()) || idx === filteredProviders.length - 1 || saving"
                :title="searchQuery.trim() ? t('gui.ai.providers.reorder_disabled_search') : undefined"
                @click="moveProvider(item.id, 1)"
              >
                ▼
              </button>
            </template>
            <span v-else class="drag">⠿</span>
          </span>

          <!-- Default AI connection -->
          <button
            type="button"
            class="radio"
            :class="{ on: currentDefaultId === item.id }"
            :disabled="settings.mode !== 'custom' || saving"
            :title="t('gui.ai.providers.set_default')"
            @click="setDefaultProvider(item.id)"
          />

          <div class="provider-main">
            <span class="avatar-round" :class="getAvatarClass(item.name, item.kind)">
              {{ getInitials(item.name) }}
            </span>
            <div class="name-box">
              <div class="name-title-row">
                <b>{{ item.name }}</b>
                <Badge
                  v-if="settings.mode === 'custom' && item.exec_type === 'api' && !item.api_key_set"
                  tone="warning"
                  size="sm"
                  class="credential-badge"
                >
                  {{ t('gui.ai.providers.credential_required') }}
                </Badge>
                <Badge v-if="testBadge(item.id)" :tone="testBadge(item.id)!.tone" size="sm">
                  {{ testBadge(item.id)!.label }}
                </Badge>
              </div>
              <small>{{ item.kind }} · {{ item.id }}</small>
            </div>
          </div>

          <div>
            <span class="badge">{{ item.exec_type.toUpperCase() }}</span>
          </div>

          <div>
            <span class="role" :class="getKindToneClass(item.kind)">{{ item.kind }}</span>
          </div>

          <!-- Per-connection on/off (project settings only). Project-wide AI off is a mode, not this. -->
          <div class="status-cell">
            <template v-if="settings.mode === 'custom'">
              <Toggle
                :model-value="item.enabled"
                :disabled="saving"
                :aria-label="t('gui.ai.providers.toggle_aria', { name: item.name })"
                @update:model-value="(val) => toggleEnabled(item.id, val)"
              />
              <small class="status-caption">
                {{ item.enabled ? t('gui.ai.providers.connection_on') : t('gui.ai.providers.connection_off') }}
              </small>
            </template>
            <span v-else-if="settings.mode === 'disabled'" class="status-chip off">
              <i></i>{{ t('gui.ai.providers.project_ai_off') }}
            </span>
            <span v-else class="status-chip" :class="{ off: !item.enabled }">
              <i></i>{{ item.enabled ? t('gui.ai.providers.connection_on') : t('gui.ai.providers.connection_off') }}
            </span>
          </div>

          <div class="row-actions">
            <button
              v-if="isUsable(item.id)"
              type="button"
              class="action-btn"
              :disabled="testing[item.id]"
              @click="runConnectionTest(item)"
            >
              {{ testing[item.id] ? t('gui.ai.providers.testing') : t('gui.ai.providers.test_connection') }}
            </button>
            <button type="button" class="action-btn" @click="openCommandInfo(item)">
              {{ item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection') }}
            </button>

            <template v-if="settings.mode === 'custom'">
              <button type="button" class="action-btn" @click="openEditDialog(item)">
                {{ t('gui.common.edit') }}
              </button>
              <button type="button" class="action-btn action-btn--danger" @click="confirmDelete(item)">
                {{ t('gui.common.delete') }}
              </button>
            </template>
          </div>
        </div>
      </div>
    </section>

    <!-- Repeat limit: system setting ai_repeat_count_max (review / restart style choices). -->
    <div v-if="policy" class="mode-card repeat-card">
      <div class="mode-info">
        <b>{{ t('gui.ai.providers.repeat_max') }}</b>
        <small>{{ t('gui.ai.providers.repeat_max_hint') }}</small>
      </div>
      <div class="repeat-stepper">
        <button type="button" class="action-btn" :disabled="savingRepeat || repeatMax <= policy.repeat_count_min" @click="repeatMax--">−</button>
        <b>{{ repeatMax }}</b>
        <button type="button" class="action-btn" :disabled="savingRepeat || repeatMax >= policy.repeat_count_hard_max" @click="repeatMax++">＋</button>
        <Button variant="secondary" size="sm" :loading="savingRepeat" :disabled="repeatMax === policy.repeat_count_max" @click="saveRepeat">
          {{ t('gui.common.save') }}
        </Button>
      </div>
    </div>

    <AiProviderEditDialog
      :open="dialogOpen"
      :provider="editingProvider"
      :saving="saving"
      @close="dialogOpen = false"
      @save="onSaveProvider"
    />

    <!-- Command / connection info -->
    <ModalShell :open="infoModalOpen" size="sm" @close="infoModalOpen = false">
      <template #header="{ titleId }">
        <div class="dialog-header">
          <h2 :id="titleId" class="dialog-title">
            {{ infoProvider?.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection') }}
          </h2>
        </div>
      </template>
      <div class="info-modal-content">
        <template v-if="infoProvider?.exec_type === 'cli'">
          <div class="info-field">
            <div class="info-k-row">
              <span class="info-k">{{ t('gui.ai.providers.field_cli_command') }}:</span>
              <Button
                variant="secondary"
                size="sm"
                :disabled="!infoProvider?.cli_command"
                @click="copyCommand(infoProvider?.cli_command)"
              >
                {{ t('gui.ai.providers.copy_command') }}
              </Button>
            </div>
            <pre class="info-v">{{ infoProvider?.cli_command || t('gui.ai.providers.default_command') }}</pre>
          </div>
        </template>
        <template v-else>
          <div class="info-field">
            <span class="info-k">{{ t('gui.ai.providers.field_api_base_url') }}:</span>
            <span class="info-v">{{ infoProvider?.api_base_url || t('gui.ai.providers.default_value') }}</span>
          </div>
          <div class="info-field">
            <span class="info-k">{{ t('gui.ai.providers.field_api_model') }}:</span>
            <span class="info-v">{{ infoProvider?.api_model || t('gui.ai.providers.default_value') }}</span>
          </div>
          <div class="info-field">
            <span class="info-k">{{ t('gui.ai.providers.field_api_key') }}:</span>
            <span class="info-v" :class="{ 'text-warn': !infoProvider?.api_key_set }">
              {{ infoProvider?.api_key_set ? t('gui.ai.providers.key_configured') : t('gui.ai.providers.credential_required') }}
            </span>
          </div>
        </template>
      </div>
      <template #footer>
        <div class="dialog-actions">
          <Button variant="secondary" @click="infoModalOpen = false">
            {{ t('gui.common.close') }}
          </Button>
        </div>
      </template>
    </ModalShell>

    <ConfirmDialog
      :open="!!deletingProvider"
      :title="t('gui.ai.providers.delete_confirm_title')"
      :message="t('gui.ai.providers.delete_confirm_msg', { name: deletingProvider?.name })"
      danger
      @confirm="onDeleteConfirmed"
      @cancel="deletingProvider = null"
    />

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" @dismiss="dismissToast" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import Toggle from '@/components/ui/Toggle.vue'
import {
  fetchCurrentConnectionTest,
  fetchExecutionPolicy,
  saveProjectAiSettings,
  saveRepeatCountMax,
  startConnectionTest,
} from '../api'
import type { AiConnectionTest, AiExecutionPolicy, AiSettingsMode, ProjectAiSettings, ProviderItem } from '../types'
import AiProviderEditDialog from './AiProviderEditDialog.vue'

const props = defineProps<{
  projectId: string
  initialSettings: ProjectAiSettings
}>()

const emit = defineEmits<{
  changed: []
}>()

const { t } = useI18n()

const settings = ref<ProjectAiSettings>({ ...props.initialSettings })
const saving = ref(false)
const error = ref('')
const searchQuery = ref('')

const dialogOpen = ref(false)
const editingProvider = ref<ProviderItem | null>(null)
const deletingProvider = ref<ProviderItem | null>(null)

const infoModalOpen = ref(false)
const infoProvider = ref<ProviderItem | null>(null)

const toasts = ref<ToastHostItem[]>([])
const testing = ref<Record<string, boolean>>({})
const testResults = ref<Record<string, AiConnectionTest | null>>({})
const policy = ref<AiExecutionPolicy | null>(null)
const repeatMax = ref(0)
const savingRepeat = ref(false)
let disposed = false

async function loadPolicy(): Promise<void> {
  try {
    policy.value = await fetchExecutionPolicy(props.projectId)
    repeatMax.value = policy.value.repeat_count_max
  } catch {
    policy.value = null
  }
}

async function saveRepeat(): Promise<void> {
  if (!policy.value) return
  savingRepeat.value = true
  try {
    await saveRepeatCountMax(repeatMax.value)
    await loadPolicy()
    pushToast(t('gui.ai.providers.repeat_saved'), 'success')
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'), 'danger')
  } finally {
    savingRepeat.value = false
  }
}

watch(() => props.projectId, () => void loadPolicy(), { immediate: true })

watch(
  () => props.initialSettings,
  (val) => {
    settings.value = { ...val }
  },
  { deep: true }
)

onBeforeUnmount(() => {
  disposed = true
})

const modeOptions = computed<{ value: AiSettingsMode; label: string }[]>(() => [
  { value: 'inherit', label: t('gui.ai.providers.mode_inherit') },
  { value: 'custom', label: t('gui.ai.providers.mode_custom') },
  { value: 'disabled', label: t('gui.ai.providers.mode_disabled') },
])

const effectiveCount = computed(() => settings.value.effective?.providers?.length ?? 0)

const currentDefaultId = computed(() =>
  settings.value.mode === 'custom' ? settings.value.default_provider_id : settings.value.effective?.default_provider_id
)

const hasMissingCredentials = computed(() => {
  return (settings.value.providers ?? []).some((p) => p.exec_type === 'api' && !p.api_key_set)
})

// custom: the project's own list (with per-connection on/off)
// inherit: the effective list the project is actually using (read only)
// disabled: the preserved project list, shown dimmed -- AI is off for the whole project
const activeProvidersList = computed<ProviderItem[]>(() => {
  if (settings.value.mode === 'custom' || settings.value.mode === 'disabled') {
    return settings.value.providers ?? []
  }
  return settings.value.effective?.providers ?? []
})

const filteredProviders = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return activeProvidersList.value
  return activeProvidersList.value.filter(
    (p) =>
      p.name.toLowerCase().includes(query) ||
      p.kind.toLowerCase().includes(query) ||
      p.id.toLowerCase().includes(query)
  )
})

function isUsable(providerId: string): boolean {
  return (settings.value.effective?.providers ?? []).some((p) => p.id === providerId)
}

function pushToast(message: string, tone: ToastHostItem['tone']): void {
  toasts.value = [...toasts.value, { id: `ai-conn-${Date.now()}-${Math.random()}`, message, tone, duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

async function persist(newSettings: Partial<ProjectAiSettings>) {
  if (!props.projectId || saving.value) return
  saving.value = true
  error.value = ''
  try {
    const payload = {
      mode: newSettings.mode ?? settings.value.mode,
      providers: newSettings.providers !== undefined ? newSettings.providers : settings.value.providers,
      default_provider_id:
        newSettings.default_provider_id !== undefined
          ? newSettings.default_provider_id
          : settings.value.default_provider_id,
    }
    const updated = await saveProjectAiSettings(props.projectId, payload)
    settings.value = updated
    emit('changed')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    saving.value = false
  }
}

function changeMode(mode: AiSettingsMode) {
  if (mode === settings.value.mode) return
  let providers = settings.value.providers
  let defaultId = settings.value.default_provider_id
  if (mode === 'custom' && (!providers || providers.length === 0)) {
    // Switching from system settings to project settings starts from what the project is
    // using now. The GET response exposes only api_key_set/hint, never the secret, so cloned
    // API connections are marked as needing a project key instead of implying a copied key.
    providers = (settings.value.effective?.providers ?? []).map((p) => {
      if (p.exec_type === 'api') {
        return { ...p, api_key: undefined, api_key_set: false, api_key_hint: null }
      }
      return { ...p }
    })
    defaultId = settings.value.effective?.default_provider_id ?? providers[0]?.id ?? null
  }
  void persist({ mode, providers, default_provider_id: defaultId })
}

function setDefaultProvider(providerId: string) {
  if (settings.value.mode !== 'custom') return
  void persist({ default_provider_id: providerId })
}

function toggleEnabled(providerId: string, enabled: boolean) {
  if (settings.value.mode !== 'custom' || !settings.value.providers) return
  const next = settings.value.providers.map((p) =>
    p.id === providerId ? { ...p, enabled } : p
  )
  void persist({ providers: next })
}

function moveProvider(providerId: string, delta: number) {
  if (settings.value.mode !== 'custom' || !settings.value.providers) return
  if (searchQuery.value.trim()) return
  const list = [...settings.value.providers]
  const index = list.findIndex((p) => p.id === providerId)
  if (index < 0) return
  const target = index + delta
  if (target < 0 || target >= list.length) return
  const temp = list[index]
  list[index] = list[target]
  list[target] = temp
  void persist({ providers: list })
}

function openAddDialog() {
  editingProvider.value = null
  dialogOpen.value = true
}

function openEditDialog(provider: ProviderItem) {
  editingProvider.value = { ...provider }
  dialogOpen.value = true
}

function openCommandInfo(provider: ProviderItem) {
  infoProvider.value = provider
  infoModalOpen.value = true
}

async function writeClipboard(text: string): Promise<void> {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(text)
    return
  }
  // Non-secure contexts (plain http on a LAN address) have no Clipboard API.
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.opacity = '0'
  document.body.appendChild(area)
  area.select()
  try {
    if (!document.execCommand('copy')) throw new Error('copy command rejected')
  } finally {
    document.body.removeChild(area)
  }
}

async function copyCommand(command: string | null | undefined) {
  if (!command) return
  try {
    await writeClipboard(command)
    pushToast(t('gui.ai.providers.copy_success'), 'success')
  } catch {
    pushToast(t('gui.ai.providers.copy_failed'), 'danger')
  }
}

function testBadge(providerId: string): { tone: 'success' | 'warning' | 'danger' | 'info'; label: string } | null {
  if (testing.value[providerId]) return { tone: 'info', label: t('gui.ai.providers.testing') }
  const result = testResults.value[providerId]
  if (!result) return null
  if (result.lifecycle === 'completed') {
    return result.result === 'responded'
      ? { tone: 'success', label: t('gui.ai.providers.test_responded') }
      : { tone: 'warning', label: t('gui.ai.providers.test_no_response') }
  }
  if (result.lifecycle === 'failed') return { tone: 'danger', label: t('gui.ai.providers.test_failed') }
  return null
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

async function runConnectionTest(provider: ProviderItem) {
  if (testing.value[provider.id]) return
  testing.value = { ...testing.value, [provider.id]: true }
  try {
    let current: AiConnectionTest | null = await startConnectionTest(props.projectId, provider.id)
    for (let i = 0; i < 45 && current && !['completed', 'failed', 'cancelled'].includes(current.lifecycle); i++) {
      await sleep(2000)
      if (disposed) return
      current = (await fetchCurrentConnectionTest(props.projectId, provider.id)).current
    }
    testResults.value = { ...testResults.value, [provider.id]: current }
    const badge = testBadge(provider.id)
    if (badge) pushToast(`${provider.name}: ${badge.label}`, badge.tone === 'success' ? 'success' : badge.tone)
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.test_failed'), 'danger')
  } finally {
    testing.value = { ...testing.value, [provider.id]: false }
  }
}

function confirmDelete(provider: ProviderItem) {
  deletingProvider.value = provider
}

function onDeleteConfirmed() {
  if (!deletingProvider.value || settings.value.mode !== 'custom' || !settings.value.providers) return
  const next = settings.value.providers.filter((p) => p.id !== deletingProvider.value!.id)
  let nextDefault = settings.value.default_provider_id
  if (nextDefault === deletingProvider.value.id) {
    nextDefault = next[0]?.id ?? null
  }
  deletingProvider.value = null
  void persist({ providers: next, default_provider_id: nextDefault })
}

function onSaveProvider(item: ProviderItem) {
  dialogOpen.value = false
  const current = settings.value.providers ? [...settings.value.providers] : []
  const idx = current.findIndex((p) => p.id === item.id)
  if (idx >= 0) {
    current[idx] = item
  } else {
    current.push(item)
  }
  let defId = settings.value.default_provider_id
  if (!defId) defId = item.id
  void persist({ providers: current, default_provider_id: defId })
}

function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function getAvatarClass(name: string, kind?: string): string {
  const n = (name + ' ' + (kind || '')).toLowerCase()
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}

function getKindToneClass(kind: string): string {
  const k = (kind || '').toLowerCase()
  if (k.includes('claude') || k.includes('anthropic')) return 'indigo'
  if (k.includes('openai') || k.includes('gpt')) return 'cyan'
  if (k.includes('custom')) return 'purple'
  return ''
}
</script>

<style scoped>
.ai-providers-panel {
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}

.detail-head {
  display: flex;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 16px;
}

.page-title {
  font-size: 21px;
  font-weight: 850;
  margin: 0 0 6px;
  color: var(--fg2-text, #edf3fa);
}

.page-sub {
  color: #b9c8d8;
  font-size: 14.5px;
  margin: 0;
}

.detail-actions {
  margin-left: auto;
  display: flex;
  gap: 8px;
}

.repeat-card {
  margin-top: 14px;
  margin-bottom: 0;
}

.repeat-stepper {
  display: flex;
  align-items: center;
  gap: 10px;
}

.repeat-stepper b {
  min-width: 28px;
  text-align: center;
  font-size: 16px;
}

.mode-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  margin-bottom: 14px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  background: #0d1b2b;
}

.mode-info b {
  display: block;
  font-size: 13.5px;
  color: #eaf1f8;
}

.mode-info small {
  display: block;
  margin-top: 3px;
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.mode-options {
  display: flex;
  gap: 12px;
}

.mode-radio {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 6px;
  border: 1px solid var(--fg2-line);
  background: #102238;
  color: #c8d7e7;
  font-size: 12.5px;
  font-weight: 600;
  cursor: pointer;
  transition: 0.16s ease;
}

.mode-radio.is-selected {
  border-color: rgba(75, 163, 255, 0.58);
  background: linear-gradient(180deg, #1c4e84, #153b65);
  color: #edf6ff;
}

.detail-panel {
  border: 1px solid var(--fg2-line);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(13, 26, 40, 0.94), rgba(10, 22, 35, 0.92));
  overflow: hidden;
  margin-bottom: 14px;
}

.detail-toolbar {
  height: 52px;
  padding: 0 16px;
  display: flex;
  align-items: center;
  gap: 14px;
  border-bottom: 1px solid var(--fg2-line);
}

.detail-search {
  height: 34px;
  min-width: 280px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 11px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: #0c1b2c;
  color: #8399b0;
  font-size: 13px;
}

.detail-search input {
  min-width: 0;
  flex: 1;
  border: 0;
  outline: 0;
  background: transparent;
  color: #dce7f3;
  font-size: 12.5px;
}

.detail-hint {
  font-size: 11.5px;
  color: var(--fg2-muted);
}

.provider-table-head,
.provider-detail-row {
  display: grid;
  grid-template-columns: 44px 50px minmax(220px, 1.6fr) 72px 100px 120px minmax(280px, auto);
  gap: 10px;
  align-items: center;
}

.provider-table-head {
  height: 38px;
  padding: 0 16px;
  background: #0b1827;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 10.5px;
  color: #6f86a0;
  font-weight: 800;
}

.provider-detail-row {
  min-height: 64px;
  padding: 8px 16px;
  border-bottom: 1px solid rgba(154, 181, 210, 0.08);
}

.provider-detail-row:last-child {
  border-bottom: 0;
}

.empty-row {
  padding: 36px 16px;
  text-align: center;
  color: var(--fg2-muted);
  font-size: 13px;
}

.drag-cell {
  display: flex;
  gap: 3px;
}

.icon-order-btn {
  background: none;
  border: 0;
  color: #7189a3;
  cursor: pointer;
  font-size: 10px;
  padding: 2px;
}

.icon-order-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}

.drag {
  color: #7189a3;
  font-size: 16px;
}

.radio {
  width: 17px;
  height: 17px;
  border-radius: 50%;
  border: 1px solid #56718f;
  background: none;
  position: relative;
  cursor: pointer;
  padding: 0;
}

.radio.on {
  border-color: #55a9ff;
}

.radio.on::after {
  content: "";
  position: absolute;
  inset: 3px;
  border-radius: 50%;
  background: #55a9ff;
}

.provider-main {
  display: flex;
  align-items: center;
  gap: 11px;
  min-width: 0;
}

.name-box b {
  font-size: 13px;
  color: #edf3fa;
  display: block;
}

.name-box small {
  margin-top: 3px;
  font-size: 10.5px;
  color: var(--fg2-muted);
  display: block;
}

.name-title-row {
  display: flex;
  align-items: center;
  gap: 6px;
}

.panel-warning-alert {
  padding: 10px 14px;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.4);
  border-radius: 8px;
  color: var(--fg2-amber-dim, #d97706);
  font-size: 13px;
  font-weight: 500;
  margin-bottom: 12px;
}

.text-warn {
  color: var(--fg2-amber-dim, #d97706);
  font-weight: 600;
}

.avatar-round {
  width: 36px;
  height: 36px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-weight: 800;
  font-size: 11px;
  color: #fff;
  flex: none;
}

.av-cs, .av-co, .av-ch { background: linear-gradient(145deg, #d98466, #8d3f31); }
.av-og, .av-o4, .av-lu { background: linear-gradient(145deg, #67a2ff, #3667d7); }
.av-gp { background: linear-gradient(145deg, #9b79ff, #6f49d7); }
.av-l3 { background: linear-gradient(145deg, #58adff, #2e77c6); }
.av-te { background: linear-gradient(145deg, #e6b089, #b97851); }

.badge {
  height: 20px;
  padding: 0 7px;
  border-radius: 5px;
  background: #11243a;
  border: 1px solid rgba(111, 141, 176, 0.18);
  font-size: 10.5px;
  color: #d7e6f5;
  font-weight: 600;
}

.role {
  height: 24px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(112, 151, 193, 0.2);
  background: #11263c;
  color: #dce9f8;
  font-size: 11.5px;
  display: inline-flex;
  align-items: center;
}

.role.cyan { background: #102b2d; border-color: rgba(73, 223, 208, 0.2); color: #95eee5; }
.role.purple { background: #241a3d; border-color: rgba(163, 123, 255, 0.22); color: #d0bdfd; }
.role.indigo { background: #132744; border-color: rgba(91, 146, 255, 0.22); color: #b4d1ff; }

.status-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #78dcb0;
  font-size: 12px;
}

.status-chip.off {
  color: #94a8bd;
}

.status-chip i {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: currentColor;
}

.row-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.action-btn {
  height: 28px;
  padding: 0 9px;
  border: 1px solid var(--fg2-line);
  border-radius: 6px;
  background: #102238;
  color: #b8cadc;
  font-size: 11px;
  font-weight: 600;
  cursor: pointer;
  transition: 0.16s ease;
}

.action-btn:hover {
  background: #16304d;
}

.action-btn--danger {
  color: #fca5a5;
  border-color: rgba(239, 68, 68, 0.25);
}

.execution-policy {
  margin-top: auto;
  min-height: 70px;
  padding: 14px 18px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  background: #0d1b2b;
  display: flex;
  align-items: center;
  gap: 20px;
}

.execution-policy b {
  font-size: 13px;
  display: block;
  color: #edf3fa;
}

.execution-policy small {
  margin-top: 4px;
  color: var(--fg2-muted);
  font-size: 11px;
  display: block;
}

.repeat-box {
  margin-left: auto;
  display: flex;
  align-items: center;
  border: 1px solid var(--fg2-line);
  border-radius: 7px;
  overflow: hidden;
}

.repeat-box button,
.repeat-box span {
  height: 30px;
  min-width: 32px;
  border: 0;
  border-right: 1px solid var(--fg2-line);
  background: #102238;
  display: grid;
  place-items: center;
  color: #c8d7e7;
  font-size: 12px;
}

.repeat-box button {
  cursor: pointer;
}

.repeat-box .repeat-val {
  min-width: 40px;
  font-weight: 700;
}

.repeat-box .save-btn {
  min-width: 54px;
  background: #174775;
  color: #dff0ff;
  border-right: 0;
  font-weight: 700;
}

.dialog-header {
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.dialog-title {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
}

.info-modal-content {
  padding: 18px 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.info-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.info-k {
  font-size: 11.5px;
  color: var(--fg2-muted);
  font-weight: 650;
}

.info-v {
  font-size: 12.5px;
  color: var(--fg2-text);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: var(--fg2-font-mono, monospace);
  background: #091522;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--fg2-line);
}

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  padding: 12px 20px;
  border-top: 1px solid var(--fg2-line);
}

.panel-error-alert {
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(239, 68, 68, 0.15);
  border: 1px solid rgba(239, 68, 68, 0.3);
  color: #fca5a5;
  font-size: 12px;
  margin-bottom: 12px;
}

.mode-banner {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 12px 16px;
  margin-bottom: 14px;
  border-radius: 10px;
  border: 1px solid var(--fg2-line);
}

.mode-banner--info {
  border-color: rgba(75, 163, 255, 0.35);
  background: rgba(28, 101, 197, 0.12);
}

.mode-banner--off {
  border-color: rgba(148, 168, 189, 0.35);
  background: rgba(148, 168, 189, 0.08);
}

.mode-banner-text {
  flex: 1;
  min-width: 0;
}

.mode-banner-text b {
  display: block;
  font-size: 13.5px;
  color: #eaf1f8;
}

.mode-banner-text small {
  display: block;
  margin-top: 3px;
  font-size: 12px;
  color: #b9c8d8;
  line-height: 1.45;
}

.detail-panel.is-project-off .provider-detail-row {
  opacity: 0.55;
}

.status-cell {
  display: flex;
  align-items: center;
  gap: 8px;
}

.status-caption {
  font-size: 11px;
  color: var(--fg2-muted);
}

.info-k-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.action-btn:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
