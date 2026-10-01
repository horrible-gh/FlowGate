<template>
  <div class="ai-providers-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.owned.title') }}</div>
      </div>
      <div class="detail-actions">
        <Button
          variant="primary"
          :disabled="loading || !!loadError || addBlocked"
          :title="addBlocked ? t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX }) : undefined"
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
      <!-- One list: system AI first, then my AI. Drag reorders within the same owner (order = fallback order). -->
      <section class="detail-panel">
        <div class="detail-toolbar">
          <div class="segmented" role="group" :aria-label="t('gui.ai.owned.filter_aria')">
            <button
              v-for="key in OWNER_FILTERS"
              :key="key"
              type="button"
              :class="{ on: ownerFilter === key }"
              :aria-pressed="ownerFilter === key"
              @click="ownerFilter = key"
            >
              {{ t(`gui.ai.owned.filter_${key}`) }}
            </button>
          </div>
          <label class="detail-search">
            ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.providers.search_placeholder')" />
          </label>
          <span class="detail-hint count-hint">
            {{ t('gui.ai.owned.count_split', { system: systemList.length, mine: myList.length }) }}
          </span>
        </div>

        <div class="provider-cards">
          <div v-if="rows.length === 0" class="empty-row">
            {{ t('gui.ai.providers.empty_list') }}
          </div>

          <div
            v-for="row in rows"
            :key="row.item.id"
            class="provider-card"
            :class="{
              'is-dragging': dragId === row.item.id,
              'is-drag-over': overId === row.item.id && dragId !== row.item.id && dragOwner === row.owner,
              'is-off': !row.item.enabled,
            }"
            :draggable="canReorder(row.owner)"
            @dragstart="onDragStart(row, $event)"
            @dragover="onDragOver(row, $event)"
            @drop.prevent="onDrop(row)"
            @dragend="onDragEnd"
          >
            <button
              v-if="canReorder(row.owner)"
              type="button"
              class="grip"
              :aria-label="t('gui.ai.providers.reorder_aria', { name: row.item.name })"
              @keydown.up.prevent="moveProvider(row, -1)"
              @keydown.down.prevent="moveProvider(row, 1)"
            >
              <AiIcon name="grip" />
            </button>
            <span v-else class="grip grip--static"><AiIcon name="grip" /></span>
            <span class="rank">{{ row.rank }}</span>

            <!-- My default AI: my pick, or the system default while I follow it. Picking the
                 starred system default means "follow the system default". -->
            <button
              type="button"
              class="radio"
              :class="{ on: effectiveDefaultId === row.item.id, follow: myDefaultId === null }"
              :disabled="saving"
              :title="t('gui.ai.owned.my_default')"
              :aria-label="t('gui.ai.owned.my_default')"
              :aria-pressed="effectiveDefaultId === row.item.id"
              @click="pickMyDefault(row)"
            />

            <span class="avatar-round" :class="getAvatarClass(row.item.name, row.item.kind)">
              {{ getInitials(row.item.name) }}
            </span>
            <div class="name-box">
              <div class="name-title-row">
                <b>{{ row.item.name }}</b>
                <i
                  v-if="row.owner === 'system' && systemDefaultId === row.item.id"
                  class="mode-icon system-default"
                  role="img"
                  :aria-label="t('gui.ai.owned.system_default')"
                  :data-tip="t('gui.ai.owned.system_default')"
                >
                  <AiIcon name="star" />
                </i>
                <i
                  class="mode-icon owner"
                  :class="row.owner"
                  role="img"
                  :aria-label="t(`gui.ai.owned.owner_${row.owner}`)"
                  :data-tip="t(`gui.ai.owned.owner_${row.owner}`)"
                >
                  <AiIcon :name="row.owner === 'system' ? 'building' : 'person'" />
                </i>
                <i
                  class="mode-icon"
                  :class="[row.item.exec_location === 'remote' ? 'remote' : 'local', { unsupported: !isModeSupported(row.item) }]"
                  role="img"
                  :aria-label="modeLabel(row.item)"
                  :data-tip="modeLabel(row.item)"
                >
                  <AiIcon :name="row.item.exec_type === 'cli' ? 'terminal' : 'cloud'" />
                </i>
                <span class="role" :class="getKindToneClass(row.item.kind)">{{ row.item.kind }}</span>
                <Badge v-if="row.item.exec_type === 'api' && !row.item.api_key_set && canEdit(row.owner)" tone="warning" size="sm">
                  {{ t('gui.ai.providers.credential_required') }}
                </Badge>
                <Badge v-if="probeBadge(row.item.id)" :tone="probeBadge(row.item.id)!.tone" size="sm">
                  {{ probeBadge(row.item.id)!.label }}
                </Badge>
              </div>
              <small>{{ row.item.id }}</small>
            </div>

            <div class="status-cell">
              <Toggle
                :model-value="row.item.enabled"
                :disabled="saving || !canEdit(row.owner)"
                :aria-label="t('gui.ai.providers.toggle_aria', { name: row.item.name })"
                @update:model-value="(val) => toggleEnabled(row, val)"
              />
            </div>

            <div class="row-actions">
              <template v-if="canEdit(row.owner)">
                <IconButton
                  v-if="row.owner === 'system' && systemDefaultId !== row.item.id"
                  :label="t('gui.ai.owned.set_system_default')"
                  :title="t('gui.ai.owned.set_system_default')"
                  size="sm"
                  :disabled="saving"
                  @click="setDefaultProvider(row.item.id)"
                >
                  <AiIcon name="star" />
                </IconButton>
                <IconButton
                  v-if="row.owner === 'system' && row.item.exec_type === 'cli' && row.item.exec_location !== 'remote'"
                  :label="t('gui.ai.owned.probe')"
                  :title="t('gui.ai.owned.probe')"
                  size="sm"
                  :disabled="probing[row.item.id]"
                  @click="runProbe(row.item)"
                >
                  <AiIcon name="pulse" />
                </IconButton>
                <IconButton
                  :label="row.item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
                  :title="row.item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
                  size="sm"
                  @click="openCommandInfo(row.item)"
                >
                  <AiIcon :name="row.item.exec_type === 'cli' ? 'terminal' : 'link'" />
                </IconButton>
                <IconButton :label="t('gui.common.edit')" :title="t('gui.common.edit')" size="sm" @click="openEditDialog(row)">
                  <AiIcon name="edit" />
                </IconButton>
                <IconButton
                  class="danger-icon"
                  :label="t('gui.common.delete')"
                  :title="t('gui.common.delete')"
                  size="sm"
                  @click="confirmDelete(row)"
                >
                  <AiIcon name="trash" />
                </IconButton>
              </template>
              <span v-else class="read-only-hint" :title="t('gui.ai.owned.admin_only')">
                <AiIcon name="lock" />
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- Repeat limit: system setting ai_repeat_count_max, saved as soon as it changes. -->
      <div v-if="isAdmin && execPolicy" class="repeat-card">
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
      :owner="editingOwner"
      :owner-choices="ownerChoices"
      :saving="saving"
      @close="dialogOpen = false"
      @save="onSaveProvider"
    />

    <AiCommandInfoModal :provider="infoProvider" @close="infoProvider = null" @toast="pushToast" />

    <ConfirmDialog
      :open="!!deleting"
      :title="t('gui.ai.providers.delete_confirm_title')"
      :message="t('gui.ai.providers.delete_confirm_msg', { name: deleting?.item.name })"
      danger
      @confirm="onDeleteConfirmed"
      @cancel="deleting = null"
    />

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </div>
</template>

