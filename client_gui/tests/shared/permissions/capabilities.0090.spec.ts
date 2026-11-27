import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick } from 'vue'

// flowgate.v02test.0090 card "화면 권한": the screens read ONE store of the server's capability answers.
// Only the transport is replaced; the answer shapes are rbac/capabilities.py's.

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: (path: string, params?: unknown) => api.get(path, params),
}))

import {
  anyProjectCan,
  ensureProjectCapabilityList,
  invalidateCapabilities,
  loadProjectCapabilities,
  newProjectDecision,
  onCapabilitiesInvalidated,
  parseCapabilities,
  projectCan,
  projectRefusal,
  setSystemCapabilities,
  systemCan,
  useCapabilities,
} from '@shared/permissions/capabilities'
import { reportForbidden } from '@shared/permissions/forbidden'
import { grantNewProject, grantProject, grantSystemAdmin, grantSystemNothing, resetCapabilities, wireBlock } from '../../helpers/capabilities'

beforeEach(() => {
  resetCapabilities()
  api.get.mockReset()
})
afterEach(() => resetCapabilities())

describe('parseCapabilities', () => {
  it('reads allowed / reason / minimum_role / rule and the sources', () => {
    const block = parseCapabilities({
      role: 'role_viewer',
      sources: [{ kind: 'team', role: 'role_viewer', name: 'QA' }, { kind: 'none' }],
      actions: {
        'project.view': { allowed: true },
        'project.run': { allowed: false, reason: 'role_limit', minimum_role: 'role_developer' },
        'project.document.update': { allowed: false, reason: 'context_rule', rule: 'document_editable' },
      },
    })!
    expect(block.role).toBe('role_viewer')
    expect(block.sources).toEqual([{ kind: 'team', role: 'role_viewer', teamName: 'QA' }, { kind: 'none', role: null, teamName: null }])
    expect(block.actions['project.view']).toMatchObject({ allowed: true, reason: null })
    expect(block.actions['project.run']).toMatchObject({ allowed: false, reason: 'role_limit', minimumRole: 'role_developer' })
    expect(block.actions['project.document.update']).toMatchObject({ allowed: false, reason: 'context_rule', rule: 'document_editable' })
  })

  it('anything that is not a block gives null — never a guess', () => {
    for (const bad of [null, undefined, 'x', 3, [], {}, { actions: 'x' }, { role: 'role_admin' }]) expect(parseCapabilities(bad)).toBeNull()
  })

  it('an action that says allowed:false without a reason is still refused', () => {
    expect(parseCapabilities({ actions: { 'project.run': { allowed: false } } })!.actions['project.run']).toMatchObject({ allowed: false, reason: 'role_limit' })
  })
})

describe('system block', () => {
  it('nothing is allowed until the server has answered', () => {
    expect(systemCan('system.user.read')).toBe(false)
  })
  it('reads the action the server allowed, one at a time — no role is looked at', () => {
    setSystemCapabilities(parseCapabilities(wireBlock(['system.user.read', 'system.settings.manage'], null, { 'system.settings.manage': { reason: 'system_admin_only' } })))
    expect(systemCan('system.user.read')).toBe(true)
    expect(systemCan('system.settings.manage')).toBe(false)
    expect(systemCan('system.user.create')).toBe(false)
  })
  it('grant helpers match the two kinds of person', () => {
    grantSystemAdmin()
    expect(systemCan('system.user.disable')).toBe(true)
    grantSystemNothing()
    expect(systemCan('system.user.disable')).toBe(false)
  })
})

