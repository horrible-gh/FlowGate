import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import { validateProvider } from '@/features/ai/providers/providerValidation'
import * as transport from '@shared/api'
import * as aiApi from '@/features/ai/api'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import { setCurrentUser } from '@/core/auth/currentUser'
import * as settingsApi from '@/features/settings/api'
import type { SystemAgent } from '@/features/settings/types'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const wrappers: VueWrapper[] = []
const agent = (id: string, over: Partial<SystemAgent> = {}): SystemAgent => ({
  agent_id: id, name: `Agent ${id}`, enabled: true, location: 'remote', connection_mode: 'agent_pull',
  local_executable_path: null, configured_capabilities: { ai_cli: true, ai_api: false, storage: false },
  reported_capabilities: { ai_cli: true, ai_api: null, storage: null },
  effective_capabilities: { ai_cli: true, ai_api: false, storage: false },
  registration_status: 'registered', credential_status: 'active', has_registration_history: true,
  enrollment_status: 'none', enrollment_expires_at: null, online_status: 'online', last_seen_at: null,
  agent_version: null, protocol_version: 1, protocol_compatibility: 'compatible',
  last_rejected_protocol_version: null, last_protocol_rejection_at: null, os: null, architecture: null,
  ...over,
} as SystemAgent)
const remote = (target: string | null = null): ProviderItem => ({
  id: 'r1', name: 'R', exec_type: 'cli', exec_location: 'remote', kind: 'custom', enabled: true,
  cli_command: 'fake-cli', target_agent_id: target,
})
const local: ProviderItem = { id: 'l1', name: 'L', exec_type: 'cli', exec_location: 'local', kind: 'custom', enabled: true, cli_command: 'fake-cli' }

async function open(provider: ProviderItem | null, agents: SystemAgent[]) {
  vi.spyOn(settingsApi, 'listPersonalAgents').mockResolvedValue(agents)
  vi.spyOn(settingsApi, 'listSystemAgents').mockResolvedValue(agents)
  const w = mount(AiProviderEditDialog, { props: { open: true, provider }, global: { plugins: [i18n] }, attachTo: document.body })
  wrappers.push(w)
  await flushPromises()
  return w
}
const cells = () => Array.from(document.querySelectorAll<HTMLButtonElement>('.agent-cell'))
const save = () => document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement

beforeEach(() => { vi.restoreAllMocks(); i18n.global.locale.value = 'ko' })
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); document.body.innerHTML = '' })

