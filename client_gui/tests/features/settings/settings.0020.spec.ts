import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import * as api from '@/features/settings/api'
import {
  SettingsHubView,
  SystemSecurityView,
  SystemUsersView,
  WorkspaceDetailView,
  WorkspaceListView,
} from '@/features/settings'
import type {
  SecurityResponse,
  SystemUsersResponse,
  WorkspaceDetail,
  WorkspaceListResponse,
} from '@/features/settings/types'

// flowgate.v02.0020 — 환경 설정 시안 11~14 wired to the real API adapter (mocked here).
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

function makeRouter(): Router {
  const stub = { template: '<div />' }
  return createRouter({
    history: createMemoryHistory('/app'),
    routes: [
      { path: '/settings', component: SettingsHubView },
      { path: '/settings/workspaces', component: WorkspaceListView },
      { path: '/settings/workspaces/:workspaceId', component: WorkspaceDetailView, props: true },
      { path: '/settings/system/users', component: SystemUsersView },
      { path: '/settings/system/security', component: SystemSecurityView },
      { path: '/:rest(.*)*', component: stub },
    ],
  })
}

let wrapper: VueWrapper | null = null

async function mountAt(url: string, router = makeRouter()) {
  await router.push(url)
  await router.isReady()
  const view = router.currentRoute.value.matched[0].components!.default
  const props = router.currentRoute.value.params.workspaceId ? { workspaceId: router.currentRoute.value.params.workspaceId as string } : {}
  wrapper = mount(view as never, {
    props,
    global: { plugins: [i18n, router], stubs: { teleport: true } },
  })
  await flushPromises()
  return { wrapper, router }
}

const btn = (w: VueWrapper, selector: string) => w.get(selector)

function workspaceList(): WorkspaceListResponse {
  return {
    can_create: true,
    workspaces: [
      {
        workspace_id: 'ws_default', name: 'FlowGate Workspace', status: 'active', is_default: true, member_count: 12,
        project_count: 4, ai_policy_mode: 'allowlist', owner: { user_id: 'u1', username: 'Jung-Min Shin', email: null },
        my_role: 'owner', can_manage: true, can_admin: true,
      },
      {
        workspace_id: 'ws_lab', name: 'Personal Lab', status: 'active', is_default: false, member_count: 1,
        project_count: 0, ai_policy_mode: 'off', owner: { user_id: 'u1', username: 'Jung-Min Shin', email: null },
        my_role: 'owner', can_manage: true, can_admin: true,
      },
      {
        workspace_id: 'ws_arc', name: 'Archive Team', status: 'disabled', is_default: false, member_count: 3,
        project_count: 1, ai_policy_mode: 'off', owner: { user_id: 'u2', username: 'Kim Minseo', email: null },
        my_role: null, can_manage: true, can_admin: true,
      },
    ],
  }
}

function detail(overrides: Partial<WorkspaceDetail> = {}): WorkspaceDetail {
  return {
    ...workspaceList().workspaces[0],
    members: [
      { user_id: 'u1', username: 'Jung-Min Shin', email: 'admin@flowgate.local', role: 'owner', state: 'active', is_system_admin: true, last_login_at: null, joined_at: null, project_count: 2 },
      { user_id: 'u2', username: 'Kim Minseo', email: 'minseo@flowgate.local', role: 'admin', state: 'active', is_system_admin: false, last_login_at: null, joined_at: null, project_count: 2 },
      { user_id: 'u3', username: 'Park Jiwon', email: 'jiwon@example.com', role: 'member', state: 'invited', is_system_admin: false, last_login_at: null, joined_at: null, project_count: 0 },
    ],
    projects: [
      { project_id: 'flowgate', project_name: 'FlowGate', is_active: true },
      { project_id: 'mirage', project_name: 'MirageGlass', is_active: true },
    ],
    active_project_count: 2,
    access: {
      defaults: { owner: 'admin', admin: 'dev', member: 'none' },
      exceptions: [{ project_id: 'flowgate', user_id: 'u3', access: 'review' }],
    },
    policy: {
      ai_mode: 'allow',
      allowlist: [{ resource_type: 'provider', resource_id: 'aip_a', name: 'OpenAI · GPT', available: true, status: 'approved', source: 'admin' }],
      project_create: 'owner_admin',
    },
    ...overrides,
  }
}

