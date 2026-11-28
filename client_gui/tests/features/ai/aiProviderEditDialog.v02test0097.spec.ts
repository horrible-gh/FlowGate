import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import * as settingsApi from '@/features/settings/api'
import type { SystemAgent } from '@/features/settings/types'
import type { ProviderItem } from '@/features/ai/types'
import { fakeCompose, mockCliCatalog } from './cliCatalogMock'

registerAppMessages(i18n)

// flowgate.v02test.0097 F97-4: the AI 연결 수정 window — name and command composed by the server from
// 종류/버전/모델/추론도, 사용자 정의 for anything else. v02test.0108 F108-4 redesign: 모델 구성 card,
// a one-line command preview with 직접 편집, and 사용 여부 in the title row.

const wrappers: VueWrapper[] = []
let mocks: ReturnType<typeof mockCliCatalog>

const CLAUDE = 'claude --model claude-opus-5-5 --effort high --dangerously-skip-permissions -p -'
const CODEX_61 = 'codex --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json --model gpt-6.1-sol -c model_reasoning_effort=high -'
const GEMINI_45 = 'agy --output-format stream-json --dangerously-skip-permissions --model gemini-3.8-flash-medium --print-timeout 45m'

const $ = <T extends Element = HTMLElement>(selector: string) => document.querySelector(selector) as T | null
const select = (id: string) => $<HTMLSelectElement>(`#${id}`)!
const options = (id: string) => Array.from(($<HTMLDataListElement>(`#${id}-suggestions`) ?? select(id)).options).filter((o) => !o.hidden).map((o) => ({ value: o.value, label: o.textContent?.trim(), disabled: o.disabled }))
const field = (id: string) => select(id).closest('.form-group') as HTMLElement
const save = () => $<HTMLButtonElement>('.dialog-actions .ui-btn--primary')!
const nameInput = () => $<HTMLInputElement>('#provider-name')!
const command = () => $<HTMLTextAreaElement>('#provider-command')
// F108-4: the CLI 실행 커맨드 switch became the 직접 편집 button next to the command preview.
const commandSwitch = () => $<HTMLButtonElement>('.command-edit-btn')!
const preview = () => $('.command-preview')
const link = (text: string) => Array.from(document.querySelectorAll<HTMLButtonElement>('.link-btn')).find((b) => b.textContent?.trim() === text)

async function choose(id: string, value: string) {
  select(id).value = value
  const input = select(id).tagName === 'INPUT'
  select(id).dispatchEvent(new Event(input ? 'input' : 'change'))
  if (input) await new Promise((resolve) => setTimeout(resolve, 350))
  await flushPromises()
}
async function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function open(provider: ProviderItem | null, props: Record<string, unknown> = {}) {
  const w = mount(AiProviderEditDialog, { props: { open: true, provider, ...props }, global: { plugins: [i18n] }, attachTo: document.body })
  wrappers.push(w)
  await flushPromises()
  return w
}
const saved = (w: VueWrapper) => {
  const calls = (w.emitted('save') ?? []) as [ProviderItem][]
  return calls[calls.length - 1]?.[0]
}

beforeEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'ko'
  mocks = mockCliCatalog()
})
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
  document.body.innerHTML = ''
})

