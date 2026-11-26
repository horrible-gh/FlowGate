import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { G, P, createFakeGitSource, row, status } from './fakeGitSource'
import { grantProject, resetCapabilities, grantSystemAdmin, grantSystemNothing } from '../../helpers/capabilities'

// The Git panel follows the server's `project.git.write` for its project: a "no" turns the write actions off (the base
// commit / revert); the merge and review actions are under `project.git.merge` (gitMergeRefused.0090.spec.ts), so a
// view-only role is refused both. "Allowed" or "no answer yet" leaves the panel as it was.

vi.mock('@shared/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@shared/api')>()),
  getRequest: () => new Promise(() => undefined),
}))

registerAppMessages(i18n)
let wrapper: VueWrapper | null = null
beforeEach(() => { i18n.global.locale.value = 'ko'; resetCapabilities() })
afterEach(() => { wrapper?.unmount(); wrapper = null; document.body.innerHTML = '' })

const FLOW = { groupId: G, name: '인증 오류 조사' }
async function mountPanel(props: Record<string, unknown> = {}) {
  const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'resolved_pending_review' })], baseDirty: { files: ['a.ts'], mergeInProgress: false, aiRun: false } }) })
  wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: FLOW, source: fake.source, ...props }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return fake
}
const button = (id: string) => document.body.querySelector<HTMLButtonElement>(`[data-test="${id}"]`)!
const WRITE_ACTIONS = ['git-dirty-commit', 'git-dirty-revert']
const MERGE_ACTIONS = ['git-review-approve', 'git-review-reject', 'git-review-abort']
const ACTIONS = [...MERGE_ACTIONS, ...WRITE_ACTIONS]

describe('Git panel and project.git.write', () => {
  it('refused (view-only role): every action is off', async () => {
    grantProject(P, 'role_viewer', {
      'project.git.write': { reason: 'role_limit', minimum_role: 'role_developer' },
      'project.git.merge': { reason: 'role_limit', minimum_role: 'role_reviewer' },
    })
    await mountPanel()
    for (const id of ACTIONS) expect(button(id).disabled, id).toBe(true)
  })

  it('write refused alone (a reviewer): the write actions are off, the merge and review ones stay on', async () => {
    grantProject(P, 'role_reviewer', { 'project.git.write': { reason: 'role_limit', minimum_role: 'role_developer' } })
    await mountPanel()
    for (const id of WRITE_ACTIONS) expect(button(id).disabled, id).toBe(true)
    for (const id of MERGE_ACTIONS) expect(button(id).disabled, id).toBe(false)
  })

  it('allowed: the actions are on as before', async () => {
    grantProject(P, 'role_developer')
    await mountPanel()
    for (const id of ['git-review-approve', 'git-dirty-commit']) expect(button(id).disabled, id).toBe(false)
  })

  it('no answer yet: nothing is turned off (the server still checks every Git call)', async () => {
    await mountPanel()
    expect(button('git-dirty-commit').disabled).toBe(false)
  })

  it('another project\'s refusal does not turn this panel off', async () => {
    grantProject('some-other-project', 'role_viewer', { 'project.git.write': { reason: 'role_limit' } })
    await mountPanel()
    expect(button('git-dirty-commit').disabled).toBe(false)
  })
})

describe('the "정리 실패" list (administrators) follows the server\'s system.settings.manage', () => {
  async function mountWith(allowed: boolean | null, props: Record<string, unknown> = {}) {
    if (allowed === true) grantSystemAdmin()
    if (allowed === false) grantSystemNothing()
    const fetchAbortFailures = vi.fn(async () => [])
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) }, { fetchAbortFailures })
    wrapper = mount(DesignerGitPanel, { props: { projectId: P, flow: FLOW, source: fake.source, ...props }, attachTo: document.body, global: { plugins: [i18n] } })
    await flushPromises()
    return fetchAbortFailures
  }

  it('the person the server lets manage system settings reads the list', async () => {
    expect(await mountWith(true)).toHaveBeenCalled()
  })
  it('anyone else (or no answer yet) never makes the request', async () => {
    expect(await mountWith(false)).not.toHaveBeenCalled()
    wrapper?.unmount(); resetCapabilities()
    expect(await mountWith(null)).not.toHaveBeenCalled()
  })
  it('an explicit isAdmin prop still wins over the capabilities (the panel\'s own override)', async () => {
    expect(await mountWith(false, { isAdmin: true })).toHaveBeenCalled()
  })
})