function users(): SystemUsersResponse {
  return {
    summary: { total: 3, active: 2, invited: 1, disabled: 0, admins: 1, tfa_missing: 2, tfa_enabled: 1, no_workspace: 1 },
    workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace' }],
    users: [
      { user_id: 'u1', username: 'Jung-Min Shin', email: 'admin@flowgate.local', is_admin: true, state: 'active', tfa_enabled: true, workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace', role: 'owner' }], last_login_at: null, created_at: '2026-03-02T00:00:00+00:00' },
      { user_id: 'u2', username: 'Kim Minseo', email: 'minseo@flowgate.local', is_admin: false, state: 'active', tfa_enabled: false, workspaces: [{ workspace_id: 'ws_default', name: 'FlowGate Workspace', role: 'admin' }], last_login_at: null, created_at: '2026-04-18T00:00:00+00:00' },
      { user_id: 'u3', username: 'Park Jiwon', email: 'jiwon@example.com', is_admin: false, state: 'invited', tfa_enabled: false, workspaces: [], last_login_at: null, created_at: '2026-09-30T00:00:00+00:00' },
    ],
  }
}

function security(overrides: Partial<SecurityResponse['policy']> = {}): SecurityResponse {
  return {
    policy: {
      tfa_scope: 'admin', tfa_grace_days: 7, recovery_codes: true, password_min_length: 10,
      lock_attempts: 5, lock_minutes: 15, idle_timeout: '8h', logout_on_tighten: false, ...overrides,
    },
    people: [
      { user_id: 'u1', username: 'Jung-Min Shin', is_admin: true, tfa_enabled: true, state: 'active' },
      { user_id: 'u4', username: 'Choi Yuna', is_admin: true, tfa_enabled: false, state: 'active' },
      { user_id: 'u2', username: 'Kim Minseo', is_admin: false, tfa_enabled: false, state: 'active' },
    ],
  }
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'Jung-Min Shin', email: 'admin@flowgate.local', isAdmin: true })
  for (const fn of Object.values(m)) if (typeof fn === 'function' && 'mockReset' in fn) (fn as ReturnType<typeof vi.fn>).mockReset()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
})

describe('환경 설정 hub (시안 11 #hub)', () => {
  it('opens 워크스페이스 and, for an administrator, 시스템; 사용자/프로젝트 stay inert', async () => {
    const { wrapper } = await mountAt('/settings')
    expect(wrapper.find('[data-scope-card="workspace"] a').attributes('href')).toBe('/app/settings/workspaces')
    expect(wrapper.find('[data-scope-card="system"] a').attributes('href')).toBe('/app/settings/system/users')
    expect(wrapper.find('[data-scope-card="user"] a').exists()).toBe(false)
    expect(wrapper.find('[data-scope-card="project"] a').exists()).toBe(false)
  })

  it('keeps 시스템 closed to a non-administrator', async () => {
    setCurrentUser({ username: 'kim', email: null, isAdmin: false })
    const { wrapper } = await mountAt('/settings')
    expect(wrapper.find('[data-scope-card="system"] a').exists()).toBe(false)
    expect(wrapper.find('[data-scope-card="system"]').text()).toContain('시스템 관리자 전용')
  })
})