<script setup lang="ts">
// AI 관리 list (flowgate.v02.0017 CH0006 (2)): system AI and my AI in ONE list, each card marked
// with its owner and its 2x2 execution mode. System AI is edited by administrators only and read
// by everyone; my AI is edited by its owner. Saves stay per owner (each list is a full save).
// Only failures are announced (an error toast under the header); a change that worked shows in
// the card itself (toggle, star, radio), so there is no success toast.
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import IconButton from '@/components/ui/IconButton.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import Toggle from '@/components/ui/Toggle.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import {
  fetchMyAi,
  fetchSystemAi,
  fetchVisibleSystemAi,
  probeSystemAi,
  saveMyAi,
  saveMyAiDefault,
  saveRepeatCountMax,
  saveSystemAi,
} from '../api'
import type { AiExecutionPolicy, AiOwnerScope, AiProbeResult, ProviderItem } from '../types'
import AiIcon from '../AiIcon.vue'
import { PROVIDERS_MAX } from '../limits'
import AiCommandInfoModal from './AiCommandInfoModal.vue'
import AiProviderEditDialog from './AiProviderEditDialog.vue'
import { getAvatarClass, getInitials, getKindToneClass, isModeSupported, modeKey } from './providerDisplay'

// Emitted after a successful save so screens listing usable AI can reload.
const emit = defineEmits<{ changed: [] }>()

