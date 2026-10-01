<template>
  <div class="ai-providers-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t(`gui.ai.owned.${scope}.title`) }}</div>
        <div class="page-sub">{{ t(`gui.ai.owned.${scope}.subtitle`) }}</div>
      </div>
      <div class="detail-actions">
        <Button
          variant="primary"
          :disabled="loading || !!loadError || atLimit"
          :title="atLimit ? t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX }) : undefined"
          @click="openAddDialog"
        >
          ＋ {{ t('gui.ai.owned.add') }}
        </Button>
      </div>
    </div>

    <div v-if="loading && !loaded" class="panel-state">
      <LoadingState :label="t('gui.common.loading')" />
    </div>
    <div v-else-if="loadError" class="panel-state">
      <ErrorState :title="t('gui.ai.load_failed_title')" :message="loadError">
        <template #action>
          <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
        </template>
      </ErrorState>
    </div>

    <template v-else>
      <div v-if="error" class="panel-error-alert" role="alert">{{ error }}</div>

      <!-- AI cards; drag to reorder (order = fallback order) -->
      <section class="detail-panel">
        <div class="detail-toolbar">
          <label class="detail-search">
            ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.providers.search_placeholder')" />
          </label>
          <span class="detail-hint">{{ t('gui.ai.providers.custom_hint') }}</span>
          <span class="detail-hint count-hint" :class="{ full: atLimit }">
            {{ t('gui.ai.owned.count', { count: providers.length, max: PROVIDERS_MAX }) }}
          </span>
        </div>

        <div class="provider-cards">
          <div v-if="filteredProviders.length === 0" class="empty-row">
            {{ t('gui.ai.providers.empty_list') }}
          </div>

          <div
            v-for="(item, idx) in filteredProviders"
            :key="item.id"
            class="provider-card"
            :class="{
              'is-dragging': dragId === item.id,
              'is-drag-over': overId === item.id && dragId !== item.id,
              'is-off': !item.enabled,
              'no-default': scope !== 'system',
            }"
            :draggable="canReorder"
            @dragstart="onDragStart(item.id, $event)"
            @dragover="onDragOver(item.id, $event)"
            @drop.prevent="onDrop(item.id)"
            @dragend="onDragEnd"
          >
            <button
              v-if="canReorder"
              type="button"
              class="grip"
              :aria-label="t('gui.ai.providers.reorder_aria', { name: item.name })"
              @keydown.up.prevent="moveProvider(item.id, -1)"
              @keydown.down.prevent="moveProvider(item.id, 1)"
            >
              <AiIcon name="grip" />
            </button>
            <span v-else class="grip grip--static"><AiIcon name="grip" /></span>
            <span class="rank">{{ idx + 1 }}</span>

            <!-- Default AI (system list only; a user's own AI has no system-wide default) -->
            <button
              v-if="scope === 'system'"
              type="button"
              class="radio"
              :class="{ on: defaultId === item.id }"
              :disabled="saving"
              :title="t('gui.ai.providers.set_default')"
              :aria-label="t('gui.ai.providers.set_default')"
              @click="setDefaultProvider(item.id)"
            />

            <span class="avatar-round" :class="getAvatarClass(item.name, item.kind)">
              {{ getInitials(item.name) }}
            </span>
            <div class="name-box">
              <div class="name-title-row">
                <b>{{ item.name }}</b>
                <span class="badge">{{ item.exec_type.toUpperCase() }}</span>
                <span class="role" :class="getKindToneClass(item.kind)">{{ item.kind }}</span>
                <Badge v-if="item.exec_type === 'api' && !item.api_key_set" tone="warning" size="sm">
                  {{ t('gui.ai.providers.credential_required') }}
                </Badge>
                <Badge v-if="probeBadge(item.id)" :tone="probeBadge(item.id)!.tone" size="sm">
                  {{ probeBadge(item.id)!.label }}
                </Badge>
              </div>
              <small>{{ item.id }}</small>
            </div>

            <div class="status-cell">
              <!-- Which projects may use this system AI -->
              <IconButton
                v-if="scope === 'system'"
                class="access-btn"
                :class="{ listed: item.project_access === 'listed' }"
                :label="accessLabel(item)"
                :title="accessLabel(item)"
                size="sm"
                :disabled="saving"
                @click="toggleAccess(item.id)"
              >
                <AiIcon :name="item.project_access === 'listed' ? 'lock' : 'globe'" />
              </IconButton>
              <Toggle
                :model-value="item.enabled"
                :disabled="saving"
                :aria-label="t('gui.ai.providers.toggle_aria', { name: item.name })"
                @update:model-value="(val) => toggleEnabled(item.id, val)"
              />
            </div>

            <div class="row-actions">
              <IconButton
                v-if="scope === 'system' && item.exec_type === 'cli'"
                :label="t('gui.ai.owned.probe')"
                :title="t('gui.ai.owned.probe')"
                size="sm"
                :disabled="probing[item.id]"
                @click="runProbe(item)"
              >
                <AiIcon name="pulse" />
              </IconButton>
              <IconButton
                :label="item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
                :title="item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
                size="sm"
                @click="openCommandInfo(item)"
              >
                <AiIcon :name="item.exec_type === 'cli' ? 'terminal' : 'link'" />
              </IconButton>
              <IconButton :label="t('gui.common.edit')" :title="t('gui.common.edit')" size="sm" @click="openEditDialog(item)">
                <AiIcon name="edit" />
              </IconButton>
              <IconButton
                class="danger-icon"
                :label="t('gui.common.delete')"
                :title="t('gui.common.delete')"
                size="sm"
                @click="confirmDelete(item)"
              >
                <AiIcon name="trash" />
              </IconButton>
            </div>
          </div>
        </div>
      </section>

      <!-- Repeat limit: system setting ai_repeat_count_max, saved as soon as it changes. -->
      <div v-if="scope === 'system' && execPolicy" class="repeat-card">
        <div class="repeat-info">
          <b>{{ t('gui.ai.providers.repeat_max') }}</b>
          <small>{{ t('gui.ai.providers.repeat_max_hint') }}</small>
        </div>
        <div class="repeat-stepper">
          <IconButton
            :label="t('gui.ai.providers.repeat_decrease')"
            variant="secondary"
            size="sm"
            :disabled="repeatMax <= execPolicy.repeat_count_min"
            @click="stepRepeat(-1)"
          >
            <span aria-hidden="true">−</span>
          </IconButton>
          <b aria-live="polite">{{ repeatMax }}</b>
          <IconButton
            :label="t('gui.ai.providers.repeat_increase')"
            variant="secondary"
            size="sm"
            :disabled="repeatMax >= execPolicy.repeat_count_hard_max"
            @click="stepRepeat(1)"
          >
            <span aria-hidden="true">＋</span>
          </IconButton>
        </div>
      </div>
    </template>

    <AiProviderEditDialog
      :open="dialogOpen"
      :provider="editingProvider"
      :saving="saving"
      @close="dialogOpen = false"
      @save="onSaveProvider"
    />

    <AiCommandInfoModal :provider="infoProvider" @close="infoProvider = null" @toast="pushToast" />

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
// Owned AI list (flowgate.v02.0017 TR0003 rev7): 시스템 AI (administrator) or 내 AI (the
// signed-in user's own AI), both tabs of the AI 설정 screen (T0004 §1). A project never edits
// this list; it only allows and picks AI in its usage policy (AiPolicyPanel).
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import IconButton from '@/components/ui/IconButton.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import Toggle from '@/components/ui/Toggle.vue'
import { fetchMyAi, fetchSystemAi, probeSystemAi, saveMyAi, saveRepeatCountMax, saveSystemAi } from '../api'
import type { AiExecutionPolicy, AiProbeResult, ProviderItem } from '../types'
import AiIcon from '../AiIcon.vue'
import { PROVIDERS_MAX } from '../limits'
import AiCommandInfoModal from './AiCommandInfoModal.vue'
import AiProviderEditDialog from './AiProviderEditDialog.vue'
import { getAvatarClass, getInitials, getKindToneClass } from './providerDisplay'

const props = defineProps<{
  scope: 'system' | 'user'
}>()

// Emitted after a successful save so screens showing the policy/effective list can reload.
const emit = defineEmits<{ changed: [] }>()

const { t } = useI18n()

const providers = ref<ProviderItem[]>([])
const defaultId = ref<string | null>(null)
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const saving = ref(false)
const error = ref('')
const searchQuery = ref('')

const dialogOpen = ref(false)
const editingProvider = ref<ProviderItem | null>(null)
const deletingProvider = ref<ProviderItem | null>(null)
const infoProvider = ref<ProviderItem | null>(null)

const toasts = ref<ToastHostItem[]>([])
const probing = ref<Record<string, boolean>>({})
const probeResults = ref<Record<string, AiProbeResult | null>>({})
const execPolicy = ref<AiExecutionPolicy | null>(null)
const repeatMax = ref(0)
const dragId = ref<string | null>(null)
const overId = ref<string | null>(null)
let disposed = false
let repeatTimer: ReturnType<typeof setTimeout> | null = null

function apply(list: ProviderItem[], nextDefault: string | null): void {
  providers.value = list
  defaultId.value = nextDefault
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    if (props.scope === 'system') {
      const data = await fetchSystemAi()
      apply(data.providers ?? [], data.default_provider_id ?? null)
      execPolicy.value = data.execution_policy ?? null
      repeatMax.value = execPolicy.value?.repeat_count_max ?? 0
    } else {
      const data = await fetchMyAi()
      apply(data.providers ?? [], null)
    }
    loaded.value = true
  } catch (err: unknown) {
    loadError.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    loading.value = false
  }
}