describe('워크스페이스 목록 (시안 11)', () => {
  it('renders the cards with counts, AI policy and filters by search and state', async () => {
    m.listWorkspaces.mockResolvedValue(workspaceList())
    const { wrapper } = await mountAt('/settings/workspaces')
    const cards = wrapper.findAll('[data-workspace]')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('기본')
    expect(cards[0].get('[data-testid="ws-ai"]').text()).toBe('On')
    expect(cards[1].get('[data-testid="ws-ai"]').text()).toBe('Off')
    expect(cards[2].get('[data-testid="ws-ai"]').text()).toBe('—')
    expect(wrapper.get('[data-filter="disabled"]').text()).toContain('1')

    await wrapper.get('[data-filter="disabled"]').trigger('click')
    expect(wrapper.findAll('[data-workspace]').map((c) => c.attributes('data-workspace'))).toEqual(['ws_arc'])
    await wrapper.get('[data-filter="all"]').trigger('click')
    await wrapper.get('[data-testid="ws-search"]').setValue('lab')
    expect(wrapper.findAll('[data-workspace]').map((c) => c.attributes('data-workspace'))).toEqual(['ws_lab'])
  })

  it('creates a workspace with the chosen owner and opens it', async () => {
    m.listWorkspaces.mockResolvedValue(workspaceList())
    m.listSystemUsers.mockResolvedValue(users())
    m.createWorkspace.mockResolvedValue(detail({ workspace_id: 'ws_new', name: 'Data Platform' }))
    const { wrapper, router } = await mountAt('/settings/workspaces')
    await btn(wrapper, '[data-testid="ws-create"]').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="ws-new-owner"]').element as HTMLSelectElement).value).toBe('u1')
    await wrapper.get('[data-testid="ws-new-name"]').setValue('Data Platform')
    await btn(wrapper, '[data-testid="ws-create-confirm"]').trigger('click')
    await flushPromises()
    expect(m.createWorkspace).toHaveBeenCalledWith('Data Platform', 'u1')
    expect(router.currentRoute.value.fullPath).toBe('/settings/workspaces/ws_new')
  })

  it('disables from the menu and locks delete while projects or members remain', async () => {
    m.listWorkspaces.mockResolvedValue(workspaceList())
    m.updateWorkspace.mockResolvedValue(detail())
    const { wrapper } = await mountAt('/settings/workspaces')
    const menus = wrapper.findAll('[data-testid="ws-menu"]')
    await menus[0].trigger('click')
    expect(wrapper.get('[data-testid="ws-toggle"]').attributes('disabled')).toBeDefined() // default workspace
    expect(wrapper.get('[data-testid="ws-delete"]').attributes('disabled')).toBeDefined()
    await menus[1].trigger('click')
    expect(wrapper.get('[data-testid="ws-delete"]').attributes('disabled')).toBeUndefined() // empty Personal Lab
    await wrapper.get('[data-testid="ws-toggle"]').trigger('click')
    await flushPromises()
    expect(m.updateWorkspace).toHaveBeenCalledWith('ws_lab', { status: 'disabled' })
  })
})