const { t } = useI18n()
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)

interface Row {
  owner: AiOwnerScope
  item: ProviderItem
  rank: number
}

const OWNER_FILTERS = ['all', 'system', 'user'] as const
type OwnerFilter = (typeof OWNER_FILTERS)[number]

const systemList = ref<ProviderItem[]>([])
const myList = ref<ProviderItem[]>([])
const systemDefaultId = ref<string | null>(null)
// My default AI; null = follow the system default (flowgate.v02.0017 CH0006 (3)).
const myDefaultId = ref<string | null>(null)
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const saving = ref(false)
const searchQuery = ref('')
const ownerFilter = ref<OwnerFilter>('all')

const dialogOpen = ref(false)
const editingProvider = ref<ProviderItem | null>(null)
const editingOwner = ref<AiOwnerScope>('user')
const deleting = ref<Row | null>(null)
const infoProvider = ref<ProviderItem | null>(null)

const toasts = ref<ToastHostItem[]>([])
const probing = ref<Record<string, boolean>>({})
const probeResults = ref<Record<string, AiProbeResult | null>>({})
const execPolicy = ref<AiExecutionPolicy | null>(null)
const repeatMax = ref(0)
const dragId = ref<string | null>(null)
const dragOwner = ref<AiOwnerScope | null>(null)
const overId = ref<string | null>(null)
let disposed = false
let repeatTimer: ReturnType<typeof setTimeout> | null = null

function listOf(owner: AiOwnerScope): ProviderItem[] {
  return owner === 'system' ? systemList.value : myList.value
}

function canEdit(owner: AiOwnerScope): boolean {
  return owner === 'user' || isAdmin.value
}

async function load(): Promise<void> {
  loading.value = true
  loadError.value = ''
  try {
    const [system, mine] = await Promise.all([
      isAdmin.value ? fetchSystemAi() : fetchVisibleSystemAi(),
      fetchMyAi(),
    ])
    systemList.value = system.providers ?? []
    systemDefaultId.value = system.default_provider_id ?? null
    myList.value = mine.providers ?? []
    myDefaultId.value = mine.default_provider_id ?? null
    execPolicy.value = isAdmin.value ? system.execution_policy ?? null : null
    repeatMax.value = execPolicy.value?.repeat_count_max ?? 0
    loaded.value = true
  } catch (err: unknown) {
    loadError.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    loading.value = false
  }
}

void load()

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

function modeLabel(item: ProviderItem): string {
  return t(`gui.ai.modes.${modeKey(item)}`)
}

const rows = computed<Row[]>(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const matches = (p: ProviderItem) =>
    !query ||
    p.name.toLowerCase().includes(query) ||
    p.kind.toLowerCase().includes(query) ||
    p.id.toLowerCase().includes(query) ||
    modeLabel(p).toLowerCase().includes(query)
  const owners: AiOwnerScope[] = ownerFilter.value === 'all' ? ['system', 'user'] : [ownerFilter.value]
  return owners.flatMap((owner) =>
    listOf(owner)
      .map((item, index) => ({ owner, item, rank: index + 1 }))
      .filter((row) => matches(row.item))
  )
})