describe('layout (F108-4)', () => {
  it('orders the body 이름 → 실행 방식 → 모델 구성 → 커맨드 and groups the four model fields in one card', async () => {
    await open(null)
    const group = $('.model-group')!
    expect(group.querySelector('.group-title')!.textContent?.trim()).toBe('모델 구성')
    for (const id of ['provider-kind', 'provider-version', 'provider-model-name', 'provider-effort']) {
      expect(group.contains(select(id)), id).toBe(true)
    }
    // 종류 on its own line, 버전 · 모델 · 추론도 together on the next one.
    expect(field('provider-kind').closest('.model-row')).toBeNull()
    const row = field('provider-version').closest('.model-row')!
    expect(['provider-version', 'provider-model-name', 'provider-effort'].every((id) => row.contains(select(id)))).toBe(true)
    const order = [nameInput(), select('provider-exec'), group, preview()!]
    for (let i = 1; i < order.length; i++) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING, String(i)).toBeTruthy()
    }
    expect(document.querySelectorAll('.switch-label, .switch-cell')).toHaveLength(0)
  })

  it('puts 사용 여부 in the title row with a 사용 / 사용 안 함 label and saves it', async () => {
    const w = await open(null)
    const toggle = $<HTMLButtonElement>('.dialog-header .ui-toggle')!
    // The switch is now the first focusable, but opening still starts on the name.
    expect(document.activeElement).toBe(nameInput())
    expect(toggle.getAttribute('aria-label')).toBe('사용 여부')
    expect($('.header-switch-state')!.textContent?.trim()).toBe('사용')
    toggle.click()
    await flushPromises()
    expect($('.header-switch-state')!.textContent?.trim()).toBe('사용 안 함')
    expect(document.querySelectorAll('.dialog-form .ui-toggle')).toHaveLength(0)
    save().click()
    expect(saved(w)!.enabled).toBe(false)
  })

  it('gives 실행 방식 the full row for local CLI and shares it with the target Agent for remote CLI', async () => {
    await open(null)
    expect(field('provider-exec').classList.contains('span-all')).toBe(true)
    await choose('provider-exec', 'remote_cli')
    expect(field('provider-exec').classList.contains('span-all')).toBe(false)
  })

  it('lists the execution modes by name only, CLI first, API greyed out', async () => {
    await open(null)
    expect(options('provider-exec')).toEqual([
      { value: 'local_cli', label: '로컬 CLI', disabled: false },
      { value: 'remote_cli', label: '원격 CLI', disabled: false },
      { value: 'local_api', label: '로컬 API', disabled: true },
      { value: 'remote_api', label: '원격 API', disabled: true },
    ])
  })

  it('hides the agent cell for local CLI and shows it for remote CLI', async () => {
    await open(null)
    expect(field('provider-agent').classList.contains('ghost')).toBe(true)
    await choose('provider-exec', 'remote_cli')
    expect(field('provider-agent').classList.contains('ghost')).toBe(false)
  })

  it('takes kinds and their lists from the server catalog', async () => {
    await open(null)
    expect(mocks.catalog).toHaveBeenCalled()
    expect(options('provider-kind').map((o) => o.label)).toEqual(['Claude', 'Codex', 'Gemini', '사용자 정의'])
    await choose('provider-kind', 'codex')
    expect(options('provider-version').map((o) => o.value)).toEqual(['6.2', '6.1'])
    expect(options('provider-model-name').map((o) => o.label)).toEqual(['Luna', 'Terra', 'Sol', 'Astra'])
    expect(options('provider-effort').map((o) => o.value)).toEqual(['low', 'medium', 'high', 'xhigh'])
  })

  it('has no description text, placeholder or auto/manual mark next to the name', async () => {
    await open(null)
    expect(document.querySelectorAll('.dialog-form [placeholder]:not([placeholder=""])')).toHaveLength(0)
    expect(nameInput().closest('.form-group')!.querySelector('.command-chip')).toBeNull()
    expect(document.querySelector('.dialog-form small')).toBeNull()
  })
})

