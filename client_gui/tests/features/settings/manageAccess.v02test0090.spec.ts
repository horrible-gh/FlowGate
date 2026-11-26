import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser, toCurrentUser, type CapabilityBlock } from '@/core/auth/currentUser'
import { decideSettingsAccess } from '@/features/settings/access'
import { allows, evaluateRequirement, loadSettingsAccess, resetSettingsAccess, type AccessLists } from '@/features/settings/capabilities'
import { SETTINGS_PAGES, useSettingsMenu } from '@/features/settings/settingsMenu'
import * as api from '@/features/settings/api'

// v02test.0090 card 5 — who is offered which settings screen follows the capabilities the server sent
// (/auth/me for the system scope, the rows of GET /workspaces and GET /projects for the rest); an
// older server that sends none keeps the administrator-only rule.

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

const SYSTEM_ACTIONS = ['system.settings.read', 'system.settings.manage', 'system.user.read', 'system.user.create', 'system.user.update', 'system.user.disable', 'system.user.assign_admin', 'system.workspace.create']
const WORKSPACE_ACTIONS = ['workspace.view', 'workspace.member.manage', 'workspace.team.manage', 'workspace.admin', 'workspace.project.create']

/** A capabilities block that allows exactly `allowed` out of `all`. */
function block(all: string[], allowed: string[]): CapabilityBlock {
  return { role: null, actions: Object.fromEntries(all.map((id) => [id, allowed.includes(id) ? { allowed: true } : { allowed: false, reason: 'role_limit' }])) }
}

const ADMIN = { username: 'admin', email: null, isAdmin: true, capabilities: block(SYSTEM_ACTIONS, SYSTEM_ACTIONS) }
const PROJECT_ADMIN = { username: 'pa', email: null, isAdmin: false, capabilities: block(SYSTEM_ACTIONS, []) }

function serve(workspaceAllowed: string[], projectAllowed: string[], workspaces = 1) {
  m.listWorkspaces.mockResolvedValue({
    can_create: false,
    workspaces: Array.from({ length: workspaces }, (_, i) => ({
      workspace_id: `w${i + 1}`, name: `WS ${i + 1}`, status: 'active', is_default: i === 0, member_count: 1, project_count: 1, ai_policy_mode: 'off',
      owner: null, my_role: 'member', can_manage: false, can_admin: false, capabilities: block(WORKSPACE_ACTIONS, workspaceAllowed),
    })),
  } as never)
  m.listAllProjects.mockResolvedValue([
    { project_id: 'p1', project_name: 'P1', description: null, is_active: 1, modules: [], capabilities: block(['project.settings.read', 'project.member.read', 'project.member.manage'], projectAllowed) },
    { project_id: '__SYSTEM__', project_name: 'sys', is_active: 1 },
  ] as never)
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(m)) if (typeof fn === 'function' && 'mockReset' in fn) (fn as ReturnType<typeof vi.fn>).mockReset()
  resetSettingsAccess()
  setCurrentUser(null)
})

afterEach(() => {
  resetSettingsAccess()
  setCurrentUser(null)
})

describe('toCurrentUser', () => {
  it('reads what /auth/me sends: capabilities, teams and project roles', () => {
    const user = toCurrentUser({
      username: 'kim', email: null, is_admin: false,
      capabilities: { role: null, sources: [], actions: { 'system.user.read': { allowed: false, reason: 'system_admin_only' } } },
      teams: [{ team_id: 't1', name: '디자인팀', workspace_id: 'w1' }],
      projects: [{ project_id: 'p1', role: 'role_developer', sources: ['team'], grants: { team: 'role_developer' } }],
    })
    expect(user?.capabilities?.actions['system.user.read']).toEqual({ allowed: false, reason: 'system_admin_only' })
    expect(user?.teams).toEqual([{ team_id: 't1', name: '디자인팀', workspace_id: 'w1' }])
    expect(user?.projects?.[0]).toEqual({ project_id: 'p1', role: 'role_developer', sources: ['team'], grants: { team: 'role_developer' } })
  })

  it('an older server\'s answer reads exactly as before: no capabilities, teams or projects keys', () => {
    expect(toCurrentUser({ username: 'admin', email: 'a@b', is_admin: true })).toEqual({ username: 'admin', email: 'a@b', isAdmin: true })
    expect(Object.keys(toCurrentUser({ username: 'admin', is_admin: true })!)).toEqual(['username', 'email', 'isAdmin'])
  })
})

