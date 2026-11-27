<template>
  <SettingsShell scope="manage">
    <template #nav>
      <!-- The 관리 pages (팀 · 워크스페이스 설정) stay listed here as on the list and teams screens (C-1): with one
           workspace this screen is the "워크스페이스 설정" entry itself, and 팀 must not drop out of the menu. -->
      <SettingsSubnav :items="manageNav" active="workspace" />
      <SettingsSubnav
        :title="detail?.name ?? ''"
        :items="tabItems"
        :active="tab"
        :back="showBack ? { to: '/settings/workspaces', label: t('gui.settings.workspaces.list') } : null"
        @select="selectTab"
      >
        <template #mark><div class="workspace-mark">{{ initials(detail?.name) }}</div></template>
      </SettingsSubnav>
    </template>

    <LoadingState v-if="loading && !detail" :label="t('gui.common.loading')" />
    <ErrorState v-else-if="error && !detail" :title="t('gui.settings.errors.load_failed')" :message="error">
      <template #action>
        <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
      </template>
    </ErrorState>

    <template v-else-if="detail">
      <!-- Why the controls are off: the server's own reason, one line. -->
      <p v-if="!canManage" class="fgs-hintline readonly-reason" data-testid="ws-readonly-reason">{{ readonlyReason }}</p>

      <!-- ── Members ── -->
      <section v-if="tab === 'members'" class="fgs-panel" data-panel="members">
        <div class="fgs-phead">
          <span class="fgs-tico teal"><SettingsIcon name="users" size="lg" /></span>
          <h2>{{ t('gui.settings.detail.members') }}</h2>
          <div class="fgs-push">
            <button v-if="canManage" type="button" class="fgs-btn pri" data-testid="member-add" @click="openAdd">
              <SettingsIcon name="plus" />{{ t('gui.settings.detail.add_member') }}
            </button>
          </div>
        </div>
        <div class="fgs-panel-body">
          <div class="fgs-glance" style="--n:3">
            <div class="fgs-tile">
              <span class="fgs-tico teal"><SettingsIcon name="users" /></span>
              <div class="tbody">
                <div class="big" data-testid="member-total">{{ detail.members.length }}</div>
                <div class="fgs-sbar">
                  <i class="fgs-c-pur" :style="{ width: pct(roleCount.owner) }" />
                  <i class="fgs-c-blue" :style="{ width: pct(roleCount.admin) }" />
                  <i class="fgs-c-member" :style="{ width: pct(roleCount.member) }" />
                </div>
                <div class="fgs-legend">
                  <span><span class="fgs-dot fgs-c-pur" />Owner {{ roleCount.owner }}</span>
                  <span><span class="fgs-dot fgs-c-blue" />Admin {{ roleCount.admin }}</span>
                  <span><span class="fgs-dot fgs-c-member" />Member {{ roleCount.member }}</span>
                </div>
              </div>
            </div>
            <div class="fgs-tile">
              <span class="fgs-tico ok"><SettingsIcon name="check" /></span>
              <div class="tbody">
                <div class="big">{{ stateCount.active }}<small>/ {{ detail.members.length }}</small></div>
                <div class="fgs-sbar">
                  <i class="fgs-c-ok" :style="{ width: pct(stateCount.active) }" />
                  <i class="fgs-c-warn" :style="{ width: pct(stateCount.invited) }" />
                </div>
                <div class="fgs-legend">
                  <span><span class="fgs-dot fgs-c-ok" />{{ t('gui.settings.state.active') }} {{ stateCount.active }}</span>
                  <span><span class="fgs-dot fgs-c-warn" />{{ t('gui.settings.state.invited') }} {{ stateCount.invited }}</span>
                  <span v-if="stateCount.disabled"><span class="fgs-dot fgs-c-off" />{{ t('gui.settings.state.disabled') }} {{ stateCount.disabled }}</span>
                </div>
              </div>
            </div>
            <button type="button" class="fgs-tile" data-testid="to-projects" @click="selectTab('projects')">
              <span class="fgs-tico"><SettingsIcon name="folder" /></span>
              <div class="tbody">
                <div class="big">{{ detail.project_count }}</div>
                <div class="lbl">{{ t('gui.settings.workspaces.projects') }} <SettingsIcon name="chev" size="sm" /></div>
              </div>
            </button>
          </div>

          <div class="fgs-bar">
            <label class="fgs-search">
              <SettingsIcon name="search" class="fgs-search-icon" />
              <input v-model="memberQuery" class="fgs-input" :placeholder="t('gui.settings.search_people')" data-testid="member-search" />
            </label>
            <div class="fgs-chips">
              <button
                v-for="chip in roleChips"
                :key="chip.value"
                type="button"
                class="fgs-chip"
                :class="{ on: roleFilter === chip.value }"
                :data-role-filter="chip.value || 'all'"
                @click="roleFilter = chip.value"
              >
                <SettingsIcon v-if="chip.icon" :name="chip.icon" size="sm" />{{ chip.label }}
              </button>
            </div>
            <span class="fgs-vsep" />
            <button type="button" class="fgs-chip warn" :class="{ on: invitedOnly }" data-testid="invited-filter" @click="invitedOnly = !invitedOnly">
              <span class="fgs-dot fgs-c-warn" />{{ t('gui.settings.state.invited') }}
            </button>
            <span class="fgs-spacer" />
            <button type="button" class="fgs-iconbtn" :title="t('gui.settings.csv')" :aria-label="t('gui.settings.csv')" @click="exportMembers">
              <SettingsIcon name="download" />
            </button>
          </div>

          <div class="fgs-dtable" style="--cols: minmax(230px, 1.6fr) 120px 100px 110px 110px 24px">
            <div class="fgs-dtr head">
              <div>{{ t('gui.settings.detail.col_member') }}</div>
              <div>{{ t('gui.settings.detail.col_role') }}</div>
              <div>{{ t('gui.settings.detail.col_state') }}</div>
              <div>{{ t('gui.settings.workspaces.projects') }}</div>
              <div>{{ t('gui.settings.detail.col_activity') }}</div>
              <div />
            </div>
            <div
              v-for="member in filteredMembers"
              :key="member.user_id"
              class="fgs-dtr row"
              :class="{ static: !canManage, dim: member.state === 'disabled' }"
              :data-member="member.user_id"
              @click="openMember(member)"
            >
              <div class="fgs-who">
                <div class="fgs-av">{{ initials(member.username) }}</div>
                <div><b>{{ member.username }}</b><span>{{ member.email }}</span></div>
              </div>
              <div><span class="fgs-pill" :class="rolePillClass(member.role)"><SettingsIcon :name="roleIcon(member.role)" size="sm" />{{ roleLabel(member.role) }}</span></div>
              <div><span class="fgs-st"><span class="fgs-dot" :class="stateDot(member.state)" />{{ t(`gui.settings.state.${member.state}`) }}</span></div>
              <div>
                <span class="pdots" :title="t('gui.settings.detail.projects_of', { n: member.project_count, total: detail.active_project_count })">
                  <template v-if="detail.active_project_count <= 8">
                    <i v-for="n in detail.active_project_count" :key="n" :class="{ on: n <= member.project_count }" />
                  </template>
                  <span v-else class="fgs-muted">{{ member.project_count }} / {{ detail.active_project_count }}</span>
                </span>
              </div>
              <div class="fgs-muted">{{ relativeTime(member.last_login_at, locale) }}</div>
              <div class="fgs-chev"><SettingsIcon v-if="canManage" name="chev" /></div>
            </div>
            <div v-if="!filteredMembers.length" class="fgs-empty">{{ t('gui.settings.no_match') }}</div>
          </div>
          <div class="fgs-tablefoot" data-testid="member-foot">{{ filteredMembers.length }} / {{ detail.members.length }}</div>
        </div>
      </section>

      <!-- ── Project access ── -->
      <section v-else-if="tab === 'projects'" class="fgs-panel" data-panel="projects">
        <div class="fgs-phead">
          <span class="fgs-tico"><SettingsIcon name="folder" size="lg" /></span>
          <h2>{{ t('gui.settings.detail.project_access') }}</h2>
        </div>
        <div class="fgs-panel-body">
          <div class="fgs-card">
            <div class="fgs-card-h"><span class="fgs-tico sm"><SettingsIcon name="gear" /></span><b>{{ t('gui.settings.detail.default_access') }}</b></div>
            <div v-for="role in ROLES" :key="role" class="fgs-line" :data-default-role="role">
              <div class="k"><span class="fgs-pill" :class="rolePillClass(role)"><SettingsIcon :name="roleIcon(role)" size="sm" />{{ roleLabel(role) }}</span></div>
              <div class="v end">
                <div class="fgs-segment" role="radiogroup">
                  <button
                    v-for="level in ACCESS"
                    :key="level"
                    type="button"
                    :class="{ on: detail.access.defaults[role] === level }"
                    :title="t(`gui.settings.access_title.${level}`)"
                    :disabled="!canManage || busy"
                    :data-access="level"
                    @click="saveDefault(role, level)"
                  >
                    <SettingsIcon :name="accessIcon(level)" size="sm" />{{ t(`gui.settings.access.${level}`) }}
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div class="fgs-card">
            <div class="fgs-card-h">
              <span class="fgs-tico sm warn"><SettingsIcon name="user" /></span>
              <b>{{ t('gui.settings.detail.exceptions') }}</b>
              <span class="fgs-pill warn" data-testid="exception-count">{{ exceptionCount }}</span>
              <div class="fgs-push">
                <div class="fgs-chips">
                  <button
                    v-for="project in detail.projects"
                    :key="project.project_id"
                    type="button"
                    class="fgs-chip"
                    :class="{ on: project.project_id === selectedProject }"
                    :data-project="project.project_id"
                    @click="selectedProject = project.project_id"
                  >
                    <SettingsIcon v-if="project.project_id === selectedProject" name="folder" size="sm" />{{ project.project_name }}
                  </button>
                </div>
              </div>
            </div>
            <p v-if="!detail.projects.length" class="fgs-empty">{{ t('gui.settings.detail.no_projects') }}</p>
            <div v-for="member in (selectedProject ? detail.members : [])" :key="member.user_id" class="fgs-line" :data-exception-member="member.user_id">
              <div class="k"><div class="fgs-who"><div class="fgs-av sm">{{ initials(member.username) }}</div><b>{{ member.username }}</b></div></div>
              <div class="v">
                <span class="fgs-pill" :class="rolePillClass(member.role)"><SettingsIcon :name="roleIcon(member.role)" size="sm" />{{ roleLabel(member.role) }}</span>
                <span class="fgs-spacer" />
                <span class="excmark"><span v-if="exceptionOf(member.user_id)" class="exc" :title="t('gui.settings.detail.exception')" /></span>
                <div class="fgs-segment" role="radiogroup">
                  <button
                    type="button"
                    :class="{ on: !exceptionOf(member.user_id) }"
                    :title="t('gui.settings.detail.role_default')"
                    :disabled="!canManage || busy"
                    data-access="default"
                    @click="saveException(member.user_id, 'default')"
                  >
                    {{ t('gui.settings.detail.default_is', { access: t(`gui.settings.access.${detail.access.defaults[member.role]}`) }) }}
                  </button>
                  <button
                    v-for="level in ACCESS"
                    :key="level"
                    type="button"
                    :class="{ on: exceptionOf(member.user_id) === level }"
                    :title="t(`gui.settings.access_title.${level}`)"
                    :disabled="!canManage || busy"
                    :data-access="level"
                    @click="saveException(member.user_id, level)"
                  >
                    <SettingsIcon :name="accessIcon(level)" size="sm" />{{ t(`gui.settings.access.${level}`) }}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- Add member -->
    <ModalShell :open="addOpen" size="sm" :aria-label="t('gui.settings.detail.add_member')" @close="addOpen = false">
      <template #header><h2 class="fgs-modal-title">{{ t('gui.settings.detail.add_member') }}</h2></template>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.detail.user') }}</span>
        <label class="fgs-search" style="flex: none">
          <SettingsIcon name="search" class="fgs-search-icon" />
          <input v-model="candidateQuery" class="fgs-input" :placeholder="t('gui.settings.search_people')" data-testid="candidate-search" @input="searchCandidates" />
        </label>
        <div class="candidates" role="listbox">
          <button
            v-for="candidate in candidates"
            :key="candidate.user_id"
            type="button"
            class="candidate"
            role="option"
            :class="{ on: candidate.user_id === pickedCandidate }"
            :aria-selected="candidate.user_id === pickedCandidate"
            :data-candidate="candidate.user_id"
            @click="pickedCandidate = candidate.user_id"
          >
            <span class="fgs-av sm">{{ initials(candidate.username) }}</span>
            <span><b>{{ candidate.username }}</b><small>{{ candidate.email }}</small></span>
          </button>
          <p v-if="!candidates.length" class="fgs-muted">{{ t('gui.settings.no_match') }}</p>
        </div>
      </div>
      <div class="fgs-formfield">
        <span class="label">{{ t('gui.settings.detail.col_role') }}</span>
        <div class="fgs-chips">
          <button type="button" class="fgs-chip" :class="{ on: addRole === 'member' }" data-add-role="member" @click="addRole = 'member'">
            <SettingsIcon name="user" size="sm" />Member
          </button>
          <button type="button" class="fgs-chip" :class="{ on: addRole === 'admin' }" data-add-role="admin" @click="addRole = 'admin'">
            <SettingsIcon name="star" size="sm" />Admin
          </button>
        </div>
      </div>
      <RouterLink v-if="canInviteUsers" to="/settings/system/users?create=1" class="fgs-link">
        <SettingsIcon name="plus" size="sm" />{{ t('gui.settings.detail.new_account') }}
      </RouterLink>
      <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="addOpen = false">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="busy || !pickedCandidate" data-testid="member-add-confirm" @click="submitAdd">
            {{ t('gui.settings.detail.add_confirm') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <!-- Member menu -->
    <ModalShell :open="!!memberMenu" size="sm" :aria-label="memberMenu?.username" @close="memberMenu = null">
      <template #header>
        <div class="member-head"><div class="fgs-av">{{ initials(memberMenu?.username) }}</div><h2 class="fgs-modal-title" style="padding: 0">{{ memberMenu?.username }}</h2></div>
      </template>
      <div v-if="memberMenu" class="fgs-actlist">
        <div class="fgs-actrow">
          <span class="fgs-tico sm"><SettingsIcon name="star" /></span>
          <b>{{ t('gui.settings.detail.col_role') }}</b>
          <span class="fgs-spacer" />
          <div class="fgs-chips">
            <button
              v-for="role in (['admin', 'member'] as const)"
              :key="role"
              type="button"
              class="fgs-chip"
              :class="{ on: memberMenu.role === role }"
              :disabled="memberMenu.role === 'owner' || busy"
              :data-member-role="role"
              @click="changeRole(role)"
            >
              <SettingsIcon :name="roleIcon(role)" size="sm" />{{ roleLabel(role) }}
            </button>
          </div>
        </div>
        <div class="fgs-actrow">
          <span class="fgs-tico sm danger"><SettingsIcon name="logout" /></span>
          <b>{{ t('gui.settings.detail.remove') }}</b>
          <span class="fgs-spacer" />
          <button type="button" class="fgs-btn sm danger" :disabled="memberMenu.role === 'owner' || busy" data-testid="member-remove" @click="confirmRemove = true">
            {{ t('gui.settings.detail.remove_short') }}
          </button>
        </div>
        <p v-if="memberMenu.role === 'owner'" class="fgs-muted">{{ t('gui.settings.detail.owner_locked') }}</p>
        <p v-if="formError" class="fgs-error" role="alert">{{ formError }}</p>
      </div>
      <template #footer>
        <div class="fgs-modal-actions"><button type="button" class="fgs-btn" @click="memberMenu = null">{{ t('gui.common.close') }}</button></div>
      </template>
    </ModalShell>

    <ConfirmDialog
      :open="confirmRemove"
      danger
      :title="t('gui.settings.detail.remove_title', { name: memberMenu?.username ?? '' })"
      :message="t('gui.settings.detail.remove_message')"
      :confirm-label="t('gui.settings.detail.remove_short')"
      :cancel-label="t('gui.common.cancel')"
      @confirm="submitRemove"
      @cancel="confirmRemove = false"
    />

    <ChangeImpactDialog :state="dialog" :busy="changing" :user-name="userName" :project-name="projectName" @confirm="confirm" @cancel="cancel" />

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// mockup 12 Workspace details. 멤버: role/state/project counts, add (existing users), change role,
// remove (also leaves every project of the workspace). 프로젝트 접근: role default access and
// per-person exceptions per project, materialised by the server into the project roles. Plain members
// see everything read-only.
//
// v02test.0090: reopened as 설정 › 워크스페이스 (with one workspace, "워크스페이스 설정" with no list in
// between). The five changes — member add / role / remove, default access, per-project exception — go
// through the server's common change path: each is previewed ("누가 무엇을 얻고 잃는지"), applied with
// the revision of that preview, and the answer's `result` is the new detail (./components/useChangeFlow).
// A 409 revision_conflict re-reads the preview; nothing is applied silently. Whether the controls are on
// is the workspace's capability workspace.member.manage. The AI / Agent allow-list tab is not part of
// this screen (requirement 5).
import { computed, onMounted, ref, watch } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { systemCan } from '@shared/permissions/capabilities'
import SettingsIcon from '../SettingsIcon.vue'
import ChangeImpactDialog from '../components/ChangeImpactDialog.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav, { type SubnavItem } from '../components/SettingsSubnav.vue'
import { useManageNav } from '../components/manageNav'
import { useChangeFlow, type ChangeSpec } from '../components/useChangeFlow'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { allows, refusalReason, reportsCapabilities, useAccessLists } from '../capabilities'
import type { SettingsIconName } from '../icons'
import {
  addMember,
  changeMemberRole,
  getWorkspace,
  listMemberCandidates,
  removeMember,
  setDefaultAccess,
  setProjectAccess,
} from '../api'
import { downloadCsv, initials, relativeTime, toCsv } from '../format'
import type {
  MemberCandidate,
  ProjectAccess,
  UserState,
  WorkspaceDetail,
  WorkspaceMember,
  WorkspaceRole,
} from '../types'

type DetailTab = 'members' | 'projects'

const ROLES: WorkspaceRole[] = ['owner', 'admin', 'member']
const ACCESS: ProjectAccess[] = ['admin', 'dev', 'review', 'none']

const props = defineProps<{ workspaceId: string }>()
const { t, te, locale } = useI18n()
const route = useRoute()
const router = useRouter()
const { user } = useCurrentUser()
// The link to invite a person is offered when the server lets this person create users.
const canInviteUsers = computed(() => systemCan('system.user.create'))
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const manageNav = useManageNav()

const detail = ref<WorkspaceDetail | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const formError = ref<string | null>(null)
const { dialog, busy: changing, run, confirm, cancel } = useChangeFlow((message) => {
  formError.value = message
  pushToast(message, 'danger')
})
const busy = computed(() => changing.value)

const tab = computed<DetailTab>(() => (route.query.tab === 'projects' ? 'projects' : 'members'))

const tabItems = computed<SubnavItem[]>(() => [
  { key: 'members', label: t('gui.settings.detail.members'), icon: 'users' },
  { key: 'projects', label: t('gui.settings.detail.project_access'), icon: 'folder' },
])

// Controls follow the capability the server sent for this workspace; an older server's flag stands in.
const canManage = computed(() => {
  const d = detail.value
  if (!d) return false
  return d.capabilities ? allows(d.capabilities, 'workspace.member.manage') : d.can_manage
})
const readonlyReason = computed(() => {
  const reason = (detail.value?.capabilities && refusalReason(detail.value.capabilities, 'workspace.member.manage')) || 'forbidden'
  return te(`gui.settings.refusal.${reason}`) ? t(`gui.settings.refusal.${reason}`) : t('gui.settings.refusal.forbidden')
})
// With one workspace the list is skipped; the way back to it is shown only when there is something to pick or create.
const lists = useAccessLists()
const showBack = computed(
  () => !(reportsCapabilities(user.value) && lists.workspaces.value.length === 1 && !allows(user.value?.capabilities, 'system.workspace.create')),
)

const userName = (userId: string) => detail.value?.members.find((m) => m.user_id === userId)?.username ?? userId
const projectName = (projectId: string) => detail.value?.projects.find((p) => p.project_id === projectId)?.project_name ?? projectId

function selectTab(key: string) {
  void router.replace({ query: { ...route.query, tab: key === 'members' ? undefined : key } })
}

async function load() {
  loading.value = true
  error.value = null
  try {
    detail.value = await getWorkspace(props.workspaceId)
  } catch (err) {
    error.value = err instanceof Error ? err.message : String(err)
  } finally {
    loading.value = false
  }
}

/** Run one access change (preview → impact dialog → apply); the answer's result is the fresh detail. */
async function apply(spec: ChangeSpec<WorkspaceDetail>, message?: string): Promise<boolean> {
  formError.value = null
  const applied = await run(spec)
  if (!applied) return false
  detail.value = applied.result
  if (message) pushToast(message)
  return true
}

// ── labels ──
const roleLabel = (role: WorkspaceRole) => t(`gui.settings.roles.${role}`)
const roleIcon = (role: WorkspaceRole): SettingsIconName => (role === 'owner' ? 'crown' : role === 'admin' ? 'star' : 'user')
const rolePillClass = (role: WorkspaceRole) => (role === 'member' ? '' : role)
const stateDot = (state: UserState) => (state === 'active' ? 'fgs-c-ok' : state === 'invited' ? 'fgs-c-warn' : 'fgs-c-off')
const accessIcon = (level: ProjectAccess): SettingsIconName =>
  level === 'admin' ? 'key' : level === 'dev' ? 'code' : level === 'review' ? 'eye' : 'ban'

// ── members ──
const memberQuery = ref('')
const roleFilter = ref<'' | WorkspaceRole>('')
const invitedOnly = ref(false)

const roleChips = computed(() => [
  { value: '' as const, label: t('gui.settings.filter_all'), icon: null },
  { value: 'owner' as const, label: roleLabel('owner'), icon: 'crown' as SettingsIconName },
  { value: 'admin' as const, label: roleLabel('admin'), icon: 'star' as SettingsIconName },
  { value: 'member' as const, label: roleLabel('member'), icon: 'user' as SettingsIconName },
])

const roleCount = computed(() => {
  const members = detail.value?.members ?? []
  return {
    owner: members.filter((m) => m.role === 'owner').length,
    admin: members.filter((m) => m.role === 'admin').length,
    member: members.filter((m) => m.role === 'member').length,
  }
})

const stateCount = computed(() => {
  const members = detail.value?.members ?? []
  return {
    active: members.filter((m) => m.state === 'active').length,
    invited: members.filter((m) => m.state === 'invited').length,
    disabled: members.filter((m) => m.state === 'disabled').length,
  }
})

function pct(n: number): string {
  const total = detail.value?.members.length ?? 0
  return total ? `${(n / total) * 100}%` : '0%'
}

const filteredMembers = computed(() => {
  const q = memberQuery.value.trim().toLowerCase()
  return (detail.value?.members ?? []).filter((m) =>
    (!q || `${m.username} ${m.email ?? ''}`.toLowerCase().includes(q))
    && (!roleFilter.value || m.role === roleFilter.value)
    && (!invitedOnly.value || m.state === 'invited'),
  )
})

function exportMembers() {
  if (!detail.value) return
  const csv = toCsv(
    ['name', 'email', 'role', 'state', 'projects', 'last_login_at'],
    filteredMembers.value.map((m) => [m.username, m.email, m.role, m.state, m.project_count, m.last_login_at]),
  )
  downloadCsv(`${detail.value.name}-members.csv`, csv)
}

// add
const addOpen = ref(false)
const candidateQuery = ref('')
const candidates = ref<MemberCandidate[]>([])
const pickedCandidate = ref<string | null>(null)
const addRole = ref<'member' | 'admin'>('member')
let searchSeq = 0

async function searchCandidates() {
  const seq = ++searchSeq
  try {
    const found = await listMemberCandidates(props.workspaceId, candidateQuery.value.trim())
    if (seq === searchSeq) candidates.value = found
  } catch (err) {
    if (seq === searchSeq) formError.value = err instanceof Error ? err.message : String(err)
  }
}

function openAdd() {
  candidateQuery.value = ''
  pickedCandidate.value = null
  addRole.value = 'member'
  formError.value = null
  addOpen.value = true
  void searchCandidates()
}

async function submitAdd() {
  const picked = candidates.value.find((c) => c.user_id === pickedCandidate.value)
  if (!picked) return
  if (await apply({ title: t('gui.settings.ws_change.add', { name: picked.username }), request: addMember(props.workspaceId, picked.user_id, addRole.value) }, t('gui.settings.detail.added', { name: picked.username }))) {
    addOpen.value = false
  }
}

// member menu
const memberMenu = ref<WorkspaceMember | null>(null)
const confirmRemove = ref(false)

function openMember(member: WorkspaceMember) {
  if (!canManage.value) return
  formError.value = null
  memberMenu.value = member
}

async function changeRole(role: 'admin' | 'member') {
  const member = memberMenu.value
  if (!member || member.role === role) return
  if (await apply({ title: t('gui.settings.ws_change.role', { name: member.username, role: roleLabel(role) }), request: changeMemberRole(props.workspaceId, member.user_id, role) }, t('gui.settings.detail.role_changed'))) {
    memberMenu.value = detail.value?.members.find((m) => m.user_id === member.user_id) ?? null
  }
}

async function submitRemove() {
  confirmRemove.value = false
  const member = memberMenu.value
  if (!member) return
  if (await apply({ title: t('gui.settings.ws_change.remove', { name: member.username }), request: removeMember(props.workspaceId, member.user_id), label: t('gui.settings.detail.remove_short') }, t('gui.settings.detail.removed', { name: member.username }))) {
    memberMenu.value = null
  }
}

// ── project access ──
const selectedProject = ref<string | null>(null)

watch(detail, (value) => {
  if (!value) return
  if (!selectedProject.value || !value.projects.some((p) => p.project_id === selectedProject.value)) {
    selectedProject.value = value.projects[0]?.project_id ?? null
  }
})

const exceptionCount = computed(() =>
  (detail.value?.access.exceptions ?? []).filter((e) => e.project_id === selectedProject.value).length,
)

function exceptionOf(userId: string): ProjectAccess | null {
  return detail.value?.access.exceptions.find((e) => e.project_id === selectedProject.value && e.user_id === userId)?.access ?? null
}

function saveDefault(role: WorkspaceRole, level: ProjectAccess) {
  if (detail.value?.access.defaults[role] === level) return
  void apply({ title: t('gui.settings.ws_change.default', { role: roleLabel(role), access: t(`gui.settings.access.${level}`) }), request: setDefaultAccess(props.workspaceId, { [role]: level }), confirmEmpty: true })
}

function saveException(userId: string, level: ProjectAccess | 'default') {
  const project = selectedProject.value
  if (!project) return
  const current = exceptionOf(userId)
  if ((level === 'default' && !current) || current === level) return
  void apply({ title: t('gui.settings.ws_change.exception', { name: userName(userId), project: projectName(project) }), request: setProjectAccess(props.workspaceId, project, userId, level), confirmEmpty: true })
}

watch(() => props.workspaceId, () => void load())
onMounted(load)
</script>

<style scoped>
.workspace-mark {
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 9px;
  display: grid;
  place-items: center;
  font-size: 12px;
  font-weight: 800;
  background: #11383e;
  color: #5bdabb;
}

.pdots {
  display: inline-flex;
  gap: 4px;
}

.pdots i {
  width: 9px;
  height: 9px;
  border-radius: 3px;
  background: #22324a;
}

.pdots i.on {
  background: #35c7a4;
}

.excmark {
  width: 10px;
  display: inline-grid;
  place-items: center;
}

.exc {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f5b84b;
  box-shadow: 0 0 8px rgba(245, 184, 75, 0.5);
}

.allowchip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  border: 1px solid #2f76c7;
  border-radius: 999px;
  background: #102b4c;
  color: #d8e8fb;
  padding: 0 6px 0 12px;
  font-size: 12px;
}

.allowchip.unavailable {
  border-style: dashed;
  opacity: 0.7;
}

.allowchip button {
  border: 0;
  background: transparent;
  color: #8fb1d3;
  display: grid;
  place-items: center;
  cursor: pointer;
  padding: 2px;
}

.addchip {
  height: 32px;
  border: 1px dashed var(--fg2-line-strong);
  border-radius: 999px;
  background: transparent;
  color: #8fb1d3;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font: inherit;
  font-size: 12px;
  cursor: pointer;
}

.policy-note {
  margin: 0;
  padding: 14px 18px;
}

.candidates {
  display: grid;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
  margin-top: 6px;
}

.candidate {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid var(--fg2-line);
  border-radius: 9px;
  background: #0b1929;
  color: var(--fg2-text);
  padding: 8px 10px;
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.candidate small {
  display: block;
  color: #7d92a8;
  font-size: 11px;
}

.candidate.on,
.candidate:hover:not(:disabled) {
  border-color: #2f76c7;
  background: #12304f;
}

.member-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 22px 0;
}
</style>