describe('remote CLI target Agent field (0129 T#3)', () => {
  it('shows the field only for remote CLI', async () => {
    const w = await open(local, [agent('a')])
    expect(document.querySelector('.agent-list')).toBeNull()
    await w.setProps({ provider: remote('a') })
    await flushPromises()
    expect(cells()).toHaveLength(1)
  })

  it('keeps the saved Agent selected and sends target_agent_id on save', async () => {
    const w = await open(remote('b'), [agent('a'), agent('b')])
    expect(cells().map((c) => c.getAttribute('aria-checked'))).toEqual(['false', 'true'])
    save().click()
    await flushPromises()
    expect(w.emitted('save')![0][0]).toMatchObject({ exec_location: 'remote', target_agent_id: 'b' })
  })

  it('does not send the value for local CLI', async () => {
    const w = await open({ ...local, target_agent_id: 'x' }, [agent('a')])
    save().click()
    await flushPromises()
    expect(w.emitted('save')![0][0]).not.toHaveProperty('target_agent_id')
  })

  it('shows name, online state and why an Agent cannot be chosen', async () => {
    await open(remote(null), [
      agent('a'),
      agent('off', { enabled: false }),
      agent('rev', { registration_status: 'revoked', credential_status: 'revoked' }),
      agent('nocli', { effective_capabilities: { ai_cli: false, ai_api: false, storage: false } }),
    ])
    const [ok, disabled, revoked, nocli] = cells()
    expect(ok.textContent).toContain('Agent a')
    expect(ok.textContent).toContain('온라인')
    expect([ok, disabled, revoked, nocli].map((c) => c.disabled)).toEqual([false, true, true, true])
    expect(disabled.textContent).toContain('사용 중지')
    expect(revoked.textContent).toContain('등록 해지')
    expect(nocli.textContent).toContain('CLI 불가')
  })

  it('selects nothing automatically with one Agent and blocks save until one is picked', async () => {
    await open(remote(null), [agent('a')])
    expect(cells()[0].getAttribute('aria-checked')).toBe('false')
    expect(save().disabled).toBe(true)
    cells()[0].click()
    await flushPromises()
    expect(save().disabled).toBe(false)
  })

  it('shows one line for zero Agents and for a saved Agent that is gone', async () => {
    await open(remote(null), [])
    expect(document.body.textContent).toContain('사용할 수 있는 에이전트가 없습니다.')
    document.body.innerHTML = ''
    await open(remote('gone'), [agent('a')])
    expect(document.body.textContent).toContain('저장된 에이전트를 찾을 수 없습니다.')
    expect(cells()[0].getAttribute('aria-checked')).toBe('false')
    expect(save().disabled).toBe(true)
  })

  it('uses system Agents for a system AI and my Agents for my AI', async () => {
    vi.spyOn(settingsApi, 'listPersonalAgents').mockResolvedValue([agent('me')])
    const system = vi.spyOn(settingsApi, 'listSystemAgents').mockResolvedValue([agent('sys')])
    const w = mount(AiProviderEditDialog, { props: { open: true, provider: remote('sys'), owner: 'system' }, global: { plugins: [i18n] }, attachTo: document.body })
    wrappers.push(w)
    await flushPromises()
    expect(system).toHaveBeenCalled()
    expect(cells().map((c) => c.textContent)).toEqual([expect.stringContaining('Agent sys')])
  })

  it('requires the Agent for remote CLI only', () => {
    const base = { name: 'n', kind: 'custom', exec_type: 'cli' as const, cli_command: 'x' }
    expect(validateProvider({ ...base, exec_location: 'remote' })).toEqual([{ field: 'target_agent_id', reason: 'required_for_remote_cli' }])
    expect(validateProvider({ ...base, exec_location: 'remote', target_agent_id: 'a' })).toEqual([])
    expect(validateProvider({ ...base, exec_location: 'local' })).toEqual([])
  })

  it.each(['ko', 'en', 'ja'])('has the %s texts', (locale) => {
    i18n.global.locale.value = locale as 'ko'
    for (const key of [
      'gui.ai.providers.field_target_agent', 'gui.ai.providers.agent_loading', 'gui.ai.providers.agent_load_failed',
      'gui.ai.providers.agent_none', 'gui.ai.providers.agent_missing', 'gui.ai.providers.agent_reason_disabled',
      'gui.ai.providers.agent_reason_revoked', 'gui.ai.providers.agent_reason_no_cli', 'settings.ai.field.target_agent_id',
      'settings.ai.reason.required_for_remote_cli', 'settings.ai.reason.invalid_target_agent', 'settings.ai.reason.target_agent_unavailable',
    ]) expect(i18n.global.te(key, locale), `${locale} ${key}`).toBe(true)
  })
})

