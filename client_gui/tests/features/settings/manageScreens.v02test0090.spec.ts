import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser, type CapabilityBlock } from '@/core/auth/currentUser'
import * as api from '@/features/settings/api'
import { loadSettingsAccess, resetSettingsAccess } from '@/features/settings/capabilities'
import MyAccessSection from '@/features/settings/components/MyAccessSection.vue'
import { SETTINGS_PAGES } from '@/features/settings/settingsMenu'
import ProjectMembersView from '@/features/settings/views/ProjectMembersView.vue'
import TeamsView from '@/features/settings/views/TeamsView.vue'
import WorkspaceDetailView from '@/features/settings/views/WorkspaceDetailView.vue'
import { SettingsRequestError } from '@/features/settings/api'
import type { ChangeImpactRow, ChangeMode, ProjectRow } from '@/features/settings/types'

// v02test.0090 card 5 — 설정 › 팀, 설정 › 프로젝트 › 멤버 and 내 설정 › 계정 (내 팀 · 프로젝트 · 역할)
// with the api adapter mocked. A change is a request function that answers a preview for
// { preview: true } and the applied answer for { revision }, like the adapter's ChangeRequest.

vi.mock('@/features/settings/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/settings/api')>()
  const mocked: Record<string, unknown> = { SettingsRequestError: actual.SettingsRequestError }
  for (const [name, value] of Object.entries(actual)) {
    if (typeof value === 'function' && name !== 'SettingsRequestError') mocked[name] = vi.fn()
  }
  return mocked
})

registerAppMessages(i18n)
const m = vi.mocked(api)

const SYSTEM_ACTIONS = ['system.settings.read', 'system.user.read']
const WORKSPACE_ACTIONS = ['workspace.view', 'workspace.member.manage', 'workspace.team.manage']
const MEMBER_ACTIONS = ['project.member.read', 'project.member.manage']

function block(all: string[], allowed: string[], extra: Partial<CapabilityBlock> = {}): CapabilityBlock {
  return { role: null, actions: Object.fromEntries(all.map((id) => [id, allowed.includes(id) ? { allowed: true } : { allowed: false, reason: 'role_limit' }])), ...extra }
}

const ME = { username: 'pa', email: null, isAdmin: false, capabilities: block(SYSTEM_ACTIONS, []) }

const PROJECT: ProjectRow = { project_id: 'p1', project_name: 'P1', description: null, is_active: true, modules: [] }

/** The two lists the screens read their capabilities from. */
async function serveLists(workspaceAllowed: string[], projectCaps: CapabilityBlock) {
  m.listWorkspaces.mockResolvedValue({
    can_create: false,
    workspaces: [{
      workspace_id: 'w1', name: 'WS 1', status: 'active', is_default: true, member_count: 3, project_count: 1, ai_policy_mode: 'off', owner: null,
      my_role: 'admin', can_manage: true, can_admin: false, capabilities: block(WORKSPACE_ACTIONS, workspaceAllowed),
    }],
  } as never)
  m.listAllProjects.mockResolvedValue([{ project_id: 'p1', project_name: 'P1', description: null, is_active: 1, modules: [], capabilities: projectCaps }] as never)
  resetSettingsAccess()
  await loadSettingsAccess(true)
}

function changeRequest(result: unknown, impact: ChangeImpactRow[] = []) {
  return vi.fn(async (mode: ChangeMode) => ('preview' in mode ? { revision: 5, impact } : { revision: 6, result }))
}

let wrapper: VueWrapper | null = null

async function mountView(component: object, props: Record<string, unknown> = {}) {
  const router = createRouter({
    history: createMemoryHistory('/app'),
    routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }, ...SETTINGS_PAGES.map((p) => ({ path: p.path, name: p.routeName, component: { template: '<div />' } }))],
  })
  await router.push('/settings')
  wrapper = mount(component as never, { props, global: { plugins: [i18n, router], stubs: { teleport: true } } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(m)) if (typeof fn === 'function' && 'mockReset' in fn) (fn as ReturnType<typeof vi.fn>).mockReset()
  m.isPreview.mockImplementation(((answer: object) => 'impact' in answer) as never)
  setCurrentUser(ME)
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  resetSettingsAccess()
  setCurrentUser(null)
})

