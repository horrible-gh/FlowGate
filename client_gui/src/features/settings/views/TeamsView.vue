<template>
  <SettingsShell scope="manage">
    <template #nav>
      <SettingsSubnav :items="manageNav" active="teams" />
    </template>

    <section class="fgs-panel" data-panel="teams">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="users" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.settings.teams.title') }}</h2>
          <p>{{ t('gui.settings.page_sub.manage_teams') }}</p>
        </div>
        <div class="fgs-push">
          <button type="button" class="fgs-btn pri" data-testid="team-create" :disabled="!workspaceId || changing" @click="openName('create')">
            <SettingsIcon name="plus" />{{ t('gui.settings.teams.create') }}
          </button>
        </div>
      </div>

      <div class="fgs-panel-body">
        <!-- Q1-B: the workspace picker exists only when the person manages teams in more than one workspace. -->
        <div v-if="manageable.length > 1" class="fgs-formfield" data-testid="team-workspace-pick">
          <label for="team-workspace">{{ t('gui.settings.teams.workspace') }}</label>
          <select id="team-workspace" v-model="workspaceId" class="fgs-input" data-testid="team-workspace">
            <option v-for="w in manageable" :key="w.workspace_id" :value="w.workspace_id">{{ w.name }}</option>
          </select>
        </div>

        <LoadingState v-if="loading && !loaded" :label="t('gui.common.loading')" />
        <ErrorState v-else-if="error" :title="t('gui.settings.errors.load_failed')" :message="error" data-testid="teams-error">
          <template #action>
            <Button variant="secondary" @click="load">{{ t('gui.common.retry') }}</Button>
          </template>
        </ErrorState>
        <EmptyState v-else-if="!workspaceId" :title="t('gui.settings.teams.no_workspace')" data-testid="teams-no-workspace" />

        <template v-else>
          <div class="fgs-dtable" style="--cols: minmax(180px, 1.4fr) 90px minmax(240px, 2.2fr) 24px">
            <div class="fgs-dtr head">
              <div>{{ t('gui.settings.teams.col_name') }}</div>
              <div>{{ t('gui.settings.teams.col_people') }}</div>
              <div>{{ t('gui.settings.teams.col_roles') }}</div>
              <div />
            </div>
            <div
              v-for="row in teams"
              :key="row.team_id"
              class="fgs-dtr row"
              :class="{ sel: selectedId === row.team_id, dim: !isActive(row) }"
              :data-team="row.team_id"
              @click="selectedId = row.team_id"
            >
              <div class="fgs-who">
                <div><b>{{ row.name }}</b><span v-if="!isActive(row)">{{ t('gui.settings.teams.disabled') }}</span></div>
              </div>
              <div data-testid="team-people">{{ details[row.team_id]?.members.length ?? 0 }}</div>
              <div class="fgs-muted" data-testid="team-roles">{{ rolesText(row.team_id) }}</div>
              <div class="fgs-chev"><SettingsIcon name="chev" /></div>
            </div>
            <div v-if="!teams.length" class="fgs-empty" data-testid="teams-empty">{{ t('gui.settings.teams.empty') }}</div>
          </div>
        </template>
      </div>
    </section>

    <!-- The selected team: its people, the roles it holds, rename / turn off -->
    <section v-if="selected" class="fgs-panel team-detail" data-panel="team-detail">
      <div class="fgs-phead">
        <span class="fgs-tico sm"><SettingsIcon name="users" /></span>
        <h2 data-testid="team-detail-name">{{ selected.name }}</h2>
        <div class="fgs-push">
          <button type="button" class="fgs-btn sm" data-testid="team-rename" :disabled="changing || !isActive(selected)" @click="openName('rename')">
            <SettingsIcon name="edit" size="sm" />{{ t('gui.settings.teams.rename') }}
          </button>
          <button type="button" class="fgs-btn sm danger" data-testid="team-disable" :disabled="changing || !isActive(selected)" @click="disableSelected">
            <SettingsIcon name="power" size="sm" />{{ t('gui.settings.teams.disable') }}
          </button>
        </div>
      </div>
      <div class="fgs-panel-body">
        <div class="fgs-sec">
          <div class="fgs-sec-head"><h3>{{ t('gui.settings.teams.members') }}</h3></div>
          <div class="fgs-rows" data-card="team-members">
            <div v-for="id in selectedMembers" :key="id" class="fgs-row" :data-member="id">
              <div class="fgs-row-label"><strong>{{ userName(id) }}</strong></div>
              <div class="fgs-row-ctrl">
                <button type="button" class="fgs-btn sm" :data-testid="`team-member-remove-${id}`" :disabled="changing || !isActive(selected)" @click="removeMemberOf(id)">
                  {{ t('gui.settings.teams.remove_member') }}
                </button>
              </div>
            </div>
            <div v-if="!selectedMembers.length" class="fgs-empty" data-testid="team-members-empty">{{ t('gui.settings.teams.members_empty') }}</div>
          </div>
          <div v-if="isActive(selected)" class="add-member">
            <select v-model="pickedMember" class="fgs-input" data-testid="team-member-pick" :aria-label="t('gui.settings.teams.add_member')">
              <option value="" disabled>{{ t('gui.settings.teams.add_member_pick') }}</option>
              <option v-for="m in addable" :key="m.user_id" :value="m.user_id">{{ m.username }}</option>
            </select>
            <button type="button" class="fgs-btn pri sm" data-testid="team-member-add" :disabled="changing || !pickedMember" @click="addPicked">
              <SettingsIcon name="plus" size="sm" />{{ t('gui.settings.teams.add_member') }}
            </button>
          </div>
        </div>

        <div class="fgs-sec">
          <div class="fgs-sec-head"><h3>{{ t('gui.settings.teams.roles') }}</h3></div>
          <div class="fgs-rows" data-card="team-roles">
            <div v-for="grant in selectedGrants" :key="grant.project_id" class="fgs-row" :data-grant="grant.project_id">
              <div class="fgs-row-label"><strong>{{ projectName(grant.project_id) }}</strong></div>
              <div class="fgs-row-ctrl">{{ roleName(grant.role_id) }}</div>
            </div>
            <div v-if="!selectedGrants.length" class="fgs-empty" data-testid="team-roles-empty">{{ t('gui.settings.teams.roles_empty') }}</div>
          </div>
          <p class="fgs-hintline">{{ t('gui.settings.teams.roles_hint') }}</p>
        </div>
      </div>
    </section>

    <!-- New team / rename -->
    <ModalShell :open="!!nameForm" size="sm" :aria-label="nameTitle" @close="nameForm = null">
      <template #header><h2 class="fgs-modal-title">{{ nameTitle }}</h2></template>
      <div v-if="nameForm" class="fgs-formfield">
        <label for="team-name">{{ t('gui.settings.teams.name') }}</label>
        <input id="team-name" v-model="nameForm.value" class="fgs-input" :placeholder="t('gui.settings.teams.name_placeholder')" data-testid="team-name" @keydown.enter="submitName" />
      </div>
      <template #footer>
        <div class="fgs-modal-actions">
          <button type="button" class="fgs-btn" @click="nameForm = null">{{ t('gui.common.cancel') }}</button>
          <button type="button" class="fgs-btn pri" :disabled="changing || !nameForm?.value.trim()" data-testid="team-name-confirm" @click="submitName">
            {{ t('gui.common.save') }}
          </button>
        </div>
      </template>
    </ModalShell>

    <ChangeImpactDialog :state="dialog" :busy="changing" :user-name="userName" :project-name="projectName" @confirm="confirm" @cancel="cancel" />
    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </SettingsShell>
