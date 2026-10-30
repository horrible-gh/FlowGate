import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { reactive } from 'vue'
import { createFirstRunAiSetup } from '@/features/ai/roles/firstRunSetup'
import AiRoleSetupModeless from '@/features/ai/roles/AiRoleSetupModeless.vue'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import { account, deferred, notUsed } from '../ai/rolesFixtures.0113'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null })
function harness() {
  let context: string | null = 'user/p/f'
  const get = vi.fn().mockResolvedValue(account()), put = vi.fn().mockResolvedValue(account(true))
  const setup = createFirstRunAiSetup({ context: () => context, get, put })
  const prepare = vi.fn().mockResolvedValue(true), start = vi.fn().mockResolvedValue({ runId: 'r', status: 'idle', openNodeKeys: [] })
  const runs = reactive<Record<string, FlowRunState>>({})
  const control = createFlowRunControl({ source: { ...fixtureFlowGraphSource, loadRun: vi.fn().mockResolvedValue(null), startRun: start, stopRun: start, resumeRun: start }, projectId: () => 'p', isOpen: () => true, beforePrepare: () => setup.request(), canProceed: () => setup.current(), prepare, refresh: () => undefined }, runs)
  return { setup, get, put, control, runs, prepare, start, context: (value: string | null) => { context = value; setup.cancel() } }
}
describe('D §8 first modeless and run lifecycle', () => {
  it.each(['next','all'] as const)('%s: saves then resumes exactly once; next click does not reopen', async mode => {
    const h = harness(), result = h.control.run('f', mode); await flushPromises()
    expect(h.setup.state.data).not.toBeNull(); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
    expect(await h.control.run('f', mode)).toBe(false)
    h.get.mockResolvedValue(account(true))
    await Promise.all([h.setup.save(notUsed), h.setup.save(notUsed)])
    expect(await result).toBe(true); expect(h.put).toHaveBeenCalledTimes(1); expect(h.start).toHaveBeenCalledTimes(1)
    expect(h.start.mock.calls[0][2]).toBe(mode === 'next' ? 'step' : 'full')
    expect(await h.control.run('f', mode)).toBe(true); expect(h.setup.state.data).toBeNull(); expect(h.put).toHaveBeenCalledTimes(1)
  })
  it.each([[true,0],[true,3],[false,0]] as const)('completed=%s count=%s bypasses using only these flags', async (complete, count) => {
    const h = harness(); h.get.mockResolvedValue({ ...account(complete,count), options: [], roles: account().roles })
    expect(await h.control.run('f','all')).toBe(true); expect(h.setup.state.data).toBeNull(); expect(h.put).not.toHaveBeenCalled()
  })
  it('opens when server count>0 even if filtered options are empty', async () => {
    const h = harness(); h.get.mockResolvedValue({ ...account(false,1), options: [] })
    const result = h.control.run('f','next'); await flushPromises(); expect(h.setup.state.data).not.toBeNull()
    h.setup.cancel(); expect(await result).toBe(false); expect(h.put).not.toHaveBeenCalled(); expect(h.prepare).not.toHaveBeenCalled()
  })
  it('a read failure never prepares or runs and keeps the run failure', async () => {
    const h = harness(); h.get.mockRejectedValue(new Error('read failed'))
    expect(await h.control.run('f','all')).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled(); expect(h.runs.f.error?.message).toBe('read failed')
  })
  it('incomplete values do not save; save failure keeps the modeless and never runs', async () => {
    const h = harness(), result = h.control.run('f','next'); await flushPromises()
    await h.setup.save({ ...notUsed, review: null } as any); expect(h.put).not.toHaveBeenCalled()
    h.put.mockRejectedValue(new Error('save failed')); await h.setup.save(notUsed)
    expect(h.setup.state.error).toBe('save failed'); expect(h.start).not.toHaveBeenCalled(); h.setup.cancel(); expect(await result).toBe(false)
  })
  it.each(['flow switch','account switch','logout','unmount'])('%s discards a waiting command and its late GET', async () => {
    const h = harness(), query = deferred<ReturnType<typeof account>>(); h.get.mockReturnValue(query.promise)
    const result = h.control.run('f','all'); h.context(null); query.resolve(account()); expect(await result).toBe(false)
    expect(h.setup.state.data).toBeNull(); expect(h.prepare).not.toHaveBeenCalled()
  })
  it('a late successful PUT after a flow change cannot execute the old command', async () => {
    const h = harness(), saved = deferred<ReturnType<typeof account>>(), result = h.control.run('f','all'); await flushPromises()
    h.put.mockReturnValue(saved.promise); const save = h.setup.save(notUsed); h.context('user/p/other'); saved.resolve(account(true)); await save
    expect(await result).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
  })
  it('locked runs skip GET; prepare refusal after setup skips start', async () => {
    const h = harness(); h.runs.f = { run: { runId: 'r', status: 'running', openNodeKeys: [] }, readState: 'read', action: null, approving: null, holding: null, error: null }
    expect(await h.control.run('f','next')).toBe(false); expect(h.get).not.toHaveBeenCalled()
    h.runs.f.run = null; h.get.mockResolvedValue(account(true)); h.prepare.mockResolvedValue(false)
    expect(await h.control.run('f','next')).toBe(false); expect(h.start).not.toHaveBeenCalled()
  })
  it('a flow/account change after setup but during prepare invalidates start', async () => {
    const h = harness(), prepared = deferred<boolean>(); h.get.mockResolvedValue(account(true)); h.prepare.mockReturnValue(prepared.promise)
    const result = h.control.run('f','all'); await flushPromises(); expect(h.prepare).toHaveBeenCalledTimes(1)
    h.context('other/p/f'); prepared.resolve(true); expect(await result).toBe(false); expect(h.start).not.toHaveBeenCalled()
  })
  it('rechecks a lock acquired while the setup is open', async () => {
    const h = harness(), result = h.control.run('f','all'); await flushPromises()
    h.runs.f.run = { runId: 'r', status: 'paused', openNodeKeys: [] }; h.get.mockResolvedValue(account(true)); await h.setup.save(notUsed)
    expect(await result).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
  })
  it('renders only three selects and footer actions without an overlay, toggle or explanation', async () => {
    wrapper = mount(AiRoleSetupModeless, { props: { data: account(), saving: false, error: null }, global: { plugins: [i18n], stubs: { Teleport: true } } })
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('false')
    expect(wrapper.findAll('select')).toHaveLength(3); expect(wrapper.find('p').exists()).toBe(false); expect(wrapper.find('[role="switch"]').exists()).toBe(false); expect(wrapper.find('.ui-modal-overlay').exists()).toBe(false)
    const save = wrapper.findAll('button').at(-1)!; expect(save.attributes('disabled')).toBeDefined()
    for (const select of wrapper.findAll('select')) await select.setValue('not_used')
    await save.trigger('click'); expect(wrapper.emitted('save')).toEqual([[notUsed]])
    await wrapper.get('header button').trigger('click'); expect(wrapper.emitted('close')).toHaveLength(1)
  })
})