describe('워크스페이스 상세 (시안 12)', () => {
  it('lists members with role/state filters and project dots', async () => {
    m.getWorkspace.mockResolvedValue(detail())
    const { wrapper } = await mountAt('/settings/workspaces/ws_default')
    expect(m.getWorkspace).toHaveBeenCalledWith('ws_default')
    expect(wrapper.get('[data-testid="member-total"]').text()).toBe('3')
    expect(wrapper.findAll('[data-member]')).toHaveLength(3)
    expect(wrapper.get('[data-member="u1"]').findAll('.pdots i.on')).toHaveLength(2)
    await wrapper.get('[data-role-filter="admin"]').trigger('click')
    expect(wrapper.findAll('[data-member]').map((r) => r.attributes('data-member'))).toEqual(['u2'])
    await wrapper.get('[data-role-filter="all"]').trigger('click')
    await wrapper.get('[data-testid="invited-filter"]').trigger('click')
    expect(wrapper.findAll('[data-member]').map((r) => r.attributes('data-member'))).toEqual(['u3'])
    expect(wrapper.get('[data-testid="member-foot"]').text()).toBe('1 / 3')
  })

  it('adds an existing user and changes a member role; the owner is locked', async () => {
    m.getWorkspace.mockResolvedValue(detail())
    m.listMemberCandidates.mockResolvedValue([{ user_id: 'u9', username: 'Oh Dohyun', email: 'd@x', state: 'active' }])
    m.addMember.mockResolvedValue(detail())
    m.changeMemberRole.mockResolvedValue(detail())
    const { wrapper } = await mountAt('/settings/workspaces/ws_default')

    await wrapper.get('[data-testid="member-add"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-candidate="u9"]').trigger('click')
    await wrapper.get('[data-add-role="admin"]').trigger('click')
    await wrapper.get('[data-testid="member-add-confirm"]').trigger('click')
    await flushPromises()
    expect(m.addMember).toHaveBeenCalledWith('ws_default', 'u9', 'admin')

    await wrapper.get('[data-member="u1"]').trigger('click')
    expect(wrapper.get('[data-testid="member-remove"]').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-member="u2"]').trigger('click')
    await wrapper.get('[data-member-role="member"]').trigger('click')
    await flushPromises()
    expect(m.changeMemberRole).toHaveBeenCalledWith('ws_default', 'u2', 'member')
  })

  it('saves role default access and per-person exceptions at once', async () => {
    m.getWorkspace.mockResolvedValue(detail())
    m.setDefaultAccess.mockResolvedValue(detail())
    m.setProjectAccess.mockResolvedValue(detail())
    const { wrapper } = await mountAt('/settings/workspaces/ws_default?tab=projects')
    expect(wrapper.find('[data-panel="projects"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="exception-count"]').text()).toBe('1')
    await wrapper.get('[data-default-role="member"] [data-access="review"]').trigger('click')
    await flushPromises()
    expect(m.setDefaultAccess).toHaveBeenCalledWith('ws_default', { member: 'review' })

    const row = wrapper.get('[data-exception-member="u3"]')
    expect(row.get('[data-access="review"]').classes()).toContain('on')
    await row.get('[data-access="default"]').trigger('click')
    await flushPromises()
    expect(m.setProjectAccess).toHaveBeenCalledWith('ws_default', 'flowgate', 'u3', 'default')

    await wrapper.get('[data-project="mirage"]').trigger('click')
    await wrapper.get('[data-exception-member="u2"] [data-access="none"]').trigger('click')
    await flushPromises()
    expect(m.setProjectAccess).toHaveBeenLastCalledWith('ws_default', 'mirage', 'u2', 'none')
  })

  it('switches the AI policy, revokes an allowed AI, adds one and sets who may create projects', async () => {
    m.getWorkspace.mockResolvedValue(detail())
    m.revokeResource.mockResolvedValue(detail())
    m.setWorkspacePolicy.mockResolvedValue(detail())
    m.listPolicyResources.mockResolvedValue({
      providers: [
        { resource_type: 'provider', resource_id: 'aip_a', name: 'OpenAI · GPT', enabled: true },
        { resource_type: 'provider', resource_id: 'aip_b', name: 'Claude', enabled: true },
      ],
      agents: [],
    })
    m.allowResource.mockResolvedValue(detail())
    const { wrapper } = await mountAt('/settings/workspaces/ws_default?tab=policy')

    const aiRow = wrapper.get('[data-allow-kind="provider"]')
    expect(aiRow.text()).toContain('OpenAI · GPT')
    await aiRow.get('.allowchip button').trigger('click')
    await flushPromises()
    expect(m.revokeResource).toHaveBeenCalledWith('ws_default', 'provider', 'aip_a')

    await wrapper.get('[data-add-kind="provider"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-resource="aip_a"]').exists()).toBe(false) // already allowed
    await wrapper.get('[data-resource="aip_b"]').trigger('click')
    await flushPromises()
    expect(m.allowResource).toHaveBeenCalledWith('ws_default', 'provider', 'aip_b')

    await wrapper.get('[data-ai-mode="all"]').trigger('click')
    await wrapper.get('[data-project-create="all"]').trigger('click')
    await flushPromises()
    expect(m.setWorkspacePolicy).toHaveBeenCalledWith('ws_default', { ai_mode: 'all' })
    expect(m.setWorkspacePolicy).toHaveBeenCalledWith('ws_default', { project_create: 'all' })
  })

  it('shows a plain member everything read-only', async () => {
    m.getWorkspace.mockResolvedValue(detail({ can_manage: false, can_admin: false, my_role: 'member' }))
    const { wrapper } = await mountAt('/settings/workspaces/ws_default?tab=projects')
    expect(wrapper.find('[data-testid="member-add"]').exists()).toBe(false)
    expect(wrapper.get('[data-default-role="member"] [data-access="review"]').attributes('disabled')).toBeDefined()
  })
})