</template>

<script setup lang="ts">
// 설정 › 팀 (v02test.0090, design 0004-D §6.1 · Q2-A): a team is a group of people inside one
// workspace. Make a team, rename it, turn it off, put people in and take them out (people come from
// the workspace's members). The roles a team holds on projects are shown here; giving and taking
// them is done in 설정 › 프로젝트 › 멤버 (project.member.manage).
//
// - The screen is offered, and these workspaces listed, only where the server's capabilities allow
//   workspace.team.manage (../capabilities); the server checks the same action on every call.
// - Every change goes preview → impact dialog → apply with the revision (./components/useChangeFlow);
//   a 409 (someone changed access meanwhile / the last project administrator) is shown, never retried
//   silently.
// - Q1-B: with teams manageable in one workspace only, no workspace picker is drawn.
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
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useChangeFlow, type ChangeSpec } from '../components/useChangeFlow'
import { useManageNav } from '../components/manageNav'
import { useSettingsToasts } from '../components/useSettingsToasts'
import { addTeamMember, createTeam, disableTeam, getTeam, getWorkspace, listTeams, removeTeamMember, renameTeam } from '../api'
import { allows, useAccessLists } from '../capabilities'
import type { Team, TeamDetail, WorkspaceMember } from '../types'

const { t, te } = useI18n()
const manageNav = useManageNav()
const lists = useAccessLists()
const { toasts, pushToast, dismissToast } = useSettingsToasts()
const { dialog, busy: changing, run, confirm, cancel } = useChangeFlow((message) => pushToast(message, 'danger'))

const manageable = computed(() =>
  lists.workspaces.value.filter((w) => w.status === 'active' && allows(w.capabilities, 'workspace.team.manage')),
)
const workspaceId = ref('')
watch(
  manageable,
  (rows) => {
    if (!rows.some((w) => w.workspace_id === workspaceId.value)) workspaceId.value = rows[0]?.workspace_id ?? ''
  },
  { immediate: true },
)

const teams = ref<Team[]>([])
const details = ref<Record<string, TeamDetail>>({})
const members = ref<WorkspaceMember[]>([])
const loading = ref(false)
const loaded = ref(false)
const error = ref<string | null>(null)
const selectedId = ref<string | null>(null)
let seq = 0