// ── 설정 › 팀 ───────────────────────────────────────────────────────────────────
describe('설정 › 팀', () => {
  const TEAMS = [
    { team_id: 't1', workspace_id: 'w1', name: '디자인팀', is_active: 1 },
    { team_id: 't2', workspace_id: 'w1', name: 'QA팀', is_active: 0 },
  ]
  const DETAILS: Record<string, unknown> = {
    t1: { ...TEAMS[0], members: ['u1', 'u2'], project_roles: [{ project_id: 'p1', role_id: 'role_developer' }] },
    t2: { ...TEAMS[1], members: [], project_roles: [] },
  }

  async function open() {
    await serveLists(['workspace.view', 'workspace.team.manage'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    m.listTeams.mockResolvedValue(TEAMS as never)
    m.getTeam.mockImplementation((async (_ws: string, id: string) => DETAILS[id]) as never)
    m.getWorkspace.mockResolvedValue({
      members: [
        { user_id: 'u1', username: '김하나', email: null, role: 'owner', state: 'active' },
        { user_id: 'u2', username: '이두리', email: null, role: 'member', state: 'active' },
        { user_id: 'u3', username: '박세나', email: null, role: 'member', state: 'active' },
      ],
    } as never)
    return mountView(TeamsView)
  }

  it('lists the teams with their people and the project roles they hold; no workspace picker for one workspace', async () => {
    const w = await open()
    expect(m.listTeams).toHaveBeenCalledWith('w1')
    expect(w.findAll('[data-team]').map((r) => r.attributes('data-team'))).toEqual(['t1', 't2'])
    expect(w.get('[data-team="t1"] [data-testid="team-people"]').text()).toBe('2')
    expect(w.get('[data-team="t1"] [data-testid="team-roles"]').text()).toBe('P1 개발자')
    expect(w.get('[data-team="t2"]').text()).toContain('꺼짐')
    expect(w.find('[data-testid="team-workspace-pick"]').exists()).toBe(false) // Q1-B
  })

  it('makes a team: preview, apply with the revision, then the list is read again', async () => {
    const w = await open()
    const create = changeRequest({ team_id: 't9', workspace_id: 'w1', name: '개발팀', is_active: 1 })
    m.createTeam.mockReturnValue(create)
    await w.get('[data-testid="team-create"]').trigger('click')
    await w.get('[data-testid="team-name"]').setValue('개발팀')
    await w.get('[data-testid="team-name-confirm"]').trigger('click')
    await flushPromises()
    expect(m.createTeam).toHaveBeenCalledWith('w1', '개발팀')
    expect(create.mock.calls).toEqual([[{ preview: true }], [{ revision: 5 }]])
    expect(m.listTeams).toHaveBeenCalledTimes(2)
  })

  it('puts a workspace member in and takes one out', async () => {
    const w = await open()
    const add = changeRequest(null)
    const remove = changeRequest(null)
    m.addTeamMember.mockReturnValue(add)
    m.removeTeamMember.mockReturnValue(remove)
    await w.get('[data-team="t1"]').trigger('click')
    expect(w.get('[data-testid="team-detail-name"]').text()).toBe('디자인팀')
    const options = w.findAll('[data-testid="team-member-pick"] option').map((o) => o.text())
    expect(options).toContain('박세나')
    expect(options).not.toContain('이두리') // already in the team

    await w.get('[data-testid="team-member-pick"]').setValue('u3')
    await w.get('[data-testid="team-member-add"]').trigger('click')
    await flushPromises()
    expect(m.addTeamMember).toHaveBeenCalledWith('w1', 't1', 'u3')
    expect(add.mock.calls).toEqual([[{ preview: true }], [{ revision: 5 }]])

    await w.get('[data-testid="team-member-remove-u2"]').trigger('click')
    await flushPromises()
    expect(m.removeTeamMember).toHaveBeenCalledWith('w1', 't1', 'u2')
  })

  it('turning a team off asks first and shows who is affected', async () => {
    const w = await open()
    const disable = changeRequest(null, [{ user_id: 'u2', project_id: 'p1', before: 'role_developer', after: null, gained: [], lost: ['project.document.update'] }])
    m.disableTeam.mockReturnValue(disable)
    await w.get('[data-team="t1"]').trigger('click')
    await w.get('[data-testid="team-disable"]').trigger('click')
    await flushPromises()
    expect(disable.mock.calls).toEqual([[{ preview: true }]])
    const row = w.get('[data-testid="impact-row"]')
    expect(row.text()).toContain('이두리')
    expect(row.get('[data-testid="impact-role"]').text()).toBe('개발자 → 권한 없음')
    await w.get('[data-testid="impact-apply"]').trigger('click')
    await flushPromises()
    expect(disable.mock.calls).toEqual([[{ preview: true }], [{ revision: 5 }]])
  })

  it('offers nothing where the server does not allow team management', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    const w = await mountView(TeamsView)
    expect(w.find('[data-testid="teams-no-workspace"]').exists()).toBe(true)
    expect(m.listTeams).not.toHaveBeenCalled()
    expect(w.get('[data-testid="team-create"]').attributes('disabled')).toBeDefined()
  })
})

// ── 설정 › 프로젝트 › 멤버 ──────────────────────────────────────────────────────
describe('설정 › 프로젝트 › 멤버', () => {
  const REVIEWER_TO_DEVELOPER: ChangeImpactRow = {
    user_id: 'u2', project_id: 'p1', before: 'role_reviewer', after: 'role_developer',
    gained: ['project.document.update'], lost: ['project.document.approve'],
  }

  function serveMembers() {
    m.listProjectMembers.mockResolvedValue([{ user_id: 'u2', project_id: 'p1', role_id: 'role_reviewer', username: '이두리', email: 'lee@x' }] as never)
    m.listProjectTeamRoles.mockResolvedValue([{ team_id: 't1', name: '디자인팀', is_active: 1, role_id: 'role_developer' }])
    m.getProjectMemberCandidates.mockResolvedValue({ users: [{ user_id: 'u3', username: '박세나', role: 'member' }], teams: [{ team_id: 't2', name: 'QA팀' }] })
  }

  it('a project administrator sees the people and teams with their roles and can change them', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    expect(m.listProjectMembers).toHaveBeenCalledWith('p1')
    expect(w.findAll('[data-member-row]').map((r) => r.attributes('data-member-row'))).toEqual(['team:t1', 'user:u2'])
    expect((w.get('[data-testid="role-user:u2"]').element as HTMLSelectElement).value).toBe('role_reviewer')
    expect((w.get('[data-testid="role-team:t1"]').element as HTMLSelectElement).value).toBe('role_developer')
    expect(w.find('[data-testid="members-disabled-reason"]').exists()).toBe(false)
    expect(w.get('[data-testid="member-add-user"]').attributes('disabled')).toBeUndefined()
  })

  it('previews "리뷰어 → 개발자 (승인 권한 없어짐)" and applies with the preview\'s revision only on 적용', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    const change = changeRequest(null, [REVIEWER_TO_DEVELOPER])
    m.assignProjectUserRole.mockReturnValue(change)
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    await w.get('[data-testid="role-user:u2"]').setValue('role_developer')
    await flushPromises()
    expect(m.assignProjectUserRole).toHaveBeenCalledWith('p1', 'u2', 'role_developer')
    const row = w.get('[data-testid="impact-row"]')
    expect(row.get('[data-testid="impact-role"]').text()).toBe('리뷰어 → 개발자')
    expect(row.get('[data-testid="impact-lost"]').text()).toContain('승인')
    expect(row.get('[data-testid="impact-gained"]').text()).toContain('편집')
    expect(change.mock.calls).toEqual([[{ preview: true }]])

    await w.get('[data-testid="impact-apply"]').trigger('click')
    await flushPromises()
    expect(change.mock.calls).toEqual([[{ preview: true }], [{ revision: 5 }]])
    expect(m.listProjectMembers).toHaveBeenCalledTimes(2) // read again after the change
  })

  it('409 "someone changed it first" re-reads the preview and says so; nothing was applied silently', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    let previews = 0
    const change = vi.fn(async (mode: ChangeMode) => {
      if ('preview' in mode) {
        previews += 1
        return { revision: previews === 1 ? 5 : 8, impact: [REVIEWER_TO_DEVELOPER] }
      }
      if (mode.revision === 5) throw new SettingsRequestError('다른 사람이 먼저 바꿨습니다. 새로 불러옵니다.', 'revision_conflict', 409)
      return { revision: 9, result: null }
    })
    m.assignProjectUserRole.mockReturnValue(change)
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    await w.get('[data-testid="role-user:u2"]').setValue('role_developer')
    await flushPromises()
    await w.get('[data-testid="impact-apply"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="impact-refreshed"]').exists()).toBe(true)
    expect(w.find('[data-testid="impact-error"]').exists()).toBe(false)
    expect(change.mock.calls.filter(([mode]) => !('preview' in mode))).toEqual([[{ revision: 5 }]])
    await w.get('[data-testid="impact-apply"]').trigger('click')
    await flushPromises()
    expect(change.mock.calls.at(-1)).toEqual([{ revision: 8 }])
  })

  it('the last project administrator: the server\'s 409 reason stays in the dialog', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    const change = vi.fn(async (mode: ChangeMode) => {
      if ('preview' in mode) return { revision: 5, impact: [REVIEWER_TO_DEVELOPER] }
      throw new SettingsRequestError('마지막 프로젝트 관리자는 빼거나 낮출 수 없습니다.', 'last_project_admin', 409)
    })
    m.revokeProjectUserRole.mockReturnValue(change)
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    await w.get('[data-testid="remove-user:u2"]').trigger('click')
    await flushPromises()
    await w.get('[data-testid="impact-apply"]').trigger('click')
    await flushPromises()
    expect(w.get('[data-testid="impact-error"]').text()).toBe('마지막 프로젝트 관리자는 빼거나 낮출 수 없습니다.')
  })

  it('someone who may read but not manage sees the list with the controls off and the server\'s reason', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, ['project.member.read']))
    serveMembers()
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    expect(w.findAll('[data-member-row]')).toHaveLength(2)
    expect(w.get('[data-testid="members-disabled-reason"]').text()).toContain('역할로는 할 수 없습니다')
    expect(w.get('[data-testid="member-add-user"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="member-add-team"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="role-user:u2"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="remove-user:u2"]').attributes('disabled')).toBeDefined()
    expect(m.getProjectMemberCandidates).not.toHaveBeenCalled() // only a manager may read the candidates
  })

  it('someone without project.member.read gets no list and no request', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, []))
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    expect(w.find('[data-testid="members-no-access"]').exists()).toBe(true)
    expect(m.listProjectMembers).not.toHaveBeenCalled()
    expect(m.listProjectTeamRoles).not.toHaveBeenCalled()
  })

  it('adds a person from the workspace members with a role', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    const add = changeRequest(null)
    m.assignProjectUserRole.mockReturnValue(add)
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    await w.get('[data-testid="member-add-user"]').trigger('click')
    expect(w.findAll('[data-testid="member-pick"] option').map((o) => o.text())).toContain('박세나')
    await w.get('[data-testid="member-pick"]').setValue('u3')
    await w.get('[data-testid="member-add-confirm"]').trigger('click')
    await flushPromises()
    expect(m.assignProjectUserRole).toHaveBeenCalledWith('p1', 'u3', 'role_developer')
    expect(add.mock.calls).toEqual([[{ preview: true }], [{ revision: 5 }]])
  })

  it('C-3: without project.member.manage the rows are static (no hover / pointer) and every control is really disabled', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, ['project.member.read']))
    serveMembers()
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    const rows = w.findAll('[data-member-row]')
    expect(rows).toHaveLength(2)
    for (const row of rows) expect(row.classes()).toContain('static')
    for (const key of ['team:t1', 'user:u2']) {
      expect(w.get(`[data-testid="role-${key}"]`).attributes('disabled')).toBeDefined()
      expect(w.get(`[data-testid="remove-${key}"]`).attributes('disabled')).toBeDefined()
    }
  })

  it('C-3: the row hover highlight never applies to a static (read-only) row', () => {
    // jsdom cannot hover, so check the stylesheet: every .fgs-dtr.row :hover rule must skip .static.
    const settingsCss = readFileSync(resolve(__dirname, '../../../src/features/settings/settings.css'), 'utf8')
    const hoverSelectors = settingsCss
      .replace(/\/\*[\s\S]*?\*\//g, '')
      .split('}')
      .map((rule) => rule.split('{')[0])
      .flatMap((sel) => sel.split(','))
      .map((sel) => sel.trim())
      .filter((sel) => sel.startsWith('.fgs-dtr') && sel.includes(':hover'))
    expect(hoverSelectors).toEqual(['.fgs-dtr.row:where(:not(.static)):hover'])
  })

  it('C-3: a manager\'s rows stay interactive; a disabled account\'s row is dimmed with the workspace table\'s class', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    m.listProjectMembers.mockResolvedValue([
      { user_id: 'u2', project_id: 'p1', role_id: 'role_reviewer', username: '이두리', email: 'lee@x', is_active: 1 },
      { user_id: 'u4', project_id: 'p1', role_id: 'role_viewer', username: '최하늘', email: null, is_active: 0 },
    ] as never)
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    const active = w.get('[data-member-row="user:u2"]')
    expect(active.classes()).not.toContain('static')
    expect(active.classes()).not.toContain('dim')
    expect(w.get('[data-member-row="user:u4"]').classes()).toContain('dim')
    expect(w.get('[data-testid="role-user:u2"]').attributes('disabled')).toBeUndefined()
  })

  it('C-4: the role picker offers the four fixed roles only (no Manager / Worker)', async () => {
    await serveLists(['workspace.view'], block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    serveMembers()
    const w = await mountView(ProjectMembersView, { project: PROJECT })
    const values = w.findAll('[data-testid="role-user:u2"] option').map((o) => (o.element as HTMLOptionElement).value)
    expect(values).toEqual(['role_admin', 'role_developer', 'role_reviewer', 'role_viewer'])
  })
})