describe('composition', () => {
  it('debounces new version/model input for 300ms and saves the latest command', async () => {
    const w = await open(null)
    vi.useFakeTimers()
    try {
      mocks.compose.mockClear()
      await type($<HTMLInputElement>('#provider-version')!, '5.6')
      await vi.advanceTimersByTimeAsync(299)
      expect(mocks.compose).not.toHaveBeenCalled()
      expect(save().disabled).toBe(true)
      await type($<HTMLInputElement>('#provider-model-name')!, 'nova')
      await vi.advanceTimersByTimeAsync(299)
      expect(mocks.compose).not.toHaveBeenCalled()
      await vi.advanceTimersByTimeAsync(1)
      await flushPromises()
      expect(mocks.compose).toHaveBeenCalledTimes(1)
      expect(mocks.compose).toHaveBeenCalledWith(expect.objectContaining({ version: '5.6', model: 'nova' }))
      save().click()
      expect(saved(w)!.cli_command).toContain('claude-nova-5-6')
    } finally {
      vi.useRealTimers()
    }
  })

  it('opens a new AI with the composed command on the preview line, which stays while 직접 편집 is open', async () => {
    await open(null)
    expect(nameInput().value).toBe('Claude Opus 5.5')
    expect(command()).toBeNull()
    expect(preview()!.textContent?.trim()).toBe(CLAUDE)
    expect(preview()!.getAttribute('title')).toBe(CLAUDE)
    expect(commandSwitch().textContent?.trim()).toBe('직접 편집')
    expect(commandSwitch().getAttribute('aria-expanded')).toBe('false')
    commandSwitch().click()
    await flushPromises()
    expect(command()!.value).toBe(CLAUDE)
    expect($<HTMLInputElement>('#provider-executable')!.value).toBe('claude')
    // The preview line is always shown; the editor opens below it.
    expect(preview()!.textContent?.trim()).toBe(CLAUDE)
    expect(preview()!.compareDocumentPosition(command()!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(commandSwitch().textContent?.trim()).toBe('편집 닫기')
    expect(commandSwitch().getAttribute('aria-expanded')).toBe('true')
    await type(command()!, `${CLAUDE} --verbose`)
    expect(preview()!.textContent?.trim()).toBe(`${CLAUDE} --verbose`)
    expect(preview()!.getAttribute('title')).toBe(`${CLAUDE} --verbose`)
    commandSwitch().click()
    await flushPromises()
    expect(command()).toBeNull()
    expect(preview()!.textContent?.trim()).toBe(`${CLAUDE} --verbose`)
  })

  it('recomposes name and command after typed selection settles', async () => {
    await open(null)
    commandSwitch().click()
    await choose('provider-kind', 'codex')
    await choose('provider-version', '6.1')
    expect(nameInput().value).toBe('Codex GPT-6.1 Sol')
    expect(command()!.value).toBe(CODEX_61)
    await choose('provider-kind', 'gemini')
    expect(nameInput().value).toBe('Gemini 3.8 Flash')
    expect(command()!.value).toContain('agy --output-format stream-json')
  })

  it('stops only the name when the name is edited, and "자동 이름으로" brings it back', async () => {
    await open(null)
    commandSwitch().click()
    expect(link('자동 이름으로')).toBeUndefined()
    await type(nameInput(), 'Claude Opus 5.5 (AG)')
    await choose('provider-model-name', 'sonnet')
    expect(nameInput().value).toBe('Claude Opus 5.5 (AG)')
    expect(command()!.value).toContain('claude-sonnet-5-5')
    link('자동 이름으로')!.click()
    await flushPromises()
    expect(nameInput().value).toBe('Claude Sonnet 5.5')
    expect(link('자동 이름으로')).toBeUndefined()
  })

  it('stops only the command when the command is edited', async () => {
    await open(null)
    commandSwitch().click()
    await flushPromises()
    await type(command()!, `${CLAUDE} --verbose`)
    expect($('.command-chip')!.textContent?.trim()).toBe('직접 편집')
    await choose('provider-model-name', 'haiku')
    expect(nameInput().value).toBe('Claude Haiku 5.5')
    expect(command()!.value).toBe(`${CLAUDE} --verbose`)
    link('조합값으로 되돌리기')!.click()
    await flushPromises()
    expect(command()!.value).toContain('claude-haiku-5-5')
    expect($('.command-chip')!.textContent?.trim()).toBe('조합값')
  })

  it('puts the executable at the front of the command and refuses one with a shell operator', async () => {
    await open(null)
    commandSwitch().click()
    await flushPromises()
    await type($<HTMLInputElement>('#provider-executable')!, '/home/agent/tools/claude-linux/claude-account1')
    await new Promise((resolve) => setTimeout(resolve, 350))
    await flushPromises()
    expect(command()!.value.startsWith('/home/agent/tools/claude-linux/claude-account1 --model')).toBe(true)
    await type($<HTMLInputElement>('#provider-executable')!, 'claude|cat')
    await new Promise((resolve) => setTimeout(resolve, 350))
    await flushPromises()
    expect(document.body.textContent).toContain('실행 파일은 공백 없는 경로 하나로 입력하세요.')
    expect(save().disabled).toBe(true)
  })

  it('without 직접 편집 there is only the preview line, which follows the selection', async () => {
    await open(null)
    expect(command()).toBeNull()
    expect($('#provider-executable')).toBeNull()
    await choose('provider-model-name', 'haiku')
    expect(preview()!.textContent).toContain('claude-haiku-5-5')
  })

  it('사용자 정의 drops version/model/effort and shows the command editor under the preview line', async () => {
    await open(null)
    await choose('provider-kind', 'custom')
    expect($('.model-row')).toBeNull()
    expect($('#provider-version')).toBeNull()
    expect($('#provider-model-name')).toBeNull()
    expect($('#provider-effort')).toBeNull()
    expect($('#provider-executable')).toBeNull()
    expect(command()).not.toBeNull()
    // The preview line stays even though the editor is always open here.
    expect(preview()!.textContent?.trim()).toBe((command()!.value || '—').trim())
    await type(command()!, 'my-cli --flag')
    expect(preview()!.textContent?.trim()).toBe('my-cli --flag')
    expect($('.command-edit-btn')).toBeNull()
    expect($('.command-chip')!.textContent?.trim()).toBe('직접 입력')
  })
})

describe('opening a saved connection', () => {
  const saved0 = (over: Partial<ProviderItem>): ProviderItem => ({
    id: 'p1', name: 'Codex GPT-6.1 Sol', exec_type: 'cli', exec_location: 'local', kind: 'codex', enabled: true, cli_command: CODEX_61, ...over,
  })

  it('opens a composed command as its combo values and keeps it unchanged on save', async () => {
    const w = await open(saved0({}))
    expect(mocks.parse).toHaveBeenCalledWith(CODEX_61, 'codex')
    expect(select('provider-kind').value).toBe('codex')
    expect(select('provider-version').value).toBe('6.1')
    expect(select('provider-model-name').value).toBe('sol')
    expect(select('provider-effort').value).toBe('high')
    expect(command()).toBeNull()
    expect(preview()!.textContent?.trim()).toBe(CODEX_61)
    save().click()
    expect(saved(w)).toMatchObject({ kind: 'codex', name: 'Codex GPT-6.1 Sol', cli_command: CODEX_61 })
  })

  it('keeps a name with something added and offers the auto name', async () => {
    const w = await open(saved0({ name: 'Codex GPT-6.1 Sol (AG)' }))
    expect(nameInput().value).toBe('Codex GPT-6.1 Sol (AG)')
    expect(link('자동 이름으로')).toBeDefined()
    await choose('provider-effort', 'xhigh')
    expect(nameInput().value).toBe('Codex GPT-6.1 Sol (AG)')
    save().click()
    expect(saved(w)!.cli_command).toContain('model_reasoning_effort=xhigh')
  })

  it.each([
    ['copilot', 'copilot', 'copilot --allow-all-tools --model gpt-6.1 -p -'],
    ['custom', 'custom', 'my-cli --flag'],
    ['hand-edited claude', 'claude', `${CLAUDE} --verbose`],
  ])('opens %s as 사용자 정의 and saves kind and command as they were', async (_label, kind, cli) => {
    const w = await open(saved0({ name: 'Mine', kind, cli_command: cli }))
    expect(select('provider-kind').value).toBe('custom')
    expect(command()!.value).toBe(cli)
    expect(preview()!.textContent?.trim()).toBe(cli)
    expect($('.command-edit-btn')).toBeNull()
    save().click()
    expect(saved(w)).toMatchObject({ kind, name: 'Mine', cli_command: cli })
  })

  it('carries a saved Gemini timeout into a recomposed command', async () => {
    const w = await open(saved0({ name: 'Gemini 3.8 Flash', kind: 'gemini', cli_command: GEMINI_45 }))
    expect(select('provider-kind').value).toBe('gemini')
    await choose('provider-model-name', 'pro')
    save().click()
    expect(saved(w)).toMatchObject({ name: 'Gemini 3.8 Pro', cli_command: fakeCompose({ kind: 'gemini', model: 'pro', timeout_seconds: 2700 }).cli_command })
    expect(saved(w)!.cli_command).toContain('--print-timeout 45m')
  })

  it('falls back to 사용자 정의 when the catalog cannot be loaded, and still saves the command as it was', async () => {
    mocks.catalog.mockRejectedValue(new Error('down'))
    const w = await open(saved0({}))
    expect(select('provider-kind').value).toBe('custom')
    expect(document.body.textContent).toContain('모델 목록을 불러오지 못했습니다.')
    save().click()
    expect(saved(w)).toMatchObject({ kind: 'codex', cli_command: CODEX_61 })
  })
})

describe('target Agent combo', () => {
  const agent = (id: string, online: boolean): SystemAgent => ({
    agent_id: id, name: id, enabled: true, location: 'remote', registration_status: 'registered', credential_status: 'active',
    enrollment_status: 'none', protocol_compatibility: 'compatible', online_status: online ? 'online' : 'offline',
    effective_capabilities: { ai_cli: true, ai_api: false, storage: false },
  } as SystemAgent)

  it('shows Agent names with the state as a dot and offline ones cannot be picked', async () => {
    vi.spyOn(settingsApi, 'listPersonalAgents').mockResolvedValue([agent('agent-249', true), agent('my-agent', false)])
    await open(null)
    await choose('provider-exec', 'remote_cli')
    expect(options('provider-agent').filter((o) => o.value)).toEqual([
      { value: 'agent-249', label: '● agent-249', disabled: false },
      { value: 'my-agent', label: '○ my-agent', disabled: true },
    ])
  })
})

describe('i18n', () => {
  const added = ['field_executable', 'field_command', 'field_version', 'field_model', 'field_effort', 'name_auto_reset',
    'command_reset', 'command_auto', 'command_edited', 'command_custom', 'catalog_load_failed', 'compose_failed', 'executable_invalid',
    'group_model', 'command_preview', 'command_edit', 'command_edit_close']
  const removed = ['gui.ai.providers.placeholder_name', 'gui.ai.providers.placeholder_cli_command', 'gui.ai.providers.agent_loading',
    'gui.ai.providers.agent_reason_disabled', 'gui.ai.providers.agent_reason_revoked', 'gui.ai.providers.agent_reason_no_cli',
    'gui.ai.providers.placeholder_api_key', 'gui.ai.providers.placeholder_keep_key', 'gui.ai.providers.api_key_needed_hint',
    'gui.ai.modes.local_hint', 'gui.ai.modes.remote_hint', 'gui.ai.modes.unsupported']
  it.each(['ko', 'en', 'ja'])('has the new %s texts and no longer the unused ones', (locale) => {
    for (const key of added) expect(i18n.global.te(`gui.ai.providers.${key}`, locale), `${locale} ${key}`).toBe(true)
    for (const key of removed) expect(i18n.global.te(key, locale), `${locale} ${key}`).toBe(false)
  })
})