function canReorder(owner: AiOwnerScope): boolean {
  return canEdit(owner) && !searchQuery.value.trim() && !saving.value
}

function pushToast(message: string, tone: ToastHostItem['tone']): void {
  if (tone !== 'danger') return
  toasts.value = [...toasts.value, { id: `ai-owned-${Date.now()}-${Math.random()}`, message, tone, duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

// Owners the add dialog may pick; each list has its own PROVIDERS_MAX.
const ownerChoices = computed<AiOwnerScope[]>(() => {
  const owners: AiOwnerScope[] = isAdmin.value ? ['system', 'user'] : ['user']
  return owners.filter((owner) => listOf(owner).length < PROVIDERS_MAX)
})
const addBlocked = computed(() => ownerChoices.value.length === 0)

// Every change is a full save of that owner's list (order = fallback order).
async function persist(
  owner: AiOwnerScope,
  list: ProviderItem[],
  nextDefault: string | null = systemDefaultId.value,
): Promise<void> {
  if (saving.value) return
  if (list.length > PROVIDERS_MAX) {
    pushToast(t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX }), 'danger')
    return
  }
  saving.value = true
  try {
    if (owner === 'system') {
      const data = await saveSystemAi(list, nextDefault)
      systemList.value = data.providers ?? []
      systemDefaultId.value = data.default_provider_id ?? null
    } else {
      const data = await saveMyAi(list)
      myList.value = data.providers ?? []
      myDefaultId.value = data.default_provider_id ?? null
    }
    emit('changed')
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'), 'danger')
  } finally {
    saving.value = false
  }
}

// The AI my runs start with: my pick when it still exists, otherwise the system default.
const effectiveDefaultId = computed<string | null>(() => {
  const mine = myDefaultId.value
  if (mine && [...systemList.value, ...myList.value].some((p) => p.id === mine)) return mine
  return systemDefaultId.value
})

async function setMyDefault(providerId: string | null): Promise<void> {
  if (saving.value || myDefaultId.value === providerId) return
  saving.value = true
  try {
    const data = await saveMyAiDefault(providerId)
    myDefaultId.value = data.default_provider_id ?? null
    emit('changed')
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'), 'danger')
  } finally {
    saving.value = false
  }
}

// The starred system default stands for "follow the system default": picking it stores no
// pick of my own, so a later change of the system default is followed.
function pickMyDefault(row: Row): void {
  const follows = row.owner === 'system' && row.item.id === systemDefaultId.value
  void setMyDefault(follows ? null : row.item.id)
}

function setDefaultProvider(providerId: string) {
  if (!isAdmin.value || systemDefaultId.value === providerId) return
  void persist('system', systemList.value, providerId)
}

function toggleEnabled(row: Row, enabled: boolean) {
  if (!canEdit(row.owner)) return
  void persist(row.owner, listOf(row.owner).map((p) => (p.id === row.item.id ? { ...p, enabled } : p)))
}

function reorder(owner: AiOwnerScope, fromId: string, toIndex: number) {
  if (!canReorder(owner)) return
  const list = [...listOf(owner)]
  const index = list.findIndex((p) => p.id === fromId)
  if (index < 0 || toIndex < 0 || toIndex >= list.length || toIndex === index) return
  const [moved] = list.splice(index, 1)
  list.splice(toIndex, 0, moved)
  void persist(owner, list)
}

// Keyboard alternative to dragging (↑/↓ on the grip).
function moveProvider(row: Row, delta: number) {
  const index = listOf(row.owner).findIndex((p) => p.id === row.item.id)
  if (index >= 0) reorder(row.owner, row.item.id, index + delta)
}

function onDragStart(row: Row, event: DragEvent) {
  if (!canReorder(row.owner)) {
    event.preventDefault()
    return
  }
  dragId.value = row.item.id
  dragOwner.value = row.owner
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', row.item.id)
  }
}

