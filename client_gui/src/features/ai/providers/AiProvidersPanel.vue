<template>
  <div class="ai-providers-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.owned.title') }}</div>
      </div>
      <div class="detail-actions">
        <Button
          variant="primary"
          :disabled="loading || saving || !!deleting || !!loadError || addBlocked"
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
      <!-- One list over system AI and my AI in my order: any card can be dragged above any other.
           For an administrator the system AI's relative order is also the fallback order (linked). -->
      <section class="detail-panel">
        <div class="detail-toolbar">
          <div v-if="!mineOnly" class="segmented" role="group" :aria-label="t('gui.ai.owned.filter_aria')">
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
            {{ mineOnly
              ? t('gui.ai.owned.count', { count: myList.length, max: PROVIDERS_MAX })
              : t('gui.ai.owned.count_split', { system: systemList.length, mine: myList.length }) }}
          </span>
        </div>
        <p class="detail-hint order-hint" data-test="ai-order-hint">
          {{ isAdmin && !mineOnly ? t('gui.ai.owned.order_hint_admin') : t('gui.ai.owned.order_hint') }}
        </p>

        <div class="provider-cards">
          <div v-if="rows.length === 0" class="empty-row">
            {{ mineOnly && !myList.length ? t('gui.ai.owned.empty_mine') : t('gui.ai.providers.empty_list') }}
          </div>

          <div
            v-for="row in rows"
            :key="row.item.id"
            class="provider-card"
            :class="{
              'is-dragging': dragId === row.item.id,
              'is-drag-over': overId === row.item.id && dragId !== row.item.id,
              'is-off': !row.item.enabled,
            }"
            :draggable="canReorder()"
            @dragstart="onDragStart(row, $event)"
            @dragover="onDragOver(row, $event)"
            @drop.prevent="onDrop(row)"
            @dragend="onDragEnd"
          >
            <button
              v-if="canReorder()"
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
              :disabled="saving || !!deleting"
              :title="t('gui.ai.owned.my_default')"
              :aria-label="t('gui.ai.owned.my_default')"
              :aria-pressed="effectiveDefaultId === row.item.id"
              @click="pickMyDefault(row)"
            />

            <!-- v02test.0096: 📌 pins this AI to 자주 쓰는 AI at the top of the AI picker. A personal display choice of
                 its own (useAiFavorites); the radio (my default) and the star (system default) keep their meaning. -->
            <button
              type="button"
              class="pin"
              :class="{ on: favorites.isPinned(row.item.id) }"
              data-test="ai-pin-toggle"
              :title="favorites.isPinned(row.item.id) ? t('gui.ai.owned.unpin') : t('gui.ai.owned.pin')"
              :aria-label="t('gui.ai.owned.pin_aria', { name: row.item.name })"
              :aria-pressed="favorites.isPinned(row.item.id)"
              @click="favorites.togglePin(row.item.id)"
            >
              <span aria-hidden="true">📌</span>
            </button>

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
                  :disabled="saving || !!deleting"
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
                <IconButton :label="t('gui.common.edit')" :title="t('gui.common.edit')" size="sm" :disabled="saving || !!deleting" @click="openEditDialog(row)">
                  <AiIcon name="edit" />
                </IconButton>
                <IconButton
                  class="danger-icon"
                  :label="t('gui.common.delete')"
                  :title="t('gui.common.delete')"
                  size="sm"
                  :disabled="saving || !!deleting"
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

      <!-- v02test.0096 F96-3: the repeat limit and title AI cards that sat here moved to the 기타 설정 tab
           (settings/AiOtherSettingsPanel.vue); this tab is the list only. -->
    </template>

    <AiProviderEditDialog
      :open="dialogOpen"
      :provider="editingProvider"
      :owner="editingOwner"
      :owner-choices="ownerChoices"
      :saving="saving"
      :existing-providers-by-owner="{ system: systemList, user: myList }"
      :save-error="dialogSaveError"
      :name-save-error="dialogNameError"
      @close="dialogOpen = false"
      @name-change="clearDialogErrors"
      @save="onSaveProvider"
    />

    <AiCommandInfoModal :provider="infoProvider" @close="infoProvider = null" @toast="pushToast" />

    <ConfirmDialog
      :open="!!deleting"
      :title="t('gui.ai.providers.delete_confirm_title')"
      :message="t('gui.ai.providers.delete_confirm_msg', { name: deleting?.item.name })"
      danger
      icon="trash"
      :busy="saving"
      :confirm-label="t('gui.common.delete')"
      :cancel-label="t('gui.common.cancel')"
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
// the list is shown in my own order (me/ai-order) and any card can be dragged above any other —
// system or mine, local or remote. That order is what every AI selection list shows. An ordinary user's drag changes
// only their order; an administrator's drag also saves the system AI's relative order as the fallback order (the
// server links the two in one transaction and says so, and this screen then tells the administrator).
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
import { useAiFavorites } from '@shared/aiFavorites'
import {
  AiRequestError,
  fetchMyAi,
  fetchMyAiOrder,
  fetchSystemAi,
  fetchVisibleSystemAi,
  probeSystemAi,
  saveMyAi,
  saveMyAiDefault,
  saveMyAiOrder,
  saveSystemAi,
} from '../api'
import '../aiBaseSelect.css'
import type {
  AiOwnerScope,
  AiProbeResult,
  ProviderItem,
} from '../types'
import AiIcon from '../AiIcon.vue'
import { PROVIDERS_MAX } from '../limits'
import AiCommandInfoModal from './AiCommandInfoModal.vue'
import AiProviderEditDialog from './AiProviderEditDialog.vue'
import { getAvatarClass, getInitials, getKindToneClass, isModeSupported, modeKey } from './providerDisplay'

