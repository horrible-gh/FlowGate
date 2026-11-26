<template>
  <div v-if="modern && user" class="fgs-sec" data-card="my-access">
    <div class="fgs-sec-head">
      <h3>{{ t('gui.settings.self.access.title') }}</h3>
      <div class="meta">{{ t('gui.settings.self.access.sub') }}</div>
    </div>

    <p v-if="user.isAdmin" class="fgs-muted" data-testid="my-admin">{{ t('gui.settings.self.access.admin') }}</p>

    <div class="fgs-rows">
      <div class="fgs-row" data-card="my-teams">
        <div class="fgs-row-label"><strong>{{ t('gui.settings.self.access.teams') }}</strong></div>
        <div class="fgs-row-ctrl">
          <span v-for="team in teams" :key="team.team_id" class="fgs-pill" :data-my-team="team.team_id">
            {{ team.name }}<small v-if="workspaceName(team.workspace_id)"> · {{ workspaceName(team.workspace_id) }}</small>
          </span>
          <span v-if="!teams.length" class="fgs-muted" data-testid="my-teams-empty">{{ t('gui.settings.self.access.teams_empty') }}</span>
        </div>
      </div>
    </div>

    <div class="fgs-rows my-projects">
      <div v-for="p in projects" :key="p.project_id" class="fgs-row" :data-my-project="p.project_id">
        <div class="fgs-row-label">
          <strong>{{ projectName(p.project_id) }}</strong>
          <small data-testid="my-project-source">{{ sourceText(p) }}</small>
        </div>
        <div class="fgs-row-ctrl">
          <span class="fgs-pill ok" data-testid="my-project-role">{{ roleName(p.role) }}</span>
          <small class="fgs-muted" data-testid="my-project-actions">{{ actionsText(p.project_id) }}</small>
        </div>
      </div>
      <div v-if="!projects.length && !user.isAdmin" class="fgs-empty" data-testid="my-projects-empty">{{ t('gui.settings.self.access.projects_empty') }}</div>
    </div>
    <p class="fgs-hintline">{{ t('gui.settings.self.access.hint') }}</p>
  </div>
</template>

<script setup lang="ts">
// 내 설정 › 계정 › "내가 속한 팀 · 프로젝트 · 역할" (v02test.0090, design 0004-D §6.1). What /auth/me
// reports about this person — the teams, and for each project the final role and which grants it
// came from (직접 / 팀 / 워크스페이스 값) — with the project names and the actions the server allows
// there (the capabilities of GET /projects). Nothing is worked out here: "할 수 있는 일" lists the
// actions the server marked allowed, never what a role name would suggest.
// It is drawn only when the server reports capabilities; the project and workspace lists are read
// only when the person is in a team or holds a project role (so an account with neither sends nothing).
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import type { MyProject } from '@/core/auth/currentUser'
import { allows, loadSettingsAccess, reportsCapabilities, useAccessLists } from '../capabilities'

const { t, te } = useI18n()
const { user } = useCurrentUser()
const lists = useAccessLists()

const modern = computed(() => reportsCapabilities(user.value))
const teams = computed(() => user.value?.teams ?? [])
const projects = computed(() => user.value?.projects ?? [])

// The actions worth naming in "할 수 있는 일", in the order a person thinks of them.
const SHOWN_ACTIONS = [
  'project.document.update',
  'project.run',
  'project.document.approve',
  'project.document.reject',
  'project.git.write',
  'project.git.merge',
  'project.member.manage',
  'project.settings.edit',
] as const

onMounted(() => {
  if (modern.value && (teams.value.length || projects.value.length)) void loadSettingsAccess()
})

const rowOf = (projectId: string) => lists.projects.value.find((p) => p.project_id === projectId)
const projectName = (projectId: string) => rowOf(projectId)?.project_name ?? projectId
const workspaceName = (workspaceId: string) => lists.workspaces.value.find((w) => w.workspace_id === workspaceId)?.name ?? ''
const roleName = (role: string | null) => (role && te(`gui.settings.project_roles.${role}`) ? t(`gui.settings.project_roles.${role}`) : t('gui.settings.impact.no_role'))
const sourceName = (kind: string) => (te(`gui.settings.sources.${kind}`) ? t(`gui.settings.sources.${kind}`) : kind)

/** "직접: 리뷰어 · 팀: 개발자 · 워크스페이스 값: 개발자" — the parts of the role, as /auth/me reports them. */
function sourceText(project: MyProject): string {
  const parts = Object.entries(project.grants).map(([kind, role]) => `${sourceName(kind)}: ${roleName(role)}`)
  const teamNames = ((rowOf(project.project_id)?.capabilities?.sources ?? []) as { kind?: string; name?: string; role?: string }[])
    .filter((s) => s.kind === 'team' && s.name)
    .map((s) => `${s.name}: ${roleName(s.role ?? null)}`)
  return teamNames.length ? `${parts.join(' · ')} (${teamNames.join(', ')})` : parts.join(' · ')
}

function actionsText(projectId: string): string {
  const caps = rowOf(projectId)?.capabilities
  const names = SHOWN_ACTIONS.filter((action) => allows(caps, action)).map((action) => t(`gui.settings.perm.${action.replace(/\./g, '_')}`))
  return names.length ? names.join(' · ') : t('gui.settings.self.access.view_only')
}
</script>