const isActive = (team: Team) => team.is_active === true || team.is_active === 1

/** Teams of the workspace, each with its people and roles, and the workspace's members to pick from. */
async function load() {
  const id = workspaceId.value
  if (!id) return
  const mine = ++seq
  loading.value = true
  error.value = null
  try {
    const rows = await listTeams(id)
    const [full, workspace] = await Promise.all([
      Promise.all(rows.map((row) => getTeam(id, row.team_id))),
      getWorkspace(id).catch(() => null),
    ])
    if (mine !== seq) return
    teams.value = rows
    details.value = Object.fromEntries(full.map((detail) => [detail.team_id, detail]))
    members.value = workspace?.members ?? []
    loaded.value = true
    if (selectedId.value && !rows.some((row) => row.team_id === selectedId.value)) selectedId.value = null
  } catch (err) {
    if (mine === seq) error.value = err instanceof Error ? err.message : String(err)
  } finally {
    if (mine === seq) loading.value = false
  }
}

watch(workspaceId, () => {
  selectedId.value = null
  void load()
})
onMounted(load)

const selected = computed(() => teams.value.find((row) => row.team_id === selectedId.value) ?? null)
const selectedMembers = computed(() => (selectedId.value ? details.value[selectedId.value]?.members ?? [] : []))
const selectedGrants = computed(() => (selectedId.value ? details.value[selectedId.value]?.project_roles ?? [] : []))
const addable = computed(() =>
  members.value.filter((m) => m.state !== 'disabled' && !selectedMembers.value.includes(m.user_id)),
)

// ── names ──
const userName = (userId: string) => members.value.find((m) => m.user_id === userId)?.username ?? userId
const projectName = (projectId: string) => lists.projects.value.find((p) => p.project_id === projectId)?.project_name ?? projectId
const roleName = (roleId: string) => (te(`gui.settings.project_roles.${roleId}`) ? t(`gui.settings.project_roles.${roleId}`) : roleId)

function rolesText(teamId: string): string {
  const grants = details.value[teamId]?.project_roles ?? []
  return grants.length ? grants.map((g) => `${projectName(g.project_id)} ${roleName(g.role_id)}`).join(' · ') : '—'
}

// ── changes ──
const pickedMember = ref('')

async function change<T>(spec: ChangeSpec<T>, done?: (result: T) => void) {
  const applied = await run(spec)
  if (!applied) return
  await load()
  done?.(applied.result)
}

const nameForm = ref<{ mode: 'create' | 'rename'; value: string } | null>(null)
const nameTitle = computed(() => (nameForm.value?.mode === 'rename' ? t('gui.settings.teams.rename') : t('gui.settings.teams.create')))

function openName(mode: 'create' | 'rename') {
  nameForm.value = { mode, value: mode === 'rename' ? selected.value?.name ?? '' : '' }
}

async function submitName() {
  const form = nameForm.value
  const id = workspaceId.value
  const name = form?.value.trim()
  if (!form || !id || !name || changing.value) return
  nameForm.value = null
  if (form.mode === 'create') {
    await change(
      { title: t('gui.settings.teams.create_title', { name }), request: createTeam(id, name) },
      (team) => {
        selectedId.value = team.team_id
        pushToast(t('gui.settings.teams.created', { name }))
      },
    )
  } else if (selected.value) {
    await change({ title: t('gui.settings.teams.rename_title', { name }), request: renameTeam(id, selected.value.team_id, name) }, () =>
      pushToast(t('gui.settings.teams.renamed', { name })),
    )
  }
}

async function disableSelected() {
  const team = selected.value
  const id = workspaceId.value
  if (!team || !id) return
  await change({ title: t('gui.settings.teams.disable_title', { name: team.name }), request: disableTeam(id, team.team_id), confirmEmpty: true, label: t('gui.settings.teams.disable') }, () =>
    pushToast(t('gui.settings.teams.disabled_done', { name: team.name })),
  )
}

async function addPicked() {
  const team = selected.value
  const id = workspaceId.value
  const userId = pickedMember.value
  if (!team || !id || !userId) return
  pickedMember.value = ''
  await change({ title: t('gui.settings.teams.add_title', { name: userName(userId), team: team.name }), request: addTeamMember(id, team.team_id, userId) }, () =>
    pushToast(t('gui.settings.teams.member_added', { name: userName(userId) })),
  )
}

async function removeMemberOf(userId: string) {
  const team = selected.value
  const id = workspaceId.value
  if (!team || !id) return
  await change({ title: t('gui.settings.teams.remove_title', { name: userName(userId), team: team.name }), request: removeTeamMember(id, team.team_id, userId) }, () =>
    pushToast(t('gui.settings.teams.member_removed', { name: userName(userId) })),
  )
}
</script>

<style scoped>
.team-detail {
  margin-top: 16px;
}

.add-member {
  display: flex;
  gap: 8px;
  margin-top: 10px;
  align-items: center;
}

.add-member select {
  max-width: 280px;
}
</style>