// Emitted after a successful save so screens listing usable AI can reload.
const emit = defineEmits<{ changed: [] }>()
// The Designer's "내 AI" window lists my own AI only (no system AI, no owner filter, adds are mine);
// the AI 관리 screen keeps the full list.
const props = defineProps<{ mineOnly?: boolean }>()

const { t } = useI18n()
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)
// 📌 pins (v02test.0096): stored apart from the AI list, per signed-in person; the composer's AI picker reads them.
const favorites = useAiFavorites(() => user.value?.username)

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
// My default AI; null = follow the system default (the earlier change the discussion ).
const myDefaultId = ref<string | null>(null)
const loading = ref(false)
const loaded = ref(false)
const loadError = ref('')
const saving = ref(false)
const searchQuery = ref('')
const ownerFilter = ref<OwnerFilter>('all')

const dialogOpen = ref(false)
const dialogSaveError = ref('')
const dialogNameError = ref('')
const editingProvider = ref<ProviderItem | null>(null)
const editingOwner = ref<AiOwnerScope>('user')
const deleting = ref<Row | null>(null)
const infoProvider = ref<ProviderItem | null>(null)

const toasts = ref<ToastHostItem[]>([])
const probing = ref<Record<string, boolean>>({})
const probeResults = ref<Record<string, AiProbeResult | null>>({})
const dragId = ref<string | null>(null)
const overId = ref<string | null>(null)
// My display order (provider ids); ids it does not name are shown after it (system, then mine).
const order = ref<string[]>([])
let disposed = false

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
    // My order is read beside the lists and never holds them up: until it answers (or if it fails) the list shows
    // system AI first, then mine.
    void loadOrder()
    const [system, mine] = await Promise.all([
      isAdmin.value ? fetchSystemAi() : fetchVisibleSystemAi(),
      fetchMyAi(),
    ])
    systemList.value = system.providers ?? []
    systemDefaultId.value = system.default_provider_id ?? null
    myList.value = mine.providers ?? []
    myDefaultId.value = mine.default_provider_id ?? null
    loaded.value = true
  } catch (err: unknown) {
    loadError.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    loading.value = false
  }
}

let orderRequest = 0
async function loadOrder(): Promise<void> {
  const request = ++orderRequest
  try {
    const data = await fetchMyAiOrder()
    // A drag saved meanwhile is newer than this read.
    if (!disposed && request === orderRequest && !saving.value) order.value = data.order ?? []
  } catch {
    // Keep the default order; a drag still saves a full order.
  }
}

void load()

onBeforeUnmount(() => {
  disposed = true
})

function modeLabel(item: ProviderItem): string {
  return t(`gui.ai.modes.${modeKey(item)}`)
}