watch(() => props.scope, () => {
  loaded.value = false
  probeResults.value = {}
  void load()
}, { immediate: true })

// No save button: the value is written shortly after the last −/＋ click.
function stepRepeat(delta: number): void {
  if (!execPolicy.value) return
  const next = repeatMax.value + delta
  if (next < execPolicy.value.repeat_count_min || next > execPolicy.value.repeat_count_hard_max) return
  repeatMax.value = next
  if (repeatTimer) clearTimeout(repeatTimer)
  repeatTimer = setTimeout(() => void saveRepeat(), 600)
}

async function saveRepeat(): Promise<void> {
  repeatTimer = null
  const current = execPolicy.value
  if (!current || repeatMax.value === current.repeat_count_max) return
  const value = repeatMax.value
  try {
    await saveRepeatCountMax(value)
    if (disposed) return
    execPolicy.value = { ...current, repeat_count_max: value }
    pushToast(t('gui.ai.providers.repeat_saved'), 'success')
  } catch (err: unknown) {
    if (disposed) return
    repeatMax.value = current.repeat_count_max
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'), 'danger')
  }
}

onBeforeUnmount(() => {
  // A pending −/＋ change is still written when the screen closes.
  if (repeatTimer) {
    clearTimeout(repeatTimer)
    void saveRepeat()
  }
  disposed = true
})

