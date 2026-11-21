import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { computed } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { VoiceService, VoiceStatus } from '@/features/conversation/voice/api'
import { resetVoiceStore } from '@/features/conversation/voice/voiceStore'
import SystemVoiceSettingsView from '@/features/settings/views/SystemVoiceSettingsView.vue'

// 시스템 › 음성 입력: state and on/off, install (with progress and cancel), make default, test, delete.
const net = vi.hoisted(() => ({
  status: vi.fn(),
  catalog: vi.fn(),
  prefs: vi.fn(),
  install: vi.fn(),
  installJob: vi.fn(),
  cancel: vi.fn(),
  remove: vi.fn(),
  setDefault: vi.fn(),
  setEnabled: vi.fn(),
  restart: vi.fn(),
}))
vi.mock('@/features/conversation/voice/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/voice/api')>()),
  fetchVoiceStatus: net.status,
  fetchVoiceCatalog: net.catalog,
  fetchVoicePrefs: net.prefs,
  startVoiceInstall: net.install,
  fetchVoiceInstall: net.installJob,
  cancelVoiceInstall: net.cancel,
  removeVoiceModel: net.remove,
  setVoiceDefault: net.setDefault,
  setVoiceEnabled: net.setEnabled,
  restartVoice: net.restart,
}))
vi.mock('@/features/settings/components/systemNav', () => ({ useSystemNav: () => computed(() => []) }))

registerAppMessages(i18n)

const model = (id: string, extra: Partial<VoiceService['models'][number]> = {}) => ({
  id, name: id, sizeBytes: 487_601_967, installed: false, realtime: false, isDefault: false, available: true, unavailableReason: null, languages: [], gpuRequired: false, ...extra,
})
const catalog = (installed: string[] = [], defaultId: string | null = null): VoiceService[] => [
  { id: 'whisper.cpp', name: 'whisper.cpp', version: '1.8.2', license: 'MIT', installed: installed.length > 0, available: true, unavailableReason: null,
    models: ['small', 'base'].map((id) => model(id, { installed: installed.includes(id), isDefault: id === defaultId })) },
]
const status = (extra: Partial<VoiceStatus> = {}): VoiceStatus => ({ enabled: false, state: 'not_installed', defaultSelection: null, error: null, canManage: true, ...extra })
const ON = status({ enabled: true, state: 'on', defaultSelection: { service: 'whisper.cpp', model: 'small' } })

let wrappers: VueWrapper[] = []
function open() {
  const w = mount(SystemVoiceSettingsView, {
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: { SettingsShell: { template: '<div><slot name="nav" /><slot /></div>' }, SettingsSubnav: true, SettingsIcon: true },
    },
  })
  wrappers.push(w)
  return w
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  resetVoiceStore()
  for (const fn of Object.values(net)) fn.mockReset()
  net.prefs.mockResolvedValue({ action: 'text', service: '', model: '', realtime: false })
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  resetVoiceStore()
  vi.useRealTimers()
})

describe('before installation', () => {
  it('shows 설치 안 됨, a switch that cannot be used yet, and 설치하고 켜기', async () => {
    net.status.mockResolvedValue(status())
    net.catalog.mockResolvedValue(catalog())
    const w = open()
    await flushPromises()
    expect(w.get('[data-testid="voice-state"]').text()).toBe('설치 안 됨')
    expect(w.get('[data-testid="voice-switch"]').attributes('disabled')).toBeDefined()
    expect(w.get('[data-testid="voice-apply-go"]').text()).toBe('설치하고 켜기')
    expect(w.get('[data-testid="voice-test-out"]').text()).toContain('설치 후 사용')
  })

  it('installs, follows the progress, and reads the state again when it ends', async () => {
    vi.useFakeTimers()
    net.status.mockResolvedValue(status())
    net.catalog.mockResolvedValue(catalog())
    net.install.mockResolvedValue('vi_1')
    net.installJob.mockResolvedValueOnce({ installId: 'vi_1', service: 'whisper.cpp', model: 'small', step: 'downloading', receivedBytes: 250_000_000, totalBytes: 500_000_000, done: false, error: null })
    const w = open()
    await flushPromises()
    await w.get('[data-testid="voice-apply-go"]').trigger('click')
    await flushPromises()
    expect(net.install).toHaveBeenCalledWith({ service: 'whisper.cpp', model: 'small' })
    expect(localStorage.getItem('flowgate.voice.install')).toBe('vi_1')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(w.get('[data-testid="voice-install-progress"]').text()).toContain('250MB / 500MB')
    expect(w.get('[data-testid="voice-state"]').text()).toBe('설치 중')

    net.installJob.mockResolvedValueOnce({ installId: 'vi_1', service: 'whisper.cpp', model: 'small', step: 'complete', receivedBytes: 500_000_000, totalBytes: 500_000_000, done: true, error: null })
    net.status.mockResolvedValue(ON)
    net.catalog.mockResolvedValue(catalog(['small'], 'small'))
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(w.find('[data-testid="voice-install-progress"]').exists()).toBe(false)
    expect(w.get('[data-testid="voice-state"]').text()).toBe('켜짐')
    expect(localStorage.getItem('flowgate.voice.install')).toBeNull()
  })

  it('shows why an installation failed', async () => {
    vi.useFakeTimers()
    net.status.mockResolvedValue(status())
    net.catalog.mockResolvedValue(catalog())
    net.install.mockResolvedValue('vi_2')
    net.installJob.mockResolvedValueOnce({ installId: 'vi_2', service: 'whisper.cpp', model: 'small', step: 'error', receivedBytes: 0, totalBytes: 0, done: true, error: { code: 'voice_sha256_mismatch', message: 'x' } })
    const w = open()
    await flushPromises()
    await w.get('[data-testid="voice-apply-go"]').trigger('click')
    await vi.advanceTimersByTimeAsync(1000)
    await flushPromises()
    expect(w.get('[data-testid="voice-install-error"]').text()).toContain('sha256')
  })
})