// Every AI once, in my order: the saved ids that still exist, then the rest (system order, then mine).
const orderedRows = computed<Row[]>(() => {
  const byId = new Map<string, { owner: AiOwnerScope; item: ProviderItem }>()
  for (const item of systemList.value) byId.set(item.id, { owner: 'system', item })
  for (const item of myList.value) if (!byId.has(item.id)) byId.set(item.id, { owner: 'user', item })
  const ids = [...order.value.filter((id) => byId.has(id)), ...byId.keys()]
  return [...new Set(ids)].map((id, index) => ({ ...byId.get(id)!, rank: index + 1 }))
})

const rows = computed<Row[]>(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const matches = (p: ProviderItem) =>
    !query ||
    p.name.toLowerCase().includes(query) ||
    p.kind.toLowerCase().includes(query) ||
    p.id.toLowerCase().includes(query) ||
    modeLabel(p).toLowerCase().includes(query)
  if (props.mineOnly) {
    // Only my AI, numbered among themselves; a drag still moves within my whole order.
    return orderedRows.value
      .filter((row) => row.owner === 'user')
      .map((row, index) => ({ ...row, rank: index + 1 }))
      .filter((row) => matches(row.item))
  }
  return orderedRows.value.filter((row) => (ownerFilter.value === 'all' || row.owner === ownerFilter.value) && matches(row.item))
})

// My order is mine to change for every card (system or mine); a search hides cards, so it pauses dragging.
function canReorder(): boolean {
  return !searchQuery.value.trim() && !saving.value && !deleting.value
}

