<template>
  <SettingsShell :crumbs="crumbs" scope="workspace">
    <template #nav>
      <SettingsSubnav
        :title="t('gui.settings.scope.workspace')"
        head-icon="grid"
        head-tone="teal"
        :items="[{ key: 'list', label: t('gui.settings.workspaces.list'), icon: 'grid', to: '/settings/workspaces' }]"
        active="list"
      />
    </template>

    <section class="fgs-panel workspace-list">
      <div class="fgs-phead">
        <span class="fgs-tico teal"><SettingsIcon name="grid" size="lg" /></span>
        <h2>{{ t('gui.settings.scope.workspace') }}</h2>
        <div class="fgs-push">
          <button v-if="canCreate" type="button" class="fgs-btn pri" data-testid="ws-create" @click="openCreate">
            <SettingsIcon name="plus" />{{ t('gui.settings.workspaces.create') }}
          </button>
        </div>
      </div>

      <div class="fgs-panel-body">
        <LoadingState v-if="loading && !loaded" :label="t('gui.common.loading')" />
        <ErrorState v-else-if="error" :title="t('gui.settings.errors.load_failed')" :message="error">
          <template #action>
            <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>

        <template v-else>
          <div class="fgs-bar">
            <label class="fgs-search">
              <SettingsIcon name="search" class="fgs-search-icon" />
              <input v-model="query" class="fgs-input" :placeholder="t('gui.settings.search')" data-testid="ws-search" />
            </label>
            <div class="fgs-chips" role="radiogroup">
              <button
                v-for="chip in stateChips"
                :key="chip.value"
                type="button"
                class="fgs-chip"
                :class="{ on: stateFilter === chip.value }"
                :data-filter="chip.value || 'all'"
                @click="stateFilter = chip.value"
              >
                <span v-if="chip.dot" class="fgs-dot" :class="chip.dot" />{{ chip.label }} <span class="n">{{ chip.count }}</span>
              </button>
            </div>
          </div>

          <div class="workspace-grid">
            <article
              v-for="ws in visible"
              :key="ws.workspace_id"
              class="workspace-card"
              :class="{ default: ws.is_default, disabled: ws.status === 'disabled' }"
              :data-workspace="ws.workspace_id"
            >
              <div class="card-top">
                <div class="workspace-mark">{{ initials(ws.name) }}</div>
                <div class="card-title">
                  <h3>{{ ws.name }}</h3>
                  <div class="badges">
                    <span v-if="ws.is_default" class="fgs-pill ok"><SettingsIcon name="star" size="sm" />{{ t('gui.settings.workspaces.default') }}</span>
                    <span class="fgs-st">
                      <span class="fgs-dot" :class="ws.status === 'active' ? 'fgs-c-ok' : 'fgs-c-off'" />
                      {{ t(`gui.settings.workspaces.status.${ws.status}`) }}
                    </span>
                  </div>
                </div>
              </div>
              <div class="stats">
                <div class="stat" :title="t('gui.settings.workspaces.members')"><SettingsIcon name="users" /><strong>{{ ws.member_count }}</strong></div>
                <div class="stat" :title="t('gui.settings.workspaces.projects')"><SettingsIcon name="folder" /><strong>{{ ws.project_count }}</strong></div>
                <div class="stat" :title="t('gui.settings.workspaces.ai_policy')">
                  <SettingsIcon name="bot" /><strong data-testid="ws-ai">{{ aiLabel(ws) }}</strong>
                </div>
              </div>
              <div class="card-foot">
                <div v-if="ws.owner" class="owner" :title="t('gui.settings.roles.owner')">
                  <span class="fgs-av sm">{{ initials(ws.owner.username) }}</span>
                  <b>{{ ws.owner.username }}</b>
                  <SettingsIcon name="crown" size="sm" />
                </div>
                <span class="fgs-spacer" />
                <RouterLink :to="`/settings/workspaces/${encodeURIComponent(ws.workspace_id)}`" class="fgs-btn sm" data-testid="ws-open">
                  {{ t('gui.settings.workspaces.open') }} <SettingsIcon name="chev" size="sm" />
                </RouterLink>
                <button
                  v-if="ws.can_admin"
                  type="button"
                  class="fgs-iconbtn"
                  :title="t('gui.settings.more')"
                  :aria-label="t('gui.settings.more')"
                  data-testid="ws-menu"
                  @click="openMenu(ws)"
                >
                  <SettingsIcon name="more" />
                </button>
              </div>
            </article>

            <button v-if="canCreate" type="button" class="empty-card" @click="openCreate">
              <SettingsIcon name="plus" size="xl" />
              <strong>{{ t('gui.settings.workspaces.create') }}</strong>
            </button>
          </div>
          <p v-if="!visible.length && !canCreate" class="fgs-empty">{{ t('gui.settings.workspaces.empty') }}</p>
        </template>
      </div>
    </section>

    <!-- 새 워크스페이스 -->
    <ModalShell :open="createOpen" size="sm" :aria-label="t('gui.settings.workspaces.create')" @close="createOpen = false">
      <template #header><h2 class="fgs-modal-title">{{ t('gui.settings.workspaces.create') }}</h2></template>
      <div class="fgs-formfield">
        <label for="ws-new-name">{{ t('gui.settings.workspaces.name') }}</label>
        <input id="ws-new-name" v-model="newName" class="fgs-input" :placeholder="t('gui.settings.workspaces.name_placeholder')" data-testid="ws-new-name" />
      </div>
      <div class="fgs-formfield">
        <label for="ws-new-owner">{{ t('gui.settings.roles.owner') }}</label>
        <select id="ws-new-owner" v-model="newOwner" class="fgs-input" data-testid="ws-new-owner">
          <option v-for="option in ownerOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
      <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="createOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="busy || !newName.trim()" data-testid="ws-create-confirm" @click="submitCreate">
            {{ t('gui.settings.workspaces.create_confirm') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <!-- 더보기: 이름 변경 / 비활성화 / 삭제 -->
    <ModalShell :open="!!menuFor" size="md" :aria-label="menuFor?.name" @close="closeMenu">
      <template #header><h2 class="fgs-modal-title">{{ menuFor?.name }}</h2></template>
      <div v-if="menuFor" class="fgs-actlist">
        <div class="fgs-actrow">
          <span class="fgs-tico sm"><SettingsIcon name="edit" /></span>
          <b>{{ t('gui.settings.workspaces.rename') }}</b>
          <span class="fgs-spacer" />
          <template v-if="renaming">
            <input v-model="renameValue" class="fgs-input rename-input" data-testid="ws-rename-input" @keydown.enter="submitRename" />
            <button type="button" class="fgs-btn sm pri" :disabled="busy || !renameValue.trim()" data-testid="ws-rename-save" @click="submitRename">
              {{ t('gui.common.save') }}
            </button>
          </template>
          <button v-else type="button" class="fgs-btn sm" data-testid="ws-rename" @click="startRename">{{ t('gui.settings.workspaces.change') }}</button>
        </div>
        <div class="fgs-actrow">
          <span class="fgs-tico sm warn"><SettingsIcon name="pause" /></span>
          <b>{{ menuFor.status === 'disabled' ? t('gui.settings.workspaces.enable_again') : t('gui.settings.workspaces.disable') }}</b>
          <span class="fgs-spacer" />
          <span v-if="menuFor.is_default" class="fgs-lockmark" :title="t('gui.settings.workspaces.default_locked')"><SettingsIcon name="lock" size="sm" /></span>
          <button type="button" class="fgs-btn sm" :disabled="busy || menuFor.is_default" data-testid="ws-toggle" @click="toggleStatus">
            {{ menuFor.status === 'disabled' ? t('gui.settings.workspaces.enable') : t('gui.settings.workspaces.disable') }}
          </button>
        </div>
        <div class="fgs-actrow">
          <span class="fgs-tico sm danger"><SettingsIcon name="trash" /></span>
          <b>{{ t('gui.common.delete') }}</b>
          <span class="fgs-spacer" />
          <span v-if="!deletable(menuFor)" class="fgs-lockmark" :title="deleteLockReason(menuFor)"><SettingsIcon name="lock" size="sm" /></span>
          <button type="button" class="fgs-btn sm danger" :disabled="busy || !deletable(menuFor)" data-testid="ws-delete" @click="confirmDelete = true">
            {{ t('gui.common.delete') }}
          </button>
        </div>
        <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="closeMenu">{{ t('gui.common.close') }}</button>
        </div>
      </template>
    </ModalShell>

    <ConfirmDialog
      :open="confirmDelete"
      danger
      :title="t('gui.settings.workspaces.delete_title', { name: menuFor?.name ?? '' })"
      :message="t('gui.settings.workspaces.delete_message')"
      :confirm-label="t('gui.common.delete')"
      :cancel-label="t('gui.common.cancel')"
      @confirm="submitDelete"
      @cancel="confirmDelete = false"
    />

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// 시안 11 워크스페이스 목록: cards with state, member/project counts, AI policy and owner; create
// (system administrator), rename / disable / delete (owner) from the "더보기" menu. Deleting is
// only possible once no project and no member besides the owner is left.
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { createWorkspace, deleteWorkspace, listSystemUsers, listWorkspaces, updateWorkspace } from '../api'
import { initials } from '../format'
import type { SystemUser, WorkspaceSummary } from '../types'

const { t } = useI18n()
const router = useRouter()
const { user } = useCurrentUser()
const { toasts, pushToast, dismissToast } = useSettingsToasts()

const crumbs = computed(() => [
  { label: t('gui.settings.title'), to: '/settings' },
  { label: t('gui.settings.scope.workspace') },
])

const workspaces = ref<WorkspaceSummary[]>([])
const canCreate = ref(false)
const loading = ref(false)
const loaded = ref(false)
const error = ref<string | null>(null)
const query = ref('')
const stateFilter = ref<'' | 'active' | 'disabled'>('')

async function load() {
  loading.value = true
  error.value = null
  try {
    const result = await listWorkspaces()
    workspaces.value = result.workspaces
    canCreate.value = result.can_create
    loaded.value = true
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

const stateChips = computed(() => [
  { value: '' as const, label: t('gui.settings.filter_all'), count: workspaces.value.length, dot: '' },
  { value: 'active' as const, label: t('gui.settings.workspaces.status.active'), count: workspaces.value.filter((w) => w.status === 'active').length, dot: 'fgs-c-ok' },
  { value: 'disabled' as const, label: t('gui.settings.workspaces.status.disabled'), count: workspaces.value.filter((w) => w.status === 'disabled').length, dot: 'fgs-c-off' },
])

const visible = computed(() => {
  const q = query.value.trim().toLowerCase()
  return workspaces.value.filter((w) => (!q || w.name.toLowerCase().includes(q)) && (!stateFilter.value || w.status === stateFilter.value))
})

function aiLabel(ws: WorkspaceSummary): string {
  if (ws.status === 'disabled') return '—'
  return ws.ai_policy_mode === 'allowlist' ? t('gui.settings.workspaces.ai_on') : t('gui.settings.workspaces.ai_off')
}

// ── create ──
const createOpen = ref(false)
const newName = ref('')
const newOwner = ref('')
const owners = ref<SystemUser[]>([])
const busy = ref(false)
const formError = ref<string | null>(null)

const ownerOptions = computed(() => {
  const me = user.value?.username
  const options = owners.value
    .filter((u) => u.state !== 'disabled')
    .map((u) => ({ value: u.user_id, label: u.username === me ? `${u.username} (${t('gui.settings.me')})` : u.username }))
  return options.length ? options : [{ value: '', label: me ? `${me} (${t('gui.settings.me')})` : t('gui.settings.me') }]
})

async function openCreate() {
  newName.value = ''
  formError.value = null
  createOpen.value = true
  try {
    owners.value = (await listSystemUsers()).users
    const me = owners.value.find((u) => u.username === user.value?.username)
    newOwner.value = me?.user_id ?? owners.value[0]?.user_id ?? ''
  } catch {
    owners.value = []
    newOwner.value = ''
  }
}

async function submitCreate() {
  busy.value = true
  formError.value = null
  try {
    const created = await createWorkspace(newName.value.trim(), newOwner.value || null)
    createOpen.value = false
    pushToast(t('gui.settings.workspaces.created', { name: created.name }))
    await router.push(`/settings/workspaces/${encodeURIComponent(created.workspace_id)}`)
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err)
  } finally {
    busy.value = false
  }
}

// ── menu ──
const menuFor = ref<WorkspaceSummary | null>(null)
const renaming = ref(false)
const renameValue = ref('')
const confirmDelete = ref(false)

function openMenu(ws: WorkspaceSummary) {
  menuFor.value = ws
  renaming.value = false
  formError.value = null
}

function closeMenu() {
  menuFor.value = null
  renaming.value = false
}

function startRename() {
  renameValue.value = menuFor.value?.name ?? ''
  renaming.value = true
}

function deletable(ws: WorkspaceSummary): boolean {
  return !ws.is_default && ws.project_count === 0 && ws.member_count <= 1
}

function deleteLockReason(ws: WorkspaceSummary): string {
  return ws.is_default ? t('gui.settings.workspaces.default_locked') : t('gui.settings.workspaces.delete_locked')
}

async function mutate(task: () => Promise<unknown>, message: string) {
  busy.value = true
  formError.value = null
  try {
    await task()
    pushToast(message)
    await load()
    const fresh = workspaces.value.find((w) => w.workspace_id === menuFor.value?.workspace_id)
    menuFor.value = fresh ?? null
    return true
  } catch (err) {
    formError.value = err instanceof Error ? err.message : String(err)
    return false
  } finally {
    busy.value = false
  }
}

async function submitRename() {
  const ws = menuFor.value
  if (!ws || !renameValue.value.trim()) return
  if (await mutate(() => updateWorkspace(ws.workspace_id, { name: renameValue.value.trim() }), t('gui.settings.saved'))) {
    renaming.value = false
  }
}

async function toggleStatus() {
  const ws = menuFor.value
  if (!ws) return
  const next = ws.status === 'disabled' ? 'active' : 'disabled'
  await mutate(
    () => updateWorkspace(ws.workspace_id, { status: next }),
    t(next === 'active' ? 'gui.settings.workspaces.enabled_done' : 'gui.settings.workspaces.disabled_done', { name: ws.name }),
  )
}

async function submitDelete() {
  confirmDelete.value = false
  const ws = menuFor.value
  if (!ws) return
  if (await mutate(() => deleteWorkspace(ws.workspace_id), t('gui.settings.workspaces.deleted', { name: ws.name }))) {
    menuFor.value = null
  }
}

onMounted(load)
</script>

<style scoped>
.workspace-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
  gap: 14px;
}

.workspace-card {
  border: 1px solid var(--fg2-line);
  border-radius: 14px;
  background: linear-gradient(160deg, rgba(16, 34, 58, 0.9), rgba(10, 24, 41, 0.94));
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
  min-width: 0;
}

.workspace-card.default {
  border-color: rgba(53, 199, 164, 0.45);
}

.workspace-card.disabled {
  opacity: 0.6;
}

.card-top {
  display: flex;
  align-items: center;
  gap: 12px;
}

.workspace-mark {
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  border-radius: 12px;
  display: grid;
  place-items: center;
  font-weight: 800;
  background: #11383e;
  color: #5bdabb;
}

.card-title {
  flex: 1;
  min-width: 0;
}

.card-title h3 {
  margin: 0 0 6px;
  font-size: 16px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.badges {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

.stat {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid var(--fg2-line);
  border-radius: 10px;
  padding: 10px 12px;
  color: #8fb1d3;
  background: #0b1929;
}

.stat strong {
  color: var(--fg2-text);
  font-size: 16px;
}

.card-foot {
  display: flex;
  align-items: center;
  gap: 8px;
}

.owner {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: #c9a8ff;
}

.owner b {
  color: #d3deea;
  font-size: 13px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-card {
  min-height: 210px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 14px;
  background: transparent;
  color: #8fb1d3;
  display: grid;
  place-items: center;
  align-content: center;
  gap: 10px;
  cursor: pointer;
  font: inherit;
}

.empty-card:hover {
  background: #0e2036;
}

.rename-input {
  max-width: 220px;
}
</style>