describe('시스템 › 사용자 (시안 13)', () => {
  it('summarises and filters users by the tiles', async () => {
    m.listSystemUsers.mockResolvedValue(users())
    const { wrapper } = await mountAt('/settings/system/users')
    expect(wrapper.get('[data-testid="users-total"]').text()).toBe('3')
    await wrapper.get('[data-testid="tile-2fa"]').trigger('click')
    expect(wrapper.findAll('[data-user]').map((r) => r.attributes('data-user'))).toEqual(['u2', 'u3'])
    await wrapper.get('[data-testid="tile-nows"]').trigger('click')
    expect(wrapper.findAll('[data-user]').map((r) => r.attributes('data-user'))).toEqual(['u3'])
  })

  it('opens the 2FA-missing filter from the 보안 link (#2fa=none)', async () => {
    m.listSystemUsers.mockResolvedValue(users())
    const { wrapper } = await mountAt('/settings/system/users#2fa=none')
    expect(wrapper.get('[data-testid="chip-2fa"]').classes()).toContain('on')
    expect(wrapper.findAll('[data-user]')).toHaveLength(2)
  })

  it('locks the last administrator and disables a user after confirming', async () => {
    m.listSystemUsers.mockResolvedValue(users())
    m.disableSystemUser.mockResolvedValue({ ...users().users[1], state: 'disabled' })
    const { wrapper } = await mountAt('/settings/system/users')
    await wrapper.get('[data-user="u1"]').trigger('click')
    expect(wrapper.get('[data-testid="drawer-admin"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="drawer-disable"]').attributes('disabled')).toBeDefined()

    await wrapper.get('[data-user="u2"]').trigger('click')
    await wrapper.get('[data-testid="drawer-disable"]').trigger('click')
    await wrapper.get('[data-testid="confirm-ok"]').trigger('click')
    await flushPromises()
    expect(m.disableSystemUser).toHaveBeenCalledWith('u2')
  })

  it('creates an invited user and shows the invitation link to hand over', async () => {
    m.listSystemUsers.mockResolvedValue(users())
    m.createSystemUser.mockResolvedValue({ user: users().users[2], invite_path: '/login?invite=tok123' })
    const { wrapper } = await mountAt('/settings/system/users')
    await wrapper.get('[data-testid="user-create"]').trigger('click')
    await wrapper.get('[data-testid="new-user-name"]').setValue('Han Seojun')
    await wrapper.get('[data-testid="new-user-email"]').setValue('han@example.com')
    await wrapper.get('[data-testid="new-user-ws"]').setValue('ws_default')
    await wrapper.get('[data-testid="user-create-confirm"]').trigger('click')
    await flushPromises()
    expect(m.createSystemUser).toHaveBeenCalledWith({
      name: 'Han Seojun', email: 'han@example.com', start: 'invite', is_admin: false, workspace_id: 'ws_default',
    })
    expect(wrapper.get('[data-testid="credential-value"]').text()).toContain('/login?invite=tok123')

    const oldClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')
    const oldExec = Object.getOwnPropertyDescriptor(document, 'execCommand')
    try {
      Object.defineProperty(navigator, 'clipboard', { configurable: true, value: undefined })
      Object.defineProperty(document, 'execCommand', { configurable: true, value: vi.fn(() => {
        expect(wrapper.get('.ui-modal-surface').element.contains(wrapper.get('textarea[aria-hidden="true"]').element)).toBe(true)
        return true
      }) })
      await wrapper.get('.fgs-secret + button').trigger('click')
      await flushPromises()
      expect(document.execCommand).toHaveBeenCalledWith('copy')
      expect(wrapper.text()).toContain('복사했습니다')
    } finally {
      if (oldClipboard) Object.defineProperty(navigator, 'clipboard', oldClipboard)
      else Reflect.deleteProperty(navigator, 'clipboard')
      if (oldExec) Object.defineProperty(document, 'execCommand', oldExec)
      else Reflect.deleteProperty(document, 'execCommand')
    }
  })

  it('shows the temporary password after a password reset', async () => {
    m.listSystemUsers.mockResolvedValue(users())
    m.resetPassword.mockResolvedValue({ user: users().users[1], temp_password: 'Tmp-Pass-123' })
    const { wrapper } = await mountAt('/settings/system/users')
    await wrapper.get('[data-user="u2"]').trigger('click')
    await wrapper.get('[data-testid="drawer-password"]').trigger('click')
    await wrapper.get('[data-testid="confirm-ok"]').trigger('click')
    await flushPromises()
    expect(m.resetPassword).toHaveBeenCalledWith('u2')
    expect(wrapper.get('[data-testid="credential-value"]').text()).toBe('Tmp-Pass-123')
  })
})