describe('installed', () => {
  it('turning off asks first, then switches off for everyone', async () => {
    net.status.mockResolvedValue(ON)
    net.catalog.mockResolvedValue(catalog(['small'], 'small'))
    net.setEnabled.mockResolvedValue(status({ state: 'off', defaultSelection: { service: 'whisper.cpp', model: 'small' } }))
    const w = open()
    await flushPromises()
    expect(w.get('[data-testid="voice-state"]').text()).toBe('켜짐')
    expect(w.get('[data-testid="voice-default"]').text()).toBe('whisper.cpp · small')
    await w.get('[data-testid="voice-switch"]').setValue(false)
    expect(net.setEnabled).not.toHaveBeenCalled()
    net.status.mockResolvedValue(status({ state: 'off', defaultSelection: { service: 'whisper.cpp', model: 'small' } }))
    await w.get('[data-testid="voice-off-confirm-yes"]').trigger('click')
    await flushPromises()
    expect(net.setEnabled).toHaveBeenCalledWith(false)
    expect(w.get('[data-testid="voice-state"]').text()).toBe('꺼짐')
  })

  it('makes another installed model the default', async () => {
    net.status.mockResolvedValue(ON)
    net.catalog.mockResolvedValue(catalog(['small', 'base'], 'small'))
    net.setDefault.mockResolvedValue(status({ enabled: true, state: 'on', defaultSelection: { service: 'whisper.cpp', model: 'base' } }))
    const w = open()
    await flushPromises()
    expect(w.find('[data-testid="voice-apply"]').exists()).toBe(false)
    await w.get('[data-testid="voice-model"]').setValue('base')
    expect(w.get('[data-testid="voice-apply-go"]').text()).toBe('기본으로 지정')
    await w.get('[data-testid="voice-apply-go"]').trigger('click')
    await flushPromises()
    expect(net.setDefault).toHaveBeenCalledWith({ service: 'whisper.cpp', model: 'base' })
  })

  it('deleting the last model of the service says the program goes too, and the default turns voice off', async () => {
    net.status.mockResolvedValue(ON)
    net.catalog.mockResolvedValue(catalog(['small'], 'small'))
    net.remove.mockResolvedValue({ serviceRemoved: true, voiceDisabled: true })
    const w = open()
    await flushPromises()
    await w.get('[data-testid="voice-remove"]').trigger('click')
    const confirm = w.get('[data-testid="voice-remove-confirm"]').text()
    expect(confirm).toContain('서비스 프로그램도 함께 지웁니다')
    expect(confirm).toContain('음성 입력이 꺼집니다')
    net.status.mockResolvedValue(status({ state: 'not_installed' }))
    net.catalog.mockResolvedValue(catalog())
    await w.get('[data-testid="voice-remove-yes"]').trigger('click')
    await flushPromises()
    expect(net.remove).toHaveBeenCalledWith({ service: 'whisper.cpp', model: 'small' })
    expect(w.get('[data-testid="voice-removed"]').text()).toContain('음성 입력이 꺼졌습니다')
  })

  it('offers 다시 시작 when the voice module does not answer', async () => {
    net.status.mockResolvedValue(status({ state: 'error', defaultSelection: { service: 'whisper.cpp', model: 'small' }, error: { code: 'voice_module_unavailable', message: 'x' } }))
    net.catalog.mockResolvedValue(catalog(['small'], 'small'))
    net.restart.mockResolvedValue(ON)
    const w = open()
    await flushPromises()
    expect(w.get('[data-testid="voice-status-error"]').text()).toBe('음성 모듈이 응답하지 않습니다.')
    net.status.mockResolvedValue(ON)
    await w.get('[data-testid="voice-restart"]').trigger('click')
    await flushPromises()
    expect(net.restart).toHaveBeenCalled()
    expect(w.get('[data-testid="voice-state"]').text()).toBe('켜짐')
  })
})
