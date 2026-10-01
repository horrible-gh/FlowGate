<template>
  <div class="ai-providers-panel">
    <div class="detail-head">
      <div>
        <div class="page-title">{{ t('gui.ai.policy.title') }}</div>
        <div class="page-sub">{{ t('gui.ai.policy.subtitle') }}</div>
      </div>
      <div class="detail-actions">
        <Button v-if="isAdmin" variant="secondary" @click="openSettings('system-ai')">
          {{ t('gui.ai.policy.manage_system') }}
        </Button>
        <Button variant="secondary" @click="openSettings('my-ai')">
          {{ t('gui.ai.policy.manage_mine') }}
        </Button>
      </div>
    </div>

    <!-- AI on/off, which system AI is allowed, and whether members may use their own AI. -->
    <div class="source-bar">
      <label class="source-switch">
        <Toggle
          :model-value="policy.ai_enabled"
          :disabled="saving"
          :aria-label="t('gui.ai.providers.ai_enabled')"
          @update:model-value="(val) => persist({ ai_enabled: val })"
        />
        <b>{{ t('gui.ai.providers.ai_enabled') }}</b>
      </label>
      <template v-if="policy.ai_enabled">
        <div class="segmented" role="radiogroup" :aria-label="t('gui.ai.policy.scope_label')">
          <button
            v-for="option in scopeOptions"
            :key="option.value"
            type="button"
            role="radio"
            :aria-checked="policy.system_ai_scope === option.value"
            :class="{ on: policy.system_ai_scope === option.value }"
            :disabled="saving"
            @click="setScope(option.value)"
          >
            {{ option.label }}
          </button>
        </div>
        <label class="source-switch">
          <Toggle
            :model-value="policy.allow_user_ai"
            :disabled="saving"
            :aria-label="t('gui.ai.policy.allow_user_ai')"
            @update:model-value="(val) => persist({ allow_user_ai: val })"
          />
          <b>{{ t('gui.ai.policy.allow_user_ai') }}</b>
        </label>
      </template>
      <span v-else class="source-off">{{ t('gui.ai.providers.ai_off_caption') }}</span>
      <Tooltip :text="t('gui.ai.policy.help')" placement="bottom">
        <IconButton :label="t('gui.ai.policy.help_label')" size="sm">
          <AiIcon name="help" />
        </IconButton>
      </Tooltip>
    </div>

    <div v-if="error" class="panel-error-alert" role="alert">{{ error }}</div>

    <section class="detail-panel" :class="{ 'is-project-off': !policy.ai_enabled }">
      <div class="detail-toolbar">
        <label class="detail-search">
          ⌕ <input v-model="searchQuery" :placeholder="t('gui.ai.providers.search_placeholder')" />
        </label>
        <span class="detail-hint">{{ listHint }}</span>
        <span class="repeat-chip" :title="t('gui.ai.providers.repeat_max_hint')">
          {{ t('gui.ai.policy.repeat_value', { count: policy.repeat_count_max }) }}
        </span>
      </div>

      <div class="provider-cards">
        <div v-if="filteredProviders.length === 0" class="empty-row">
          {{ t('gui.ai.policy.empty_system') }}
        </div>

        <div
          v-for="item in filteredProviders"
          :key="item.id"
          class="provider-card"
          :class="{
            'is-dragging': dragId === item.id,
            'is-drag-over': overId === item.id && dragId !== item.id,
            'is-off': !item.allowed || !item.enabled,
          }"
          :draggable="canReorder(item)"
          @dragstart="onDragStart(item, $event)"
          @dragover="onDragOver(item.id, $event)"
          @drop.prevent="onDrop(item.id)"
          @dragend="onDragEnd"
        >
          <button
            v-if="canReorder(item)"
            type="button"
            class="grip"
            :aria-label="t('gui.ai.providers.reorder_aria', { name: item.name })"
            @keydown.up.prevent="moveListed(item.id, -1)"
            @keydown.down.prevent="moveListed(item.id, 1)"
          >
            <AiIcon name="grip" />
          </button>
          <span v-else class="grip grip--static"><AiIcon name="grip" /></span>
          <span class="rank">{{ rankOf(item.id) }}</span>

          <!-- Default AI of this project -->
          <button
            type="button"
            class="radio"
            :class="{ on: currentDefaultId === item.id }"
            :disabled="saving || !isUsable(item.id)"
            :title="t('gui.ai.providers.set_default')"
            :aria-label="t('gui.ai.providers.set_default')"
            @click="setDefault(item.id)"
          />

          <span class="avatar-round" :class="getAvatarClass(item.name, item.kind)">
            {{ getInitials(item.name) }}
          </span>
          <div class="name-box">
            <div class="name-title-row">
              <b>{{ item.name }}</b>
              <span class="badge">{{ item.exec_type.toUpperCase() }}</span>
              <span class="role" :class="getKindToneClass(item.kind)">{{ item.kind }}</span>
              <Badge v-if="item.project_access === 'listed'" tone="warning" size="sm">
                {{ t('gui.ai.owned.access_listed') }}
              </Badge>
              <Badge v-if="!item.enabled" tone="neutral" size="sm">
                {{ t('gui.ai.policy.system_off') }}
              </Badge>
              <Badge v-if="testBadge(item.id)" :tone="testBadge(item.id)!.tone" size="sm">
                {{ testBadge(item.id)!.label }}
              </Badge>
            </div>
            <small>{{ item.id }}</small>
          </div>

          <!-- Allowed in this project. Shared system AI is always allowed under "all". -->
          <div class="status-cell">
            <Toggle
              :model-value="item.allowed"
              :disabled="saving || !policy.ai_enabled || isLockedOn(item)"
              :aria-label="t('gui.ai.policy.allow_aria', { name: item.name })"
              @update:model-value="(val) => setAllowed(item.id, val)"
            />
          </div>

          <div class="row-actions">
            <IconButton
              v-if="isUsable(item.id)"
              :label="t('gui.ai.providers.test_connection')"
              :title="t('gui.ai.providers.test_connection')"
              size="sm"
              :disabled="testing[item.id]"
              @click="runConnectionTest(item)"
            >
              <AiIcon name="pulse" />
            </IconButton>
            <IconButton
              :label="item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
              :title="item.exec_type === 'cli' ? t('gui.ai.providers.view_command') : t('gui.ai.providers.view_connection')"
              size="sm"
              @click="infoProvider = item"
            >
              <AiIcon :name="item.exec_type === 'cli' ? 'terminal' : 'link'" />
            </IconButton>
          </div>
        </div>
      </div>
    </section>

    <AiCommandInfoModal :provider="infoProvider" @close="infoProvider = null" @toast="pushToast" />
    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" @dismiss="dismissToast" />
  </div>