// ── 관리 › 워크스페이스 설정 ───────────────────────────────────────────────────────
describe('관리 › 워크스페이스 설정 (one workspace)', () => {
  const DETAIL = {
    workspace_id: 'w1', name: 'WS 1', status: 'active', is_default: true, member_count: 0, project_count: 0, ai_policy_mode: 'off', owner: null,
    my_role: 'admin', can_manage: true, can_admin: false, capabilities: block(WORKSPACE_ACTIONS, WORKSPACE_ACTIONS),
    members: [], projects: [], active_project_count: 0,
    access: { defaults: { owner: 'admin', admin: 'admin', member: 'dev' }, exceptions: [] },
    policy: { ai_mode: 'off', allowlist: [], project_create: 'members' },
  }

  it('C-1: 팀 stays listed next to 워크스페이스 설정 when the settings screen is opened', async () => {
    await serveLists(WORKSPACE_ACTIONS, block(MEMBER_ACTIONS, MEMBER_ACTIONS))
    m.getWorkspace.mockResolvedValue(DETAIL as never)
    const w = await mountView(WorkspaceDetailView, { workspaceId: 'w1' })
    const keys = w.findAll('[data-subnav]').map((item) => item.attributes('data-subnav'))
    expect(keys).toEqual(expect.arrayContaining(['teams', 'workspace', 'members', 'projects']))
    expect(w.get('[data-subnav="workspace"]').attributes('aria-current')).toBe('page')
    expect(w.get('[data-subnav="teams"]').attributes('href')).toBe('/app/settings/manage/teams')
  })
})