describe('project block', () => {
  it('projectCan is false and projectRefusal is null while nothing is known — a missing answer does not turn a button off', async () => {
    api.get.mockReturnValue(new Promise(() => undefined))
    expect(projectCan('p1', 'project.run')).toBe(false)
    expect(projectRefusal('p1', 'project.run')).toBeNull()
  })

  it('a known "no" is a refusal with its reason; a known "yes" is none', () => {
    grantProject('p1', 'role_viewer', { 'project.run': { reason: 'role_limit', minimum_role: 'role_developer' } })
    expect(projectRefusal('p1', 'project.run')).toMatchObject({ allowed: false, reason: 'role_limit', minimumRole: 'role_developer' })
    expect(projectRefusal('p1', 'project.view')).toBeNull()
    expect(projectCan('p1', 'project.view')).toBe(true)
    expect(projectCan('p1', 'project.run')).toBe(false)
  })

  it('loadProjectCapabilities asks GET /api/v1/projects/{id}/capabilities once at a time', async () => {
    api.get.mockResolvedValue({ data: { project_id: 'p 1', capabilities: wireBlock(['project.run'], 'role_developer') } })
    await Promise.all([loadProjectCapabilities('p 1'), loadProjectCapabilities('p 1')])
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get.mock.calls[0]![0]).toBe('/api/v1/projects/p%201/capabilities')
    expect(projectCan('p 1', 'project.run')).toBe(true)
    await loadProjectCapabilities('p 1')
    expect(api.get).toHaveBeenCalledTimes(1)
  })

  it('a failed read is "not known", not "refused"', async () => {
    api.get.mockRejectedValue(Object.assign(new Error('x'), { response: { status: 500 } }))
    await loadProjectCapabilities('p1')
    expect(projectRefusal('p1', 'project.run')).toBeNull()
    expect(projectCan('p1', 'project.run')).toBe(false)
  })

  it('useCapabilities asks for the project it is given and follows it', async () => {
    api.get.mockResolvedValue({ data: { capabilities: wireBlock(['project.run', 'project.git.write'], 'role_reviewer', { 'project.run': { reason: 'role_limit', minimum_role: 'role_developer' } }) } })
    const scope = effectScope()
    const caps = scope.run(() => useCapabilities(() => 'p9'))!
    expect(caps.ready.value).toBe(false)
    await vi.waitFor(() => expect(caps.ready.value).toBe(true))
    expect(caps.role.value).toBe('role_reviewer')
    expect(caps.can('project.git.write')).toBe(true)
    expect(caps.refusal('project.run')).toMatchObject({ reason: 'role_limit' })
    expect(caps.refusal('project.git.write')).toBeNull()
    scope.stop()
  })
})

describe('invalidation — permissions may have changed', () => {
  it('drops every project answer so the next read asks the server again, and tells the listeners', async () => {
    const heard = vi.fn()
    const off = onCapabilitiesInvalidated(heard)
    grantProject('p1', 'role_viewer', { 'project.run': { reason: 'role_limit' } })
    expect(projectRefusal('p1', 'project.run')).not.toBeNull()
    api.get.mockResolvedValue({ data: { capabilities: wireBlock(['project.run'], 'role_developer') } })
    invalidateCapabilities()
    expect(heard).toHaveBeenCalledTimes(1)
    expect(projectRefusal('p1', 'project.run')).toBeNull() // asks again — no old answer left
    await vi.waitFor(() => expect(projectCan('p1', 'project.run')).toBe(true))
    off()
  })

  it('an answer that arrives after an invalidation belongs to an older question and is dropped', async () => {
    let release!: (value: unknown) => void
    api.get.mockReturnValueOnce(new Promise((resolve) => { release = resolve })).mockReturnValue(new Promise(() => undefined))
    const first = loadProjectCapabilities('p1')
    invalidateCapabilities()
    release({ data: { capabilities: wireBlock(['project.run'], 'role_admin') } })
    await first
    expect(projectCan('p1', 'project.run')).toBe(false)
  })

  it('a 403 from any request invalidates (once per burst)', async () => {
    const heard = vi.fn()
    const off = onCapabilitiesInvalidated(heard)
    reportForbidden('/api/v1/x', 'no', { silent: true })
    reportForbidden('/api/v1/y', 'no', { silent: true })
    reportForbidden('/api/v1/z', 'no', { silent: true })
    await nextTick()
    expect(heard).toHaveBeenCalledTimes(1)
    off()
  })
})

describe('lists with their own blocks', () => {
  it('ensureProjectCapabilityList reads GET /api/v1/projects once and fills each project', async () => {
    api.get.mockResolvedValue({ data: { projects: [
      { project_id: 'a', capabilities: wireBlock(['project.settings.read'], 'role_admin') },
      { project_id: 'b', capabilities: wireBlock(['project.settings.read'], 'role_viewer', { 'project.settings.read': { reason: 'role_limit' } }) },
    ] } })
    await Promise.all([ensureProjectCapabilityList(), ensureProjectCapabilityList()])
    expect(api.get).toHaveBeenCalledTimes(1)
    expect(api.get).toHaveBeenCalledWith('/api/v1/projects', { status: 'active' })
    expect(projectCan('a', 'project.settings.read')).toBe(true)
    expect(projectCan('b', 'project.settings.read')).toBe(false)
    expect(anyProjectCan('project.settings.read')).toBe(true)
    expect(anyProjectCan('project.settings.edit')).toBe(false)
  })

  it('newProjectDecision follows the default workspace; unknown until the list is in', async () => {
    api.get.mockReturnValue(new Promise(() => undefined))
    expect(newProjectDecision()).toMatchObject({ allowed: false, reason: 'unknown' })
    grantNewProject(true)
    expect(newProjectDecision().allowed).toBe(true)
    grantNewProject(false)
    expect(newProjectDecision()).toMatchObject({ allowed: false, reason: 'role_limit' })
  })
})