</template>

<script setup lang="ts">
// Project AI usage policy (flowgate.v02.0017 TR0003 rev7): the project owns no AI. It turns AI
// on/off, allows system AI (all shared ones, or only the ones it picks, in its order), allows
// members' own AI, and picks its default. Every change is saved immediately.
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Badge from '@/components/ui/Badge.vue'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import Toggle from '@/components/ui/Toggle.vue'
import Tooltip from '@/components/ui/Tooltip.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { fetchCurrentConnectionTest, saveProjectAiPolicy, startConnectionTest } from '../api'
import type { AiConnectionTest, PolicyProviderItem, ProjectAiPolicy, ProjectAiPolicyPatch, SystemAiScope } from '../types'
import AiIcon from '../AiIcon.vue'
import AiCommandInfoModal from '../providers/AiCommandInfoModal.vue'
import { getAvatarClass, getInitials, getKindToneClass } from '../providers/providerDisplay'

const props = defineProps<{
  projectId: string
  initialPolicy: ProjectAiPolicy
}>()

const emit = defineEmits<{
  changed: []
}>()

const { t } = useI18n()
const router = useRouter()
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)

const policy = ref<ProjectAiPolicy>({ ...props.initialPolicy })
const saving = ref(false)
const error = ref('')
const searchQuery = ref('')
const infoProvider = ref<PolicyProviderItem | null>(null)
const toasts = ref<ToastHostItem[]>([])
const testing = ref<Record<string, boolean>>({})
const testResults = ref<Record<string, AiConnectionTest | null>>({})
const dragId = ref<string | null>(null)
const overId = ref<string | null>(null)
let disposed = false

watch(() => props.initialPolicy, (val) => (policy.value = { ...val }), { deep: true })

onBeforeUnmount(() => {
  disposed = true
})

const scopeOptions = computed<{ value: SystemAiScope; label: string }[]>(() => [
  { value: 'all', label: t('gui.ai.policy.scope_all') },
  { value: 'selected', label: t('gui.ai.policy.scope_selected') },
])

const listHint = computed(() => {
  if (!policy.value.ai_enabled) return t('gui.ai.providers.disabled_hint')
  return policy.value.system_ai_scope === 'selected' ? t('gui.ai.policy.hint_selected') : t('gui.ai.policy.hint_all')
})

