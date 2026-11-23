/** flowgate.v02test.0078 · 0015-T B (시스템 설정 › 실행 자원 › 웹 터미널): the menu entry right under 음성 입력, the state
 *  (설치 안 됨 / 설치 중 / 설치됨 / 오류), [설치] with progress · stages · [취소], the error with [다시 설치] and the offline
 *  line, [셸 시험] · [삭제], and the [사용] switch (dimmed "설치 후 사용" before installing; turning off with open terminals
 *  asks once). The server card runs at the same time, so the contract API (공통 약속 2~7) is mocked. */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed } from 'vue'

const net = vi.hoisted(() => ({
  system: vi.fn(),
  start: vi.fn(),
  read: vi.fn(),
  cancel: vi.fn(),
  removeBundle: vi.fn(),
  setEnabled: vi.fn(),
  test: vi.fn(),
}))
vi.mock('@/features/designer/flowTerminal/api', async (original) => ({
  ...(await original<typeof import('@/features/designer/flowTerminal/api')>()),
  readTerminalSystem: net.system,
  startTerminalInstall: net.start,
  readTerminalInstall: net.read,
  cancelTerminalInstall: net.cancel,
  deleteTerminalBundle: net.removeBundle,
  setTerminalEnabled: net.setEnabled,
  testTerminalShell: net.test,
}))
vi.mock('@/features/settings/components/systemNav', () => ({ useSystemNav: () => computed(() => []) }))

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { TerminalInstall, TerminalSystem } from '@/features/designer/flowTerminal/api'
import SystemTerminalSettingsView from '@/features/settings/views/SystemTerminalSettingsView.vue'
import { SETTINGS_PAGES } from '@/features/settings/settingsMenu'
import { installPercent, stageIndex, terminalSystemState } from '@/features/settings/terminal/systemTerminalModel'

registerAppMessages(i18n)

const BUNDLE = { name: 'pywinpty', version: '2.0.15', size_bytes: 1_234_567 }
const sys = (over: Partial<TerminalSystem> = {}): TerminalSystem => ({
  platform: 'windows', bundle_required: true, installed: false, enabled: false, open_sessions: 0, bundle: BUNDLE, install: null, ...over,
})
const job = (over: Partial<TerminalInstall> = {}): TerminalInstall => ({
  install_id: 'i1', status: 'running', stage: 'download', progress: 0.25, bytes_done: 300_000, bytes_total: 1_234_567,
  error_code: null, message: null, ...over,
})
const conflict = (code: string) => Object.assign(new Error(code), { isAxiosError: true, response: { status: 409, data: { detail: { code } } } })

let wrappers: VueWrapper[] = []
function open() {
  const w = mount(SystemTerminalSettingsView, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { SettingsShell: { template: '<div><slot name="nav" /><slot /></div>' }, SettingsSubnav: true, SettingsIcon: true },
    },
  })
  wrappers.push(w)
  return w
}
const q = (test: string) => document.querySelector<HTMLElement>(`[data-testid="${test}"]`)
const switchInput = () => q('terminal-switch') as HTMLInputElement

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  for (const fn of Object.values(net)) fn.mockReset()
})
afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('menu', () => {
  it('lists 웹 터미널 right under 음성 입력, admin only, in the same 실행 자원 group', () => {
    const system = SETTINGS_PAGES.filter(page => page.scope === 'system')
    const voice = system.findIndex(page => page.key === 'voice')
    const terminal = system[voice + 1]
    expect(terminal).toMatchObject({ key: 'terminal', adminOnly: true, routeName: 'settings-system-terminal', path: '/settings/system/terminal' })
    expect(terminal.groupKey).toBe(system[voice].groupKey)
    expect(i18n.global.t(terminal.labelKey)).toBe('웹 터미널')
  })
})

describe('model', () => {
  it('names the state and the stage', () => {
    expect(terminalSystemState(sys())).toBe('not_installed')
    expect(terminalSystemState(sys({ install: job() }))).toBe('installing')
    expect(terminalSystemState(sys({ install: job({ status: 'queued', stage: null }) }))).toBe('installing')
    expect(terminalSystemState(sys({ installed: true, install: job({ status: 'done' }) }))).toBe('installed')
    expect(terminalSystemState(sys({ install: job({ status: 'failed' }) }))).toBe('error')
    expect(terminalSystemState(sys({ install: job({ status: 'cancelled' }) }))).toBe('not_installed')
    expect(terminalSystemState(sys({ platform: 'linux', bundle_required: false, installed: true, bundle: null }))).toBe('installed')
    expect(stageIndex(job({ stage: 'verify' }))).toBe(1)
    expect(stageIndex(job({ stage: null }))).toBe(0)
    expect(installPercent(job({ progress: 0.426 }))).toBe(43)
    expect(installPercent(job({ progress: 7 }))).toBe(100)
  })
})