describe('evaluateRequirement', () => {
  const lists = (status: AccessLists['status'], allowed: string[] = []): AccessLists => ({
    status,
    workspaces: [],
    projects: [{ project_id: 'p1', project_name: 'P1', is_active: true, capabilities: block(['project.member.read', 'project.settings.read'], allowed) }],
  })
  const members = { scope: 'project', actions: ['project.member.read'] } as const

  it('system scope follows the system-scope entries of /auth/me', () => {
    expect(evaluateRequirement({ scope: 'system', actions: ['system.user.read'] }, ADMIN, null)).toBe('allow')
    expect(evaluateRequirement({ scope: 'system', actions: ['system.user.read'] }, PROJECT_ADMIN, null)).toBe('deny')
  })

  it('workspace / project scope waits for the lists, then asks whether ANY target allows it', () => {
    expect(evaluateRequirement(members, PROJECT_ADMIN, lists('idle'))).toBe('pending')
    expect(evaluateRequirement(members, PROJECT_ADMIN, lists('loading'))).toBe('pending')
    expect(evaluateRequirement(members, PROJECT_ADMIN, lists('ready', ['project.member.read']))).toBe('allow')
    expect(evaluateRequirement(members, PROJECT_ADMIN, lists('ready', ['project.settings.read']))).toBe('deny')
    expect(evaluateRequirement(members, PROJECT_ADMIN, lists('failed'))).toBe('deny')
  })

  it('an older server (no capabilities): administrators only, nothing to wait for', () => {
    expect(evaluateRequirement(members, { isAdmin: true }, null)).toBe('allow')
    expect(evaluateRequirement(members, { isAdmin: false }, lists('ready', ['project.member.read']))).toBe('deny')
  })

  it('a missing entry is a refusal, never an allowance', () => {
    expect(allows(undefined, 'project.member.read')).toBe(false)
    expect(allows({ actions: {} }, 'project.member.read')).toBe(false)
  })
})

describe('decideSettingsAccess with capabilities', () => {
  const target = (path: string, registered = true) => ({ path, registered, adminOnly: false })
  const ready = (user: typeof ADMIN | typeof PROJECT_ADMIN, lists: AccessLists) => ({ status: 'ready' as const, user, lists })
  const projectLists: AccessLists = {
    status: 'ready',
    workspaces: [{ workspace_id: 'w1', name: 'WS', status: 'active', capabilities: block(WORKSPACE_ACTIONS, ['workspace.view']) }],
    projects: [{ project_id: 'p1', project_name: 'P1', is_active: true, capabilities: block(['project.settings.read', 'project.member.read'], ['project.member.read']) }],
  }

  it('a project administrator opens the project screens but not users, system or teams', () => {
    const auth = ready(PROJECT_ADMIN, projectLists)
    expect(decideSettingsAccess(target('/settings/project/p1/members'), auth)).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/workspaces/w1'), auth)).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/system/users'), auth)).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/system/general'), auth)).toEqual({ kind: 'home', notice: 'admin_only' })
    expect(decideSettingsAccess(target('/settings/manage/teams'), auth)).toEqual({ kind: 'home', notice: 'no_permission' })
  })

  it('waits for the lists before deciding a workspace / project screen', () => {
    const waiting: AccessLists = { status: 'loading', workspaces: [], projects: [] }
    expect(decideSettingsAccess(target('/settings/project/p1/members'), ready(PROJECT_ADMIN, waiting))).toEqual({ kind: 'pending' })
    expect(decideSettingsAccess(target('/settings/manage/teams'), ready(PROJECT_ADMIN, waiting))).toEqual({ kind: 'pending' })
    // a system screen needs no list: /auth/me alone decides
    expect(decideSettingsAccess(target('/settings/system/users'), ready(PROJECT_ADMIN, waiting))).toEqual({ kind: 'home', notice: 'admin_only' })
  })

  it('the system administrator opens everything the server allows, the Workspace screens included', () => {
    const auth = ready(ADMIN, { status: 'ready', workspaces: [{ workspace_id: 'w1', name: 'WS', status: 'active', capabilities: block(WORKSPACE_ACTIONS, WORKSPACE_ACTIONS) }], projects: projectLists.projects })
    expect(decideSettingsAccess(target('/settings/system/users'), auth)).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/manage/teams'), auth)).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/workspaces'), auth)).toEqual({ kind: 'allow' })
  })

  it('the screens that need the new server API stay closed on an older server, even for an administrator', () => {
    const older = { status: 'ready' as const, user: { isAdmin: true } }
    for (const path of ['/settings/workspaces', '/settings/workspaces/w1', '/settings/manage/teams', '/settings/project/p1/members']) {
      expect(decideSettingsAccess(target(path), older)).toEqual({ kind: 'home', notice: null })
    }
    // the administrator-only screens it already had still open
    expect(decideSettingsAccess(target('/settings/system/users'), older)).toEqual({ kind: 'allow' })
    expect(decideSettingsAccess(target('/settings/project/p1/git'), older)).toEqual({ kind: 'allow' })
  })
})