function deferred<T>() {
  let resolve!: (v: T) => void
  let reject!: (e: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}
const modeCell = (label: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('.mode-cell')).find((b) => b.textContent!.includes(label))!
const ownerBtn = (label: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('.segmented button')).find((b) => b.textContent!.includes(label))!

async function openNewRemote() {
  const w = mount(AiProviderEditDialog, {
    props: { open: true, provider: null, owner: 'system', ownerChoices: ['system', 'user'] },
    global: { plugins: [i18n] }, attachTo: document.body,
  })
  wrappers.push(w)
  await flushPromises()
  modeCell(i18n.global.t('gui.ai.modes.remote_cli')).click()
  const name = document.querySelector('#provider-name') as HTMLInputElement
  name.value = 'N'; name.dispatchEvent(new Event('input'))
  const cmd = document.querySelector('#provider-command') as HTMLInputElement
  cmd.value = 'fake-cli'; cmd.dispatchEvent(new Event('input'))
  await flushPromises()
  return w
}

describe('owner switch on a new remote CLI (0129 rework)', () => {
  it('clears the pick and list at once and blocks save until the new owner list arrives', async () => {
    const mine = deferred<SystemAgent[]>()
    vi.spyOn(settingsApi, 'listSystemAgents').mockResolvedValue([agent('sys')])
    vi.spyOn(settingsApi, 'listPersonalAgents').mockReturnValue(mine.promise)
    const w = await openNewRemote()
    cells()[0].click()
    await flushPromises()
    expect(save().disabled).toBe(false)
    ownerBtn(i18n.global.t('gui.ai.owned.owner_user')).click()
    await flushPromises()
    expect(cells()).toHaveLength(0)
    expect(save().disabled).toBe(true)
    save().click()
    expect(w.emitted('save')).toBeUndefined()
    mine.resolve([agent('me')])
    await flushPromises()
    expect(cells().map((c) => c.textContent)).toEqual([expect.stringContaining('Agent me')])
    expect(cells()[0].getAttribute('aria-checked')).toBe('false')
    expect(save().disabled).toBe(true)
    cells()[0].click()
    await flushPromises()
    save().click()
    expect(w.emitted('save')![0][0]).toMatchObject({ target_agent_id: 'me' })
    expect(w.emitted('save')![0][1]).toBe('user')
  })

  it('keeps nothing selected and blocks save when the new owner list fails', async () => {
    vi.spyOn(settingsApi, 'listSystemAgents').mockResolvedValue([agent('sys')])
    vi.spyOn(settingsApi, 'listPersonalAgents').mockRejectedValue(new Error('down'))
    const w = await openNewRemote()
    cells()[0].click()
    await flushPromises()
    ownerBtn(i18n.global.t('gui.ai.owned.owner_user')).click()
    await flushPromises()
    expect(cells()).toHaveLength(0)
    expect(document.body.textContent).toContain(i18n.global.t('gui.ai.providers.agent_load_failed'))
    expect(save().disabled).toBe(true)
    save().click()
    expect(w.emitted('save')).toBeUndefined()
  })

  it('ignores a late response of the previous owner', async () => {
    const sys = deferred<SystemAgent[]>()
    vi.spyOn(settingsApi, 'listSystemAgents').mockReturnValue(sys.promise)
    vi.spyOn(settingsApi, 'listPersonalAgents').mockResolvedValue([agent('me')])
    await openNewRemote()
    ownerBtn(i18n.global.t('gui.ai.owned.owner_user')).click()
    await flushPromises()
    sys.resolve([agent('sys')])
    await flushPromises()
    expect(cells().map((c) => c.textContent)).toEqual([expect.stringContaining('Agent me')])
  })
})

describe('final save request goes through panel and API (0129 rework)', () => {
  const remoteItem: ProviderItem = {
    id: 'r1', name: 'R', exec_type: 'cli', exec_location: 'remote', kind: 'custom', enabled: true,
    cli_command: 'fake-cli', target_agent_id: 'a',
  }
  async function panelWith(item: ProviderItem) {
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    // Stateful fake server: a re-read returns what the last PUT stored.
    let stored: ProviderItem[] = [item]
    vi.spyOn(aiApi, 'fetchSystemAi').mockImplementation(async () => ({ providers: stored, default_provider_id: item.id, updated_at: null }))
    vi.spyOn(aiApi, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
    vi.spyOn(settingsApi, 'listSystemAgents').mockResolvedValue([agent('a'), agent('b')])
    vi.spyOn(settingsApi, 'listPersonalAgents').mockResolvedValue([])
    const put = vi.spyOn(transport, 'putRequest').mockImplementation(async (_url: string, body: any) => {
      stored = body.providers
      return { data: { providers: stored, default_provider_id: item.id, updated_at: null } } as never
    })
    const w = mount(AiProvidersPanel, { global: { plugins: [i18n] }, attachTo: document.body })
    wrappers.push(w)
    await flushPromises()
    return { w, put }
  }
  const editBtn = () => document.querySelector(`button[aria-label="${i18n.global.t('gui.common.edit')}"]`) as HTMLButtonElement
  afterEach(() => setCurrentUser(null))

  it('does not put target_agent_id when a remote CLI is switched to local CLI', async () => {
    const { put } = await panelWith(remoteItem)
    editBtn().click()
    await flushPromises()
    modeCell(i18n.global.t('gui.ai.modes.local_cli')).click()
    await flushPromises()
    save().click()
    await flushPromises()
    expect(put).toHaveBeenCalledTimes(1)
    const sent = (put.mock.calls[0][1] as { providers: ProviderItem[] }).providers[0]
    expect(sent).toMatchObject({ id: 'r1', exec_location: 'local' })
    expect(sent).not.toHaveProperty('target_agent_id')
  })

  it('keeps the Agent in the request and after re-entry for a saved remote CLI', async () => {
    const { put } = await panelWith(remoteItem)
    editBtn().click()
    await flushPromises()
    cells()[1].click()
    await flushPromises()
    save().click()
    await flushPromises()
    expect((put.mock.calls[0][1] as { providers: ProviderItem[] }).providers[0].target_agent_id).toBe('b')
    editBtn().click()
    await flushPromises()
    expect(cells().map((c) => c.getAttribute('aria-checked'))).toEqual(['false', 'true'])
  })

  it('strips the field for every non-remote-CLI item in the wire list', () => {
    const wire = aiApi.toWireProviders([
      { ...remoteItem, exec_location: 'local' }, { ...remoteItem, exec_type: 'api' }, remoteItem,
    ])
    expect(wire.map((p) => 'target_agent_id' in p)).toEqual([false, false, true])
  })
})