describe('시스템 › 웹 터미널', () => {
  it('not installed: the switch is dimmed with "설치 후 사용"; [설치] starts and shows progress, stages and [취소]', async () => {
    vi.useFakeTimers()
    net.system.mockResolvedValue(sys())
    net.start.mockResolvedValue(job({ status: 'queued', stage: null, progress: 0 }))
    net.read.mockResolvedValue(job({ stage: 'verify', progress: 0.5 }))
    open()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치 안 됨')
    expect(switchInput().disabled).toBe(true)
    expect(q('terminal-switch-label')!.textContent).toBe('설치 후 사용')
    expect(q('terminal-bundle')!.textContent).toContain('pywinpty 2.0.15')
    q('terminal-install')!.click()
    await flushPromises()
    expect(net.start).toHaveBeenCalledTimes(1)
    expect(q('terminal-state')!.textContent).toBe('설치 중')
    expect(q('terminal-install-progress')).not.toBeNull()
    expect(switchInput().disabled).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(net.read).toHaveBeenCalledWith('i1')
    const steps = [...q('terminal-install-steps')!.querySelectorAll('span')].map(el => el.textContent!.trim())
    expect(steps).toEqual(['✓ 받기', '확인(SHA-256)', '풀기', '시험', '적용'])
    expect(q('terminal-install-bar')!.style.width).toBe('50%')
    net.cancel.mockResolvedValue(undefined)
    net.system.mockResolvedValue(sys({ install: job({ status: 'cancelled' }) }))
    q('terminal-install-cancel')!.click()
    await flushPromises()
    expect(net.cancel).toHaveBeenCalledWith('i1')
    expect(q('terminal-state')!.textContent).toBe('설치 안 됨')
    expect(q('terminal-install-cancelled')).not.toBeNull()
  })

  it('missing dependencies after an earlier install offer repair and allow turning off', async () => {
    net.system.mockResolvedValue(sys({ installed: false, enabled: true, install: job({ status: 'done' }) }))
    net.start.mockResolvedValue(job())
    open()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치 안 됨')
    expect(switchInput().disabled).toBe(false)
    expect((q('terminal-install') as HTMLButtonElement).disabled).toBe(false)
    q('terminal-install')!.click()
    await flushPromises()
    expect(net.start).toHaveBeenCalledTimes(1)
    expect(q('terminal-state')!.textContent).toBe('설치 중')
  })

  it('an installation that ends reads the setting again: installed, switch available', async () => {
    vi.useFakeTimers()
    net.system.mockResolvedValueOnce(sys({ install: job({ stage: 'apply', progress: 0.95 }) }))
    net.read.mockResolvedValue(job({ status: 'done', stage: 'apply', progress: 1 }))
    net.system.mockResolvedValue(sys({ installed: true, install: job({ status: 'done', stage: 'apply', progress: 1 }) }))
    open()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치 중')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치됨')
    expect(switchInput().disabled).toBe(false)
    expect(q('terminal-switch-label')!.textContent).toBe('꺼짐')
  })

  it('failed offline: the message, [다시 설치] and the `setup-terminal --from-dir` line', async () => {
    net.system.mockResolvedValue(sys({ install: job({ status: 'failed', error_code: 'terminal_download_failed', message: 'connection refused' }) }))
    open()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('오류')
    expect(q('terminal-install-error')!.textContent).toContain('부품을 받지 못했습니다')
    expect(q('terminal-install-error')!.textContent).toContain('connection refused')
    expect(q('terminal-offline-hint')!.textContent).toContain('setup-terminal --from-dir')
    expect(q('terminal-install')!.textContent!.trim()).toBe('다시 설치')
  })

  it('Linux / macOS: installed from the start, nothing to download or delete', async () => {
    net.system.mockResolvedValue(sys({ platform: 'linux', bundle_required: false, installed: true, bundle: null }))
    open()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치됨')
    expect(q('terminal-no-bundle')).not.toBeNull()
    expect(q('terminal-install')).toBeNull()
    expect(q('terminal-remove')).toBeNull()
    expect(switchInput().disabled).toBe(false)
  })

  it('turning on puts enabled=true; turning off with no open terminal does not ask', async () => {
    net.system.mockResolvedValue(sys({ installed: true }))
    net.setEnabled.mockResolvedValueOnce({ enabled: true, closed_sessions: 0 }).mockResolvedValueOnce({ enabled: false, closed_sessions: 0 })
    open()
    await flushPromises()
    switchInput().checked = true
    switchInput().dispatchEvent(new Event('change'))
    await flushPromises()
    expect(net.setEnabled).toHaveBeenLastCalledWith(true)
    expect(q('terminal-switch-label')!.textContent).toBe('켜짐')
    switchInput().checked = false
    switchInput().dispatchEvent(new Event('change'))
    await flushPromises()
    expect(q('terminal-off-confirm')).toBeNull()
    expect(net.setEnabled).toHaveBeenLastCalledWith(false)
    expect(q('terminal-switch-label')!.textContent).toBe('꺼짐')
  })

  it('turning off with open terminals asks once: "열려 있는 터미널 N개가 바로 닫힙니다"', async () => {
    net.system.mockResolvedValue(sys({ installed: true, enabled: true, open_sessions: 2 }))
    net.setEnabled.mockResolvedValue({ enabled: false, closed_sessions: 2 })
    open()
    await flushPromises()
    switchInput().checked = false
    switchInput().dispatchEvent(new Event('change'))
    await flushPromises()
    expect(net.setEnabled).not.toHaveBeenCalled()
    expect(switchInput().checked).toBe(true)
    expect(q('terminal-off-confirm')!.textContent).toContain('열려 있는 터미널 2개가 바로 닫힙니다')
    q('terminal-off-confirm-yes')!.click()
    await flushPromises()
    expect(net.setEnabled).toHaveBeenCalledWith(false)
    expect(q('terminal-switch-label')!.textContent).toBe('꺼짐')
    expect(q('terminal-notice')!.textContent).toContain('2개')
  })

  it('a refused switch (terminal_not_installed) says why and shows what the server has', async () => {
    net.system.mockResolvedValue(sys({ installed: true }))
    net.setEnabled.mockRejectedValue(conflict('terminal_not_installed'))
    open()
    await flushPromises()
    switchInput().checked = true
    switchInput().dispatchEvent(new Event('change'))
    await flushPromises()
    expect(q('terminal-action-error')!.textContent).toBe('설치가 끝나야 켤 수 있습니다.')
    expect(net.system).toHaveBeenCalledTimes(2)
  })

  it('[셸 시험] shows the shell or the failure; [삭제] asks, deletes and reads again; it is unavailable while on', async () => {
    net.system.mockResolvedValue(sys({ installed: true }))
    net.test.mockResolvedValueOnce({ ok: true, shell: 'powershell.exe' })
      .mockResolvedValueOnce({ ok: false, error_code: 'terminal_spawn_failed', message: 'ConPTY is not available' })
    open()
    await flushPromises()
    q('terminal-test')!.click()
    await flushPromises()
    expect(q('terminal-test-ok')!.textContent).toContain('powershell.exe')
    q('terminal-test')!.click()
    await flushPromises()
    expect(q('terminal-test-error')!.textContent).toContain('ConPTY is not available')
    net.removeBundle.mockResolvedValue(undefined)
    q('terminal-remove')!.click()
    await flushPromises()
    expect(net.removeBundle).not.toHaveBeenCalled()
    net.system.mockResolvedValue(sys())
    q('terminal-remove-yes')!.click()
    await flushPromises()
    expect(net.removeBundle).toHaveBeenCalledTimes(1)
    expect(q('terminal-state')!.textContent).toBe('설치 안 됨')

    for (const w of wrappers) w.unmount()
    wrappers = []
    net.system.mockResolvedValue(sys({ installed: true, enabled: true }))
    open()
    await flushPromises()
    expect((q('terminal-remove') as HTMLButtonElement).disabled).toBe(true)
  })

  it('a failed read offers [다시 불러오기]', async () => {
    net.system.mockRejectedValueOnce(Object.assign(new Error('403'), { isAxiosError: true, response: { status: 403, data: {} } }))
    net.system.mockResolvedValue(sys())
    open()
    await flushPromises()
    expect(q('terminal-load-error')).not.toBeNull()
    q('terminal-retry')!.click()
    await flushPromises()
    expect(q('terminal-state')!.textContent).toBe('설치 안 됨')
  })
})
