import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

// settingsMenu.ts: a page is offered when the server's capabilities allow it — never from `is_admin`.

const net = vi.hoisted(() => ({ projects: [] as unknown[] }))
vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: vi.fn(async (path: string) => {
    if (path === '/api/v1/projects') return { data: { projects: net.projects } }
    throw new Error(`unexpected ${path}`)
  }),
}))

import { setCurrentUser } from '@/core/auth/currentUser'
import { SETTINGS_PAGES, useSettingsMenu } from '@/features/settings/settingsMenu'
import { setSystemCapabilities, parseCapabilities } from '@shared/permissions/capabilities'
import { PROJECT_ACTIONS, SYSTEM_ACTIONS, resetCapabilities, wireBlock } from '../../helpers/capabilities'

function keys(): string[] {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: SETTINGS_PAGES.map((p) => ({ path: p.path, name: p.routeName, component: { template: '<div />' } })),
  })
  let menu: ReturnType<typeof useSettingsMenu> | null = null
  mount(defineComponent({ setup() { menu = useSettingsMenu(); return () => h('div') } }), { global: { plugins: [router] } })
  return menu!.value.map((p) => `${p.scope}:${p.key}`)
}

const system = (allowed: readonly string[]) => setSystemCapabilities(parseCapabilities(wireBlock(
  SYSTEM_ACTIONS, null, Object.fromEntries(SYSTEM_ACTIONS.filter((a) => !allowed.includes(a)).map((a) => [a, { reason: 'system_admin_only' }])))))

const ME = ['me:account', 'me:theme', 'me:agents', 'me:security']

beforeEach(() => {
  resetCapabilities()
  net.projects = []
  setCurrentUser({ username: 'x', email: null, isAdmin: false })
})

describe('the settings menu follows the server\'s capabilities', () => {
  it('a person with no system action and no project settings sees 내 설정 only', async () => {
    system([])
    expect(keys()).toEqual(ME)
    await vi.waitFor(() => expect(keys()).toEqual(ME))
  })

  it('system.user.read opens 사용자 and nothing else of the system pages', () => {
    system(['system.user.read'])
    expect(keys()).toEqual([...ME, 'system:users'])
  })

  it('system.settings.read opens 일반 and 전체 프로젝트 and the project screens; Agents and Voice need system.settings.manage', () => {
    system(['system.settings.read'])
    expect(keys()).toEqual(expect.arrayContaining(['system:general', 'system:projects', 'project:project']))
    expect(keys()).not.toContain('system:agents')
    expect(keys()).not.toContain('system:voice')
    system(['system.settings.read', 'system.settings.manage'])
    expect(keys()).toEqual(expect.arrayContaining(['system:agents', 'system:voice']))
  })

  it('a project administrator without any system action gets the project screens once the project list says so', async () => {
    system([])
    net.projects = [{ project_id: 'p1', capabilities: wireBlock(PROJECT_ACTIONS, 'role_admin') }]
    keys() // starts asking
    await vi.waitFor(() => expect(keys()).toEqual([...ME, 'project:project']))
  })

  it('a project where the person may not read the settings does not open the project screens', async () => {
    system([])
    net.projects = [{ project_id: 'p1', capabilities: wireBlock(PROJECT_ACTIONS, 'role_viewer', { 'project.settings.read': { reason: 'role_limit', minimum_role: 'role_admin' } }) }]
    keys()
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(keys()).toEqual(ME)
  })

  it('before /auth/me has answered nothing that needs a permission is offered', () => {
    setCurrentUser(null)
    resetCapabilities()
    expect(keys()).toEqual(ME)
  })
})