// A card only accepts a drop from its own owner's list.
function onDragOver(row: Row, event: DragEvent) {
  if (!dragId.value || dragOwner.value !== row.owner) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  overId.value = row.item.id
}

function onDrop(row: Row) {
  const from = dragId.value
  const owner = dragOwner.value
  onDragEnd()
  if (!from || !owner || from === row.item.id || owner !== row.owner) return
  reorder(owner, from, listOf(owner).findIndex((p) => p.id === row.item.id))
}

function onDragEnd() {
  dragId.value = null
  dragOwner.value = null
  overId.value = null
}

function openAddDialog() {
  if (addBlocked.value) return
  editingProvider.value = null
  editingOwner.value = ownerChoices.value[0]
  dialogOpen.value = true
}

function openEditDialog(row: Row) {
  editingProvider.value = { ...row.item }
  editingOwner.value = row.owner
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
    // The card badge shows the result; only a failed command is also announced.
    if (result.status === 'command_failed') {
      const detail = result.stderr_tail ? ` — ${result.stderr_tail.slice(-160)}` : ''
      pushToast(`${provider.name}: ${t('gui.ai.owned.probe_failed')}${detail}`, 'danger')
    }
  } catch (err: unknown) {
    pushToast(err instanceof Error ? err.message : t('gui.ai.owned.probe_failed'), 'danger')
  } finally {
    probing.value = { ...probing.value, [provider.id]: false }
  }
}

function confirmDelete(row: Row) {
  deleting.value = row
}

function onDeleteConfirmed() {
  const target = deleting.value
  deleting.value = null
  if (!target) return
  const next = listOf(target.owner).filter((p) => p.id !== target.item.id)
  if (target.owner === 'system') {
    const nextDefault = systemDefaultId.value === target.item.id ? next[0]?.id ?? null : systemDefaultId.value
    void persist('system', next, nextDefault)
  } else {
    void persist('user', next)
  }
}

function onSaveProvider(item: ProviderItem, owner: AiOwnerScope) {
  dialogOpen.value = false
  if (!canEdit(owner)) return
  const current = [...listOf(owner)]
  const idx = current.findIndex((p) => p.id === item.id)
  if (idx >= 0) {
    current[idx] = { ...current[idx], ...item }
  } else {
    current.push(item)
  }
  void persist(owner, current, owner === 'system' ? systemDefaultId.value ?? item.id : systemDefaultId.value)
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

.count-hint {
  margin-left: auto;
  white-space: nowrap;
}

/* While I follow the system default its radio is shown, but dimmer than my own pick. */
.radio.on.follow {
  border-color: #3f6d9c;
}

.radio.on.follow::after {
  background: #3f6d9c;
}

/* Owner, execution mode and system default as small round icons; the name shows on hover (as in v0.1). */
.mode-icon {
  position: relative;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  display: inline-grid;
  place-items: center;
  flex: none;
  color: #fff;
  cursor: default;
}

.mode-icon :deep(.ai-icon) {
  width: 13px;
  height: 13px;
}

.mode-icon.local { background: #7c3aed; }

.mode-icon.remote { background: #0f8f86; }

.mode-icon.system-default { background: #d97706; }

.mode-icon.owner.system { background: #2f5fb3; }

.mode-icon.owner.user { background: #13837a; }

.mode-icon.unsupported {
  background: #3a4a5c;
  color: #94a8bd;
}

.mode-icon::after {
  content: attr(data-tip);
  position: absolute;
  bottom: calc(100% + 6px);
  left: 50%;
  transform: translateX(-50%);
  padding: 4px 9px;
  border-radius: 6px;
  background: #050d16;
  color: #fff;
  font-size: 11px;
  font-weight: 500;
  white-space: nowrap;
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.12s;
  z-index: 60;
}

.mode-icon:hover::after {
  opacity: 1;
}

.read-only-hint {
  display: inline-grid;
  place-items: center;
  width: 28px;
  height: 28px;
  color: #6f86a0;
}
</style>