function pushToast(message: string, tone: ToastHostItem['tone'], announce = false): void {
  // Only failures are announced, plus the one change the card does not show: the fallback order moving with my order.
  if (tone !== 'danger' && !announce) return
  toasts.value = [...toasts.value, { id: `ai-owned-${Date.now()}-${Math.random()}`, message, tone, duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

// Owners the add dialog may pick; each list has its own PROVIDERS_MAX.
const ownerChoices = computed<AiOwnerScope[]>(() => {
  const owners: AiOwnerScope[] = isAdmin.value && !props.mineOnly ? ['system', 'user'] : ['user']
  return owners.filter((owner) => listOf(owner).length < PROVIDERS_MAX)
})
const addBlocked = computed(() => ownerChoices.value.length === 0)

// Every change is a full save of that owner's list (order = fallback order).
async function persist(
  owner: AiOwnerScope,
  list: ProviderItem[],
  nextDefault: string | null = systemDefaultId.value,
  showError = true,
): Promise<unknown | null> {
  if (saving.value) {
    const error = new Error(t('gui.ai.providers.save_in_progress'))
    if (showError) pushToast(error.message, 'danger')
    return error
  }
  if (list.length > PROVIDERS_MAX) {
    const error = new Error(t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX }))
    if (showError) pushToast(error.message, 'danger')
    return error
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
    return null
  } catch (err: unknown) {
    if (showError) pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'), 'danger')
    return err
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
  if (saving.value || deleting.value || myDefaultId.value === providerId) return
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
  if (saving.value || deleting.value || !isAdmin.value || systemDefaultId.value === providerId) return
  void persist('system', systemList.value, providerId)
}

function toggleEnabled(row: Row, enabled: boolean) {
  if (saving.value || deleting.value || !canEdit(row.owner)) return
  void persist(row.owner, listOf(row.owner).map((p) => (p.id === row.item.id ? { ...p, enabled } : p)))
}

// Moves `fromId` to the place `toId` holds in my whole order (dropped on a card below = after it, above = before it).
function reorder(fromId: string, toId: string) {
  if (!canReorder() || fromId === toId) return
  const ids = orderedRows.value.map((row) => row.item.id)
  const from = ids.indexOf(fromId)
  const to = ids.indexOf(toId)
  if (from < 0 || to < 0) return
  ids.splice(from, 1)
  ids.splice(to, 0, fromId)
  void persistOrder(ids)
}

async function persistOrder(next: string[]): Promise<void> {
  if (saving.value) return
  const previous = order.value
  order.value = next // shown at once; put back if the save fails
  orderRequest += 1 // an order read still out is older than this save
  saving.value = true
  try {
    const data = await saveMyAiOrder(next)
    if (disposed) return
    order.value = data.order ?? next
    if (data.system_order_changed) {
      // The fallback order moved with it: read the system list again so its own order is current.
      const system = await (isAdmin.value ? fetchSystemAi() : fetchVisibleSystemAi()).catch(() => null)
      if (system && !disposed) systemList.value = system.providers ?? systemList.value
      pushToast(t('gui.ai.owned.fallback_order_changed'), 'info', true)
    }
    emit('changed')
  } catch (err: unknown) {
    if (disposed) return
    order.value = previous
    pushToast(err instanceof Error ? err.message : t('gui.ai.request_failed.order_save'), 'danger')
  } finally {
    saving.value = false
  }
}

// Keyboard alternative to dragging (↑/↓ on the grip): swaps places with the neighbour shown above / below.
function moveProvider(row: Row, delta: number) {
  const shown = rows.value
  const index = shown.findIndex((r) => r.item.id === row.item.id)
  const target = shown[index + delta]
  if (index >= 0 && target) reorder(row.item.id, target.item.id)
}

function onDragStart(row: Row, event: DragEvent) {
  if (!canReorder()) {
    event.preventDefault()
    return
  }
  dragId.value = row.item.id
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', row.item.id)
  }
}

// Any card takes a drop: system and my AI share one order.
function onDragOver(row: Row, event: DragEvent) {
  if (!dragId.value) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  overId.value = row.item.id
}

function onDrop(row: Row) {
  const from = dragId.value
  onDragEnd()
  if (!from || from === row.item.id) return
  reorder(from, row.item.id)
}

function onDragEnd() {
  dragId.value = null
  overId.value = null
}

function clearDialogErrors() {
  dialogSaveError.value = ''
  dialogNameError.value = ''
}

function openAddDialog() {
  if (saving.value || deleting.value || addBlocked.value) return
  clearDialogErrors()
  editingProvider.value = null
  editingOwner.value = ownerChoices.value[0]
  dialogOpen.value = true
}

function openEditDialog(row: Row) {
  if (saving.value || deleting.value || !canEdit(row.owner)) return
  clearDialogErrors()
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
  if (saving.value || !canEdit(row.owner)) return
  deleting.value = row
}

async function onDeleteConfirmed() {
  const target = deleting.value
  if (!target || saving.value || !canEdit(target.owner)) return
  const next = listOf(target.owner).filter((p) => p.id !== target.item.id)
  const nextDefault = target.owner === 'system' && systemDefaultId.value === target.item.id
    ? next[0]?.id ?? null : systemDefaultId.value
  const error = await persist(target.owner, next, nextDefault)
  if (error === null) deleting.value = null
}

async function onSaveProvider(item: ProviderItem, owner: AiOwnerScope) {
  if (!canEdit(owner) || saving.value) return
  clearDialogErrors()
  const current = [...listOf(owner)]
  const idx = current.findIndex((p) => p.id === item.id)
  if (idx >= 0) {
    const merged = { ...current[idx], ...item }
    // The dialog omits target_agent_id for non-remote-CLI; do not let the old value survive the merge.
    if (!('target_agent_id' in item)) delete merged.target_agent_id
    current[idx] = merged
  } else {
    current.push(item)
  }
  const error = await persist(
    owner, current, owner === 'system' ? systemDefaultId.value ?? item.id : systemDefaultId.value, false,
  )
  if (error === null) {
    dialogOpen.value = false
  } else if (error instanceof AiRequestError && error.fieldErrors.some(
    (field) => field.index === (idx >= 0 ? idx : current.length - 1) && field.field === 'name' && field.reason === 'duplicate_name',
  )) {
    dialogNameError.value = t('gui.ai.providers.name_duplicate')
    dialogSaveError.value = error.message
  } else {
    dialogSaveError.value = error instanceof Error ? error.message : t('gui.ai.providers.save_failed')
  }
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

.order-hint {
  margin: 0 0 8px;
  padding: 0 12px;
}

/* While I follow the system default its radio is shown, but dimmer than my own pick. */
/* 📌 pin toggle (v02test.0096): faint while off, full colour with a ring while on. */
.pin {
  width: 22px;
  height: 22px;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid transparent;
  border-radius: 6px;
  background: none;
  font-size: 12px;
  line-height: 1;
  cursor: pointer;
  filter: grayscale(1);
  opacity: 0.35;
}

.pin:hover {
  opacity: 0.7;
}

.pin.on {
  filter: none;
  opacity: 1;
  border-color: rgba(85, 169, 255, 0.55);
  background: rgba(85, 169, 255, 0.12);
}

.pin:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

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