const currentDefaultId = computed(() => policy.value.effective?.default_provider_id ?? null)

// Allowed AI in chain order: the listed order under "selected", the system order under "all".
const allowedIds = computed(() => policy.value.system_providers.filter((p) => p.allowed).map((p) => p.id))

const filteredProviders = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const list = policy.value.system_providers
  if (!query) return list
  return list.filter(
    (p) => p.name.toLowerCase().includes(query) || p.kind.toLowerCase().includes(query) || p.id.toLowerCase().includes(query)
  )
})

function rankOf(providerId: string): string {
  const index = allowedIds.value.indexOf(providerId)
  return index >= 0 ? String(index + 1) : '—'
}

function isUsable(providerId: string): boolean {
  return (policy.value.effective?.providers ?? []).some((p) => p.id === providerId)
}

// Under "all", a shared system AI is allowed by the scope itself, not by a per-AI switch.
function isLockedOn(item: PolicyProviderItem): boolean {
  return policy.value.system_ai_scope === 'all' && item.project_access !== 'listed'
}

function pushToast(message: string, tone: ToastHostItem['tone']): void {
  toasts.value = [...toasts.value, { id: `ai-policy-${Date.now()}-${Math.random()}`, message, tone, duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

async function persist(patch: ProjectAiPolicyPatch): Promise<void> {
  if (saving.value) return
  saving.value = true
  error.value = ''
  try {
    policy.value = await saveProjectAiPolicy(props.projectId, patch)
    emit('changed')
  } catch (err: unknown) {
    error.value = err instanceof Error ? err.message : t('gui.ai.providers.save_failed')
  } finally {
    saving.value = false
  }
}

function setScope(scope: SystemAiScope) {
  if (scope !== policy.value.system_ai_scope) void persist({ system_ai_scope: scope })
}

function setDefault(providerId: string) {
  if (providerId !== currentDefaultId.value) void persist({ default_provider_id: providerId })
}

function setAllowed(providerId: string, allowed: boolean) {
  const listed = policy.value.listed_provider_ids.filter((id) => id !== providerId)
  if (allowed) listed.push(providerId)
  void persist({ listed_provider_ids: listed })
}

// Order only matters (and can only be changed) for the AI a "selected" policy lists.
function canReorder(item: PolicyProviderItem): boolean {
  return policy.value.system_ai_scope === 'selected' && item.allowed && !searchQuery.value.trim() && !saving.value
}

function reorderListed(fromId: string, toIndex: number) {
  const list = [...policy.value.listed_provider_ids]
  const index = list.indexOf(fromId)
  if (index < 0 || toIndex < 0 || toIndex >= list.length || toIndex === index) return
  const [moved] = list.splice(index, 1)
  list.splice(toIndex, 0, moved)
  void persist({ listed_provider_ids: list })
}

function moveListed(providerId: string, delta: number) {
  const index = policy.value.listed_provider_ids.indexOf(providerId)
  if (index >= 0) reorderListed(providerId, index + delta)
}

function onDragStart(item: PolicyProviderItem, event: DragEvent) {
  if (!canReorder(item)) {
    event.preventDefault()
    return
  }
  dragId.value = item.id
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move'
    event.dataTransfer.setData('text/plain', item.id)
  }
}

function onDragOver(providerId: string, event: DragEvent) {
  if (!dragId.value || !policy.value.listed_provider_ids.includes(providerId)) return
  event.preventDefault()
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move'
  overId.value = providerId
}

function onDrop(providerId: string) {
  const from = dragId.value
  onDragEnd()
  if (!from || from === providerId) return
  reorderListed(from, policy.value.listed_provider_ids.indexOf(providerId))
}

function onDragEnd() {
  dragId.value = null
  overId.value = null
}

function openSettings(tab: 'system-ai' | 'my-ai') {
  void router.push({ path: '/settings', query: { tab } })
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

async function runConnectionTest(provider: PolicyProviderItem) {
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
</script>

<style scoped src="../providers/aiCards.css"></style>

<style scoped>
.repeat-chip {
  margin-left: auto;
  height: 24px;
  padding: 0 9px;
  display: inline-flex;
  align-items: center;
  border-radius: 6px;
  border: 1px solid var(--fg2-line);
  background: #0c1b2c;
  color: #9fb3c8;
  font-size: 11.5px;
  white-space: nowrap;
}
</style>