describe('시스템 › 보안 (시안 14)', () => {
  it('shows who the 2FA scope covers and saves each choice at once', async () => {
    m.getSecurity.mockResolvedValue(security())
    m.saveSecurity.mockResolvedValue(security({ tfa_scope: 'all' }))
    const { wrapper } = await mountAt('/settings/system/security')
    expect(wrapper.get('[data-testid="tfa-missing"]').text()).toBe('1') // Choi Yuna (admin, not enrolled)
    expect(wrapper.get('[data-person="u2"]').classes()).toContain('out')
    expect(wrapper.get('[data-testid="tfa-users-link"]').attributes('href')).toBe('/app/settings/system/users#2fa=none')

    await wrapper.get('[data-tfa-scope="all"]').trigger('click')
    await flushPromises()
    expect(m.saveSecurity).toHaveBeenCalledWith({ tfa_scope: 'all' })
    expect(wrapper.get('[data-person="u2"]').classes()).not.toContain('out')
    expect(wrapper.get('[data-testid="tfa-missing"]').text()).toBe('2')
  })

  it('hides the grace period when 2FA is off and saves login/session rules', async () => {
    m.getSecurity.mockResolvedValue(security({ tfa_scope: 'off' }))
    m.saveSecurity.mockImplementation(async (patch) => security({ tfa_scope: 'off', ...patch }))
    const { wrapper } = await mountAt('/settings/system/security')
    expect(wrapper.find('[data-testid="grace-row"]').exists()).toBe(false)
    for (const [selector, event] of [
      ['[data-pwlen="12"]', 'click'], ['[data-lock-attempts="3"]', 'click'], ['[data-lock-minutes="60"]', 'click'],
      ['[data-idle="30m"]', 'click'], ['[data-testid="tighten"]', 'change'],
    ] as const) {
      await wrapper.get(selector).trigger(event)
      await flushPromises()
    }
    expect(m.saveSecurity.mock.calls.map((c) => c[0])).toEqual([
      { password_min_length: 12 }, { lock_attempts: 3 }, { lock_minutes: 60 }, { idle_timeout: '30m' }, { logout_on_tighten: true },
    ])
  })

  it('puts the previous value back when saving fails', async () => {
    m.getSecurity.mockResolvedValue(security())
    m.saveSecurity.mockRejectedValue(new api.SettingsRequestError('저장하지 못했습니다.', null))
    const { wrapper } = await mountAt('/settings/system/security')
    await wrapper.get('[data-pwlen="8"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-pwlen="10"]').classes()).toContain('on')
    expect(wrapper.get('[data-testid="autosave"]').classes()).toContain('error')
  })
})