describe('the settings menu', () => {
  const Probe = defineComponent({
    setup() {
      const menu = useSettingsMenu()
      return () => h('ul', menu.value.map((page) => h('li', { 'data-page': `${page.scope}:${page.key}`, 'data-path': page.path, 'data-label': page.labelKey })))
    },
  })

  async function render() {
    const router = createRouter({
      history: createMemoryHistory('/app'),
      routes: [{ path: '/:rest(.*)*', component: { template: '<div />' } }, ...SETTINGS_PAGES.map((p) => ({ path: p.path, name: p.routeName, component: { template: '<div />' } }))],
    })
    await router.push('/settings')
    const wrapper = mount(Probe, { global: { plugins: [i18n, router] } })
    await flushPromises()
    return wrapper
  }
  const pages = (w: Awaited<ReturnType<typeof render>>) => w.findAll('li').map((li) => li.attributes('data-page'))

  it('a system administrator sees every scope', async () => {
    serve(WORKSPACE_ACTIONS, ['project.settings.read', 'project.member.read', 'project.member.manage'], 2)
    setCurrentUser(ADMIN)
    const w = await render()
    expect(pages(w)).toEqual([
      'me:account', 'me:theme', 'me:agents', 'me:security',
      'manage:teams', 'manage:workspace', 'project:project',
      'system:general', 'system:agents', 'system:voice', 'system:users', 'system:projects',
    ])
    expect(w.get('[data-page="manage:workspace"]').attributes('data-path')).toBe('/settings/workspaces') // two workspaces: the list
  })

  it('a project administrator gets 내 설정, 워크스페이스 설정 and 프로젝트, not 사용자 / 팀 / 시스템', async () => {
    serve(['workspace.view'], ['project.settings.read', 'project.member.read', 'project.member.manage'])
    setCurrentUser(PROJECT_ADMIN)
    const w = await render()
    expect(pages(w)).toEqual(['me:account', 'me:theme', 'me:agents', 'me:security', 'manage:workspace', 'project:project'])
    // one workspace: the entry is the "워크스페이스 설정" screen itself (no list in between)
    const entry = w.get('[data-page="manage:workspace"]')
    expect(entry.attributes('data-path')).toBe('/settings/workspaces/w1')
    expect(entry.attributes('data-label')).toBe('gui.settings.manage_nav.workspace_settings')
  })

  it('a person who may manage teams sees 팀', async () => {
    serve(['workspace.view', 'workspace.team.manage'], ['project.member.read'])
    setCurrentUser(PROJECT_ADMIN)
    expect(pages(await render())).toContain('manage:teams')
  })

  it('a non-member sees 내 설정 only (the lists are empty)', async () => {
    m.listWorkspaces.mockResolvedValue({ can_create: false, workspaces: [] })
    m.listAllProjects.mockResolvedValue([])
    setCurrentUser(PROJECT_ADMIN)
    expect(pages(await render())).toEqual(['me:account', 'me:theme', 'me:agents', 'me:security'])
  })

  it('a failed read of the lists offers nothing beyond 내 설정', async () => {
    m.listWorkspaces.mockRejectedValue(new Error('500'))
    m.listAllProjects.mockResolvedValue([])
    setCurrentUser(PROJECT_ADMIN)
    expect(pages(await render())).toEqual(['me:account', 'me:theme', 'me:agents', 'me:security'])
  })

  it('an older server keeps the old menu: administrators get 프로젝트 and 시스템, nobody gets the new screens, nothing is read', async () => {
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    const w = await render()
    expect(pages(w)).toEqual(['me:account', 'me:theme', 'me:agents', 'me:security', 'project:project', 'system:general', 'system:agents', 'system:voice', 'system:users', 'system:projects'])
    expect(m.listWorkspaces).not.toHaveBeenCalled()
    expect(m.listAllProjects).not.toHaveBeenCalled()
    resetSettingsAccess()
    setCurrentUser({ username: 'kim', email: null, isAdmin: false })
    expect(pages(await render())).toEqual(['me:account', 'me:theme', 'me:agents', 'me:security'])
  })

  it('reads the two lists once however many menus ask', async () => {
    serve(['workspace.view'], ['project.member.read'])
    setCurrentUser(PROJECT_ADMIN)
    await render()
    await render()
    await loadSettingsAccess()
    expect(m.listWorkspaces).toHaveBeenCalledTimes(1)
    expect(m.listAllProjects).toHaveBeenCalledTimes(1)
  })
})
