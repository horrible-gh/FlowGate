import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { reactive } from 'vue'
import { createFirstRunAiSetup } from '@/features/ai/roles/firstRunSetup'
import AiRoleSetupModeless from '@/features/ai/roles/AiRoleSetupModeless.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import { account, deferred, notUsed } from '../ai/rolesFixtures.0113'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
afterEach(() => { wrapper?.unmount(); wrapper = null })
// The Teleport stub re-renders the setup's selects on every value change, so a list found once goes stale (its later
// items are detached and setValue never reaches the component). Find the select again before each choice.
async function chooseAll(w: VueWrapper<any>, value = 'not_used') {
  const count = w.findAll('select').length
  for (let i = 0; i < count; i++) await w.findAll('select')[i]!.setValue(value)
}
// flowgate.v02.0113 T#2 rework: [저장] is one server request that saves the account and applies it to the Flow's cards.
const applied = (completed = true) => ({ flowId: 'f', requestId: 'req', replayed: false, cardKeys: ['gn_1'], account: account(completed) })
function harness() {
  let context: string | null = 'user/p/f'
  let ids = 0
  const get = vi.fn().mockResolvedValue(account()), apply = vi.fn().mockResolvedValue(applied())
  const setup = createFirstRunAiSetup({ context: () => context, get, apply, newRequestId: () => `req-${++ids}` })
  const prepare = vi.fn().mockResolvedValue(true), start = vi.fn().mockResolvedValue({ runId: 'r', status: 'idle', openNodeKeys: [] })
  const runs = reactive<Record<string, FlowRunState>>({})
  const control = createFlowRunControl({ source: { ...fixtureFlowGraphSource, loadRun: vi.fn().mockResolvedValue(null), startRun: start, stopRun: start, resumeRun: start }, projectId: () => 'p', isOpen: () => true, beforePrepare: (flowId) => setup.request(flowId), canProceed: () => setup.current(), prepare, refresh: () => undefined }, runs)
  return { setup, get, apply, control, runs, prepare, start, context: (value: string | null) => { context = value; setup.cancel() } }
}
describe('D §8 first modeless and run lifecycle', () => {
  it.each(['next','all'] as const)('%s: saves then resumes exactly once; next click does not reopen', async mode => {
    const h = harness(), result = h.control.run('f', mode); await flushPromises()
    expect(h.setup.state.data).not.toBeNull(); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
    expect(await h.control.run('f', mode)).toBe(false)
    h.get.mockResolvedValue(account(true))
    await Promise.all([h.setup.save(notUsed), h.setup.save(notUsed)])
    expect(await result).toBe(true); expect(h.apply).toHaveBeenCalledTimes(1); expect(h.start).toHaveBeenCalledTimes(1)
    expect(h.start.mock.calls[0][2]).toBe(mode === 'next' ? 'step' : 'full')
    expect(await h.control.run('f', mode)).toBe(true); expect(h.setup.state.data).toBeNull(); expect(h.apply).toHaveBeenCalledTimes(1)
  })
  it('applies to the Flow of the waiting command with a request id and needs no second account read', async () => {
    const h = harness(), result = h.control.run('f', 'all'); await flushPromises()
    expect(h.get).toHaveBeenCalledTimes(1)
    await h.setup.save(notUsed)
    expect(h.apply).toHaveBeenCalledWith('f', 'req-1', notUsed)
    expect(await result).toBe(true); expect(h.get).toHaveBeenCalledTimes(1); expect(h.start).toHaveBeenCalledTimes(1)
  })
  it('reads the account again only when the answer could not carry it', async () => {
    const h = harness(), result = h.control.run('f', 'next'); await flushPromises()
    h.apply.mockResolvedValue({ ...applied(), account: null }); h.get.mockResolvedValue(account(true))
    await h.setup.save(notUsed)
    expect(h.get).toHaveBeenCalledTimes(2); expect(await result).toBe(true); expect(h.start).toHaveBeenCalledTimes(1)
  })
  it.each([[true,0],[true,3],[false,0]] as const)('completed=%s count=%s bypasses using only these flags', async (complete, count) => {
    const h = harness(); h.get.mockResolvedValue({ ...account(complete,count), options: [], roles: account().roles })
    expect(await h.control.run('f','all')).toBe(true); expect(h.setup.state.data).toBeNull(); expect(h.apply).not.toHaveBeenCalled()
  })
  it('opens when server count>0 even if filtered options are empty', async () => {
    const h = harness(); h.get.mockResolvedValue({ ...account(false,1), options: [] })
    const result = h.control.run('f','next'); await flushPromises(); expect(h.setup.state.data).not.toBeNull()
    h.setup.cancel(); expect(await result).toBe(false); expect(h.apply).not.toHaveBeenCalled(); expect(h.prepare).not.toHaveBeenCalled()
  })
  it('a read failure never prepares or runs and keeps the run failure', async () => {
    const h = harness(); h.get.mockRejectedValue(new Error('read failed'))
    expect(await h.control.run('f','all')).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled(); expect(h.runs.f.error?.message).toBe('read failed')
  })
  it('incomplete values do not save; save failure keeps the modeless and never runs', async () => {
    const h = harness(), result = h.control.run('f','next'); await flushPromises()
    await h.setup.save({ ...notUsed, review: null } as any); expect(h.apply).not.toHaveBeenCalled()
    h.apply.mockRejectedValue(new Error('save failed')); await h.setup.save(notUsed)
    expect(h.setup.state.error).toBe('save failed'); expect(h.start).not.toHaveBeenCalled(); h.setup.cancel(); expect(await result).toBe(false)
  })
  it('a refusal (a card running) keeps the modeless with its error; a retry of the same values reuses the request id', async () => {
    const h = harness(), result = h.control.run('f','all'); await flushPromises()
    h.apply.mockRejectedValueOnce(new Error('cards running: T'))
    await h.setup.save(notUsed)
    expect(h.setup.state.error).toBe('cards running: T'); expect(h.setup.state.data).not.toBeNull()
    expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
    await h.setup.save(notUsed)
    expect(h.apply.mock.calls.map(call => call[1])).toEqual(['req-1', 'req-1'])
    expect(await result).toBe(true); expect(h.start).toHaveBeenCalledTimes(1)
  })
  it('other values after a failure are a new request', async () => {
    const h = harness(), result = h.control.run('f','next'); await flushPromises()
    h.apply.mockRejectedValueOnce(new Error('failed'))
    await h.setup.save(notUsed)
    await h.setup.save({ ...notUsed, execution: { kind: 'assigned', providerId: 'a' } })
    expect(h.apply.mock.calls.map(call => call[1])).toEqual(['req-1', 'req-2'])
    expect(await result).toBe(true)
  })
  it.each(['flow switch','account switch','logout','unmount'])('%s discards a waiting command and its late GET', async () => {
    const h = harness(), query = deferred<ReturnType<typeof account>>(); h.get.mockReturnValue(query.promise)
    const result = h.control.run('f','all'); h.context(null); query.resolve(account()); expect(await result).toBe(false)
    expect(h.setup.state.data).toBeNull(); expect(h.prepare).not.toHaveBeenCalled()
  })
  it('a late successful save after a flow change cannot execute the old command', async () => {
    const h = harness(), saved = deferred<ReturnType<typeof applied>>(), result = h.control.run('f','all'); await flushPromises()
    h.apply.mockReturnValue(saved.promise); const save = h.setup.save(notUsed); h.context('user/p/other'); saved.resolve(applied()); await save
    expect(await result).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
  })
  it('cancel saves nothing, changes nothing and asks again on the next click', async () => {
    const h = harness(), first = h.control.run('f','next'); await flushPromises()
    h.setup.cancel(); expect(await first).toBe(false)
    expect(h.apply).not.toHaveBeenCalled(); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
    const second = h.control.run('f','next'); await flushPromises()
    expect(h.setup.state.data).not.toBeNull(); h.setup.cancel(); expect(await second).toBe(false)
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
    h.runs.f.run = { runId: 'r', status: 'paused', openNodeKeys: [] }; await h.setup.save(notUsed)
    expect(await result).toBe(false); expect(h.prepare).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled()
  })
  it('renders only three selects and footer actions without an overlay, toggle or explanation', async () => {
    wrapper = mount(AiRoleSetupModeless, { props: { data: account(), saving: false, error: null }, global: { plugins: [i18n], stubs: { Teleport: true } } })
    expect(wrapper.get('[role="dialog"]').attributes('aria-modal')).toBe('false')
    expect(wrapper.findAll('select')).toHaveLength(3); expect(wrapper.find('p').exists()).toBe(false); expect(wrapper.find('[role="switch"]').exists()).toBe(false); expect(wrapper.find('.ui-modal-overlay').exists()).toBe(false)
    expect(wrapper.findAll('button').at(-1)!.attributes('disabled')).toBeDefined()
    await chooseAll(wrapper)
    await wrapper.findAll('button').at(-1)!.trigger('click'); expect(wrapper.emitted('save')).toEqual([[notUsed]])
    await wrapper.get('[data-modeless-action="close"]').trigger('click'); expect(wrapper.emitted('close')).toHaveLength(1)
  })
  // flowgate.v02.0117 T#3: the setup window is a ModelessShell (the frame of the run result window), not its own fixed panel.
  const mountSetup = (data: ReturnType<typeof account> | null = account()) => mount(AiRoleSetupModeless, { props: { data, saving: false, error: null }, global: { plugins: [i18n], stubs: { Teleport: true } } })
  it('is framed by ModelessShell: open follows data, title, shell close, no own header or fixed panel', async () => {
    wrapper = mountSetup()
    const shell = wrapper.findComponent(ModelessShell)
    expect(shell.exists()).toBe(true); expect(shell.props('open')).toBe(true); expect(shell.props('title')).toBe(String(i18n.global.t('gui.aiRoles.setup')))
    expect(wrapper.find('.ui-modeless-panel').exists()).toBe(true); expect(wrapper.find('.ui-modeless-titlebar').text()).toContain(String(i18n.global.t('gui.aiRoles.setup')))
    expect(wrapper.find('header').exists()).toBe(false)
    expect(wrapper.get('.ui-modeless-body [data-test="ai-role-setup"]').exists()).toBe(true)
    expect(wrapper.find('[data-modeless-action="minimize"]').exists()).toBe(true)
    await wrapper.setProps({ data: null }); expect(wrapper.find('.ui-modeless-panel').exists()).toBe(false); expect(wrapper.find('[data-test="ai-role-setup"]').exists()).toBe(false)
  })
  it('closes by Esc, the shell x and [cancel]; each emits close without save', async () => {
    wrapper = mountSetup()
    await wrapper.get('.ui-modeless-panel').trigger('keydown', { key: 'Escape' }); expect(wrapper.emitted('close')).toHaveLength(1)
    await wrapper.get('[data-modeless-action="close"]').trigger('click'); expect(wrapper.emitted('close')).toHaveLength(2)
    const cancel = wrapper.findAll('footer button').at(-2)!; await cancel.trigger('click'); expect(wrapper.emitted('close')).toHaveLength(3)
    expect(wrapper.emitted('save')).toBeUndefined()
  })
  it('save emits the three role values once and does not close; saving disables it', async () => {
    wrapper = mountSetup()
    await chooseAll(wrapper)
    await wrapper.findAll('footer button').at(-1)!.trigger('click')
    expect(wrapper.emitted('save')).toEqual([[notUsed]]); expect(wrapper.emitted('close')).toBeUndefined()
    await wrapper.setProps({ saving: true }); expect(wrapper.findAll('footer button').at(-1)!.attributes('disabled')).toBeDefined()
  })
})
