<template>
  <section class="fgs-panel" data-panel="project-members">
    <div class="fgs-phead">
      <span class="fgs-tico"><SettingsIcon name="users" size="lg" /></span>
      <div class="fgs-ptitle">
        <h2>{{ t('gui.settings.project_nav.members') }}</h2>
        <p>{{ t('gui.settings.page_sub.project_members') }}</p>
      </div>
      <div class="fgs-push">
        <button type="button" class="fgs-btn pri" data-testid="member-add-user" :disabled="!canManage || changing" @click="openAdd('user')">
          <SettingsIcon name="plus" />{{ t('gui.settings.members.add_user') }}
        </button>
        <button type="button" class="fgs-btn pri" data-testid="member-add-team" :disabled="!canManage || changing" @click="openAdd('team')">
          <SettingsIcon name="plus" />{{ t('gui.settings.members.add_team') }}
        </button>
      </div>
    </div>

    <div class="fgs-panel-body">
      <!-- Why the add / change controls are off: the server's own reason, one line. -->
      <p v-if="!canManage && canRead" class="fgs-hintline" data-testid="members-disabled-reason">{{ disabledReason }}</p>

      <LoadingState v-if="loading && !loaded" :label="t('gui.common.loading')" />
      <ErrorState v-else-if="error" :title="errorTitle" :message="error" data-testid="members-error">
        <template #action>
          <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
        </template>
      </ErrorState>
      <EmptyState v-else-if="!canRead" :title="t('gui.settings.members.no_access')" data-testid="members-no-access" />

      <template v-else>
        <div class="fgs-dtable" style="--cols: minmax(200px, 1.6fr) 90px 180px 110px">
          <div class="fgs-dtr head">
            <div>{{ t('gui.settings.members.col_name') }}</div>
            <div>{{ t('gui.settings.members.col_kind') }}</div>
            <div>{{ t('gui.settings.members.col_role') }}</div>
            <div />
          </div>
          <div v-for="row in rows" :key="row.key" class="fgs-dtr row" :class="{ dim: row.dim }" :data-member-row="row.key">
            <div class="fgs-who">
              <div><b>{{ row.name }}</b><span v-if="row.sub">{{ row.sub }}</span></div>
            </div>
            <div class="fgs-muted">{{ row.kind === 'team' ? t('gui.settings.members.kind_team') : t('gui.settings.members.kind_user') }}</div>
            <div>
              <select
                class="fgs-input"
                :value="row.role"
                :disabled="!canManage || changing || row.dim"
                :aria-label="t('gui.settings.members.col_role')"
                :data-testid="`role-${row.key}`"
                @change="changeRole(row, ($event.target as HTMLSelectElement).value)"
              >
                <option v-for="role in PROJECT_ROLE_IDS" :key="role" :value="role">{{ roleName(role) }}</option>
              </select>
            </div>
            <div>
              <button type="button" class="fgs-btn sm" :data-testid="`remove-${row.key}`" :disabled="!canManage || changing" @click="removeRow(row)">
                {{ t('gui.settings.members.remove') }}
              </button>
            </div>
          </div>
          <div v-if="!rows.length" class="fgs-empty" data-testid="members-empty">{{ t('gui.settings.members.empty') }}</div>
        </div>
        <p class="fgs-hintline">{{ t('gui.settings.members.final_hint') }}</p>
      </template>
    </div>

    <!-- + 사람 / + 팀 -->
    <ModalShell :open="!!adding" size="sm" :aria-label="addTitle" @close="adding = null">
      <template #header><h2 class="fgs-modal-title">{{ addTitle }}</h2></template>
      <div v-if="adding" class="fgs-formfield">
        <label for="member-pick">{{ adding.kind === 'team' ? t('gui.settings.members.kind_team') : t('gui.settings.members.kind_user') }}</label>
        <select id="member-pick" v-model="adding.id" class="fgs-input" data-testid="member-pick">
          <option value="" disabled>{{ t('gui.settings.members.pick') }}</option>
          <option v-for="option in addOptions" :key="option.id" :value="option.id">{{ option.label }}</option>
        </select>
        <label for="member-role">{{ t('gui.settings.members.col_role') }}</label>
        <select id="member-role" v-model="adding.role" class="fgs-input" data-testid="member-role">
          <option v-for="role in PROJECT_ROLE_IDS" :key="role" :value="role">{{ roleName(role) }}</option>
        </select>
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="adding = null">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="changing || !adding?.id" data-testid="member-add-confirm" @click="submitAdd">
            {{ t('gui.settings.members.add') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <ChangeImpactDialog :state="dialog" :busy="changing" :user-name="userName" :project-name="() => project.project_name" @confirm="confirm" @cancel="cancel" />
    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </section>
</template>

<script setup lang="ts">
// 설정 › 프로젝트 › 멤버 (v02test.0090, design 0004-D §6.1 · Q2-A): who holds which role on this
// project — people (directly) and teams. A project administrator opens it and manages it without
// being a system administrator.
//
// - Reading follows project.member.read, adding / changing / removing project.member.manage — both
//   read from the project's capabilities the server sent (../capabilities). A person who may read
//   but not manage sees the list with the controls off and the server's reason on one line.
// - Every change is previewed first ("이두리: 리뷰어 → 개발자 (승인 권한 없어짐)"), applied with the
//   revision of that preview, and a 409 (someone changed it first / the last project administrator)
//   is shown with its reason (./components/useChangeFlow, ChangeImpactDialog).
// - The list shows what the project grants directly (people) and to teams. A person's final role
//   also counts the teams they are in and the workspace default; the server reports that in the
//   preview's "after" column, not in this list.
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import ModalShell from '@/components/ui/ModalShell.vue'
import ToastHost from '@/components/ui/ToastHost.vue'
import SettingsIcon from '../SettingsIcon.vue'
import ChangeImpactDialog from '../components/ChangeImpactDialog.vue'
import { useChangeFlow, type ChangeSpec } from '../components/useChangeFlow'
import { useSettingsToasts } from '../components/useSettingsToasts'
import {
  assignProjectTeamRole,
  assignProjectUserRole,
  getProjectMemberCandidates,
  listProjectMembers,
  listProjectTeamRoles,
  revokeProjectTeamRole,
  revokeProjectUserRole,
} from '../api'
import { allows, refusalReason, useAccessLists } from '../capabilities'
import { PROJECT_ROLE_IDS, type ProjectDirectMember, type ProjectMemberCandidates, type ProjectRoleId, type ProjectRow, type ProjectTeamRole } from '../types'

const props = defineProps<{ project: ProjectRow }>()

const { t, te } = useI18n()
const lists = useAccessLists()
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const { dialog, busy: changing, run, confirm, cancel } = useChangeFlow((message) => pushToast(message, 'danger'))

const caps = computed(() => lists.projects.value.find((p) => p.project_id === props.project.project_id)?.capabilities)
const canRead = computed(() => allows(caps.value, 'project.member.read'))
const canManage = computed(() => allows(caps.value, 'project.member.manage'))
const disabledReason = computed(() => {
  const reason = refusalReason(caps.value, 'project.member.manage') ?? 'forbidden'
  return te(`gui.settings.refusal.${reason}`) ? t(`gui.settings.refusal.${reason}`) : t('gui.settings.refusal.forbidden')
})

const users = ref<ProjectDirectMember[]>([])
const teams = ref<ProjectTeamRole[]>([])
const candidates = ref<ProjectMemberCandidates>({ users: [], teams: [] })
const loading = ref(false)
const loaded = ref(false)
const error = ref<string | null>(null)
const errorTitle = computed(() => t('gui.settings.errors.load_failed'))
let seq = 0

/** The project's direct and team roles; the candidates only for someone who may manage. */
async function load() {
  if (!canRead.value) return
  const mine = ++seq
  loading.value = true
  error.value = null
  try {
    const [direct, byTeam, pickable] = await Promise.all([
      listProjectMembers(props.project.project_id),
      listProjectTeamRoles(props.project.project_id),
      canManage.value ? getProjectMemberCandidates(props.project.project_id) : Promise.resolve<ProjectMemberCandidates>({ users: [], teams: [] }),
    ])
    if (mine !== seq) return
    users.value = direct
    teams.value = byTeam
    candidates.value = pickable
    loaded.value = true
  } catch (err) {
    if (mine === seq) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (mine === seq) loading.value = false
  }
}

onMounted(load)
watch(() => props.project.project_id, () => {
  loaded.value = false
  void load()
})

// ── rows ──
interface Row {
  key: string
  kind: 'user' | 'team'
  id: string
  name: string
  sub: string
  role: string
  dim: boolean
}

const rows = computed<Row[]>(() => [
  ...teams.value.map((team) => ({
    key: `team:${team.team_id}`,
    kind: 'team' as const,
    id: team.team_id,
    name: team.name,
    sub: team.is_active === true || team.is_active === 1 ? '' : t('gui.settings.teams.disabled'),
    role: team.role_id,
    dim: !(team.is_active === true || team.is_active === 1),
  })),
  ...users.value.map((member) => ({
    key: `user:${member.user_id}`,
    kind: 'user' as const,
    id: member.user_id,
    name: member.username,
    sub: member.email ?? '',
    role: member.role_id,
    dim: false,
  })),
])

const roleName = (role: string) => (te(`gui.settings.project_roles.${role}`) ? t(`gui.settings.project_roles.${role}`) : role)
const userName = (userId: string) =>
  users.value.find((m) => m.user_id === userId)?.username ?? candidates.value.users.find((u) => u.user_id === userId)?.username ?? userId

// ── changes ──
async function change<T>(spec: ChangeSpec<T>, done: () => void) {
  const applied = await run(spec)
  if (!applied) return
  await load()
  done()
}

function changeRole(row: Row, roleId: string) {
  if (roleId === row.role) return
  const title = t('gui.settings.members.change_title', { name: row.name, from: roleName(row.role), to: roleName(roleId) })
  const request =
    row.kind === 'team'
      ? assignProjectTeamRole(props.project.project_id, row.id, roleId)
      : assignProjectUserRole(props.project.project_id, row.id, roleId)
  void change({ title, request }, () => pushToast(t('gui.settings.members.changed', { name: row.name })))
}

function removeRow(row: Row) {
  const request = row.kind === 'team' ? revokeProjectTeamRole(props.project.project_id, row.id) : revokeProjectUserRole(props.project.project_id, row.id)
  void change({ title: t('gui.settings.members.remove_title', { name: row.name }), request, confirmEmpty: true, label: t('gui.settings.members.remove') }, () =>
    pushToast(t('gui.settings.members.removed', { name: row.name })),
  )
}

const adding = ref<{ kind: 'user' | 'team'; id: string; role: ProjectRoleId } | null>(null)
const addTitle = computed(() => (adding.value?.kind === 'team' ? t('gui.settings.members.add_team') : t('gui.settings.members.add_user')))

const addOptions = computed(() => {
  if (adding.value?.kind === 'team') {
    const held = new Set(teams.value.map((team) => team.team_id))
    return candidates.value.teams.filter((team) => !held.has(team.team_id)).map((team) => ({ id: team.team_id, label: team.name }))
  }
  const held = new Set(users.value.map((member) => member.user_id))
  return candidates.value.users.filter((user) => !held.has(user.user_id)).map((user) => ({ id: user.user_id, label: user.username }))
})

function openAdd(kind: 'user' | 'team') {
  adding.value = { kind, id: '', role: 'role_developer' }
}

async function submitAdd() {
  const form = adding.value
  if (!form || !form.id || changing.value) return
  adding.value = null
  const name = (form.kind === 'team' ? candidates.value.teams.find((team) => team.team_id === form.id)?.name : candidates.value.users.find((user) => user.user_id === form.id)?.username) ?? form.id
  const request =
    form.kind === 'team'
      ? assignProjectTeamRole(props.project.project_id, form.id, form.role)
      : assignProjectUserRole(props.project.project_id, form.id, form.role)
  await change({ title: t('gui.settings.members.add_title', { name, role: roleName(form.role) }), request }, () =>
    pushToast(t('gui.settings.members.added', { name })),
  )
}
</script>