const canReorder = computed(() => !searchQuery.value.trim() && !saving.value)

const filteredProviders = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  if (!query) return providers.value
  return providers.value.filter(
    (p) =>
      p.name.toLowerCase().includes(query) ||
      p.kind.toLowerCase().includes(query) ||
      p.id.toLowerCase().includes(query)
  )
})

function pushToast(message: string, tone: ToastHostItem['tone']): void {
  toasts.value = [...toasts.value, { id: `ai-owned-${Date.now()}-${Math.random()}`, message, tone, duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

const atLimit = computed(() => providers.value.length >= PROVIDERS_MAX)

// Every change is a full save of the list (order = fallback order).
async function persist(list: ProviderItem[], nextDefault: string | null = defaultId.value): Promise<void> {
  if (saving.value) return
  if (list.length > PROVIDERS_MAX) {
    error.value = t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX })
    return
  }
  saving.value = true
  error.value = ''
  try {
    if (props.scope === 'system') {
      const data = await saveSystemAi(list, nextDefault)
      apply(data.providers ?? [], data.default_provider_id ?? null)
    } else {
      const data = await saveMyAi(list)
      apply(data.providers ?? [], null)
    }
    emit('changed')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    saving.value = false
  }
}

function setDefaultProvider(providerId: string) {
  if (props.scope !== 'system' || defaultId.value === providerId) return
  void persist(providers.value, providerId)
}

function toggleEnabled(providerId: string, enabled: boolean) {
  void persist(providers.value.map((p) => (p.id === providerId ? { ...p, enabled } : p)))
}

function accessLabel(item: ProviderItem): string {
  return item.project_access === 'listed' ? t('gui.ai.owned.access_listed') : t('gui.ai.owned.access_all')
}

function toggleAccess(providerId: string) {
  void persist(providers.value.map((p) =>
    p.id === providerId ? { ...p, project_access: p.project_access === 'listed' ? 'all' : 'listed' } : p
  ))
}

function reorder(fromId: string, toIndex: number) {
  if (!canReorder.value) return
  const list = [...providers.value]
  const index = list.findIndex((p) => p.id === fromId)
  if (index < 0 || toIndex < 0 || toIndex >= list.length || toIndex === index) return
  const [moved] = list.splice(index, 1)
  list.splice(toIndex, 0, moved)
  void persist(list)
}

// Keyboard alternative to dragging (↑/↓ on the grip).
function moveProvider(providerId: string, delta: number) {
  const index = providers.value.findIndex((p) => p.id === providerId)
  if (index >= 0) reorder(providerId, index + delta)
}

function onDragStart(providerId: string, event: DragEvent) {
  if (!canReorder.value) {
    event.preventDefault()
    return
  }
  dragId.value = providerId
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', providerId)
  }
}