// ── 내 설정 › 계정: 내가 속한 팀 · 프로젝트 · 역할 ─────────────────────────────────
describe('내 설정 › 계정 › 내 팀 · 프로젝트 · 역할', () => {
  const projectCaps: CapabilityBlock = {
    role: 'role_developer',
    sources: [{ kind: 'direct', role: 'role_reviewer' }, { kind: 'team', team_id: 't1', name: '디자인팀', role: 'role_developer' }],
    actions: {
      'project.document.update': { allowed: true },
      'project.run': { allowed: true },
      'project.document.approve': { allowed: false, reason: 'role_limit', minimum_role: 'role_reviewer' },
    },
  }

  it('shows the teams, the final role, where it came from and what the server allows', async () => {
    setCurrentUser({
      ...ME,
      teams: [{ team_id: 't1', name: '디자인팀', workspace_id: 'w1' }],
      projects: [{ project_id: 'p1', role: 'role_developer', sources: ['direct', 'team'], grants: { direct: 'role_reviewer', team: 'role_developer' } }],
    })
    await serveLists(['workspace.view'], projectCaps)
    const w = await mountView(MyAccessSection)
    expect(w.get('[data-my-team="t1"]').text()).toContain('디자인팀')
    expect(w.get('[data-my-team="t1"]').text()).toContain('WS 1')
    const row = w.get('[data-my-project="p1"]')
    expect(row.text()).toContain('P1')
    expect(row.get('[data-testid="my-project-role"]').text()).toBe('개발자')
    expect(row.get('[data-testid="my-project-source"]').text()).toContain('직접: 리뷰어')
    expect(row.get('[data-testid="my-project-source"]').text()).toContain('팀: 개발자')
    expect(row.get('[data-testid="my-project-source"]').text()).toContain('디자인팀')
    const actions = row.get('[data-testid="my-project-actions"]').text()
    expect(actions).toContain('편집')
    expect(actions).toContain('실행')
    expect(actions).not.toContain('승인') // the server marked approve refused
  })

  it('reads the project and workspace lists only when the person has a team or a project role', async () => {
    setCurrentUser({ ...ME, teams: [], projects: [] })
    const w = await mountView(MyAccessSection)
    expect(m.listWorkspaces).not.toHaveBeenCalled()
    expect(m.listAllProjects).not.toHaveBeenCalled()
    expect(w.get('[data-testid="my-teams-empty"]').exists()).toBe(true)
    expect(w.get('[data-testid="my-projects-empty"]').exists()).toBe(true)
  })

  it('draws nothing on an older server (no capabilities)', async () => {
    setCurrentUser({ username: 'kim', email: null, isAdmin: false })
    const w = await mountView(MyAccessSection)
    expect(w.find('[data-card="my-access"]').exists()).toBe(false)
    expect(m.listWorkspaces).not.toHaveBeenCalled()
  })
})