function onDragOver(providerId: string, event: DragEvent) {
  if (!dragId.value) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  overId.value = providerId
}

function onDrop(providerId: string) {
  const from = dragId.value
  onDragEnd()
  if (!from || from === providerId) return
  reorder(from, providers.value.findIndex((p) => p.id === providerId))
}

function onDragEnd() {
  dragId.value = null
  overId.value = null
}

function openAddDialog() {
  if (atLimit.value) return
  editingProvider.value = null
  dialogOpen.value = true
}

function openEditDialog(provider: ProviderItem) {
  editingProvider.value = { ...provider }
  dialogOpen.value = true
}

function openCommandInfo(provider: ProviderItem) {
  infoProvider.value = provider
}

function probeBadge(providerId: string): { tone: 'success' | 'warning' | 'danger' | 'info'; label: string } | null {
  if (probing.value[providerId]) return { tone: 'info', label: t('gui.ai.providers.testing') }
  const result = probeResults.value[providerId]
  if (!result) return null
  if (result.status === 'ok' || result.status === 'launched') return { tone: 'success', label: t('gui.ai.owned.probe_ok') }
  if (result.status === 'command_failed') return { tone: 'danger', label: t('gui.ai.owned.probe_failed') }
  return { tone: 'warning', label: t('gui.ai.owned.probe_skipped') }
}

async function runProbe(provider: ProviderItem) {
  if (probing.value[provider.id]) return
  probing.value = { ...probing.value, [provider.id]: true }
  try {
    const result = await probeSystemAi(provider)
    if (disposed) return
    probeResults.value = { ...probeResults.value, [provider.id]: result }
    const badge = probeBadge(provider.id)
    const detail = result.status === 'command_failed' && result.stderr_tail ? ` — ${result.stderr_tail.slice(-160)}` : ''
    if (badge) pushToast(`${provider.name}: ${badge.label}${detail}`, badge.tone === 'success' ? 'success' : badge.tone)
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.owned.probe_failed'), 'danger')
  } finally {
    probing.value = { ...probing.value, [provider.id]: false }
  }
}

function confirmDelete(provider: ProviderItem) {
  deletingProvider.value = provider
}

function onDeleteConfirmed() {
  const target = deletingProvider.value
  deletingProvider.value = null
  if (!target) return
  const next = providers.value.filter((p) => p.id !== target.id)
  const nextDefault = defaultId.value === target.id ? next[0]?.id ?? null : defaultId.value
  void persist(next, nextDefault)
}

function onSaveProvider(item: ProviderItem) {
  dialogOpen.value = false
  const current = [...providers.value]
  const idx = current.findIndex((p) => p.id === item.id)
  if (idx >= 0) {
    current[idx] = { ...current[idx], ...item }
  } else {
    current.push(item)
  }
  void persist(current, defaultId.value ?? item.id)
}
</script>

<style scoped src="./aiCards.css"></style>

<style scoped>
.panel-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 240px;
}

.provider-card.no-default {
  grid-template-columns: 22px 18px 36px minmax(0, 1fr) auto auto;
}

.access-btn {
  color: #78dcb0;
}

.access-btn.listed {
  color: #f5c46b;
}

.count-hint {
  margin-left: auto;
  white-space: nowrap;
}

.count-hint.full {
  color: #f5c46b;
}
</style>
