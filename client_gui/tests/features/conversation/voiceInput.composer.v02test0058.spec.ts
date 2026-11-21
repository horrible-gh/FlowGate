import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import ComposerVoiceSettings from '@/features/conversation/ComposerVoiceSettings.vue'
import TimelineAudio from '@/features/conversation/TimelineAudio.vue'
import type { VoiceService, VoiceStatus } from '@/features/conversation/voice/api'
import { installVoiceMessages } from '@/features/conversation/voice/messages'
import { resetVoiceStore } from '@/features/conversation/voice/voiceStore'

// The chat side of voice input: the mic in the composer (offered / off / recording pill), the recording turned into
// input text or sent as a voice message with its audio id, the ⚙ voice section, and a voice message's player.
const net = vi.hoisted(() => ({
  status: vi.fn(),
  catalog: vi.fn(),
  prefs: vi.fn(),
  savePrefs: vi.fn(),
  transcribe: vi.fn(),
  upload: vi.fn(),
  removeAudio: vi.fn(),
  install: vi.fn(),
  installJob: vi.fn(),
  blob: vi.fn(),
  record: vi.fn(),
}))
vi.mock('@/features/conversation/voice/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/voice/api')>()),
  fetchVoiceStatus: net.status,
  fetchVoiceCatalog: net.catalog,
  fetchVoicePrefs: net.prefs,
  saveVoicePrefs: net.savePrefs,
  transcribeAudio: net.transcribe,
  uploadMessageAudio: net.upload,
  deleteMessageAudio: net.removeAudio,
  startVoiceInstall: net.install,
  fetchVoiceInstall: net.installJob,
  fetchMessageAudioBlob: net.blob,
}))
vi.mock('@/features/conversation/voice/recorder', async (original) => ({
  ...(await original<typeof import('@/features/conversation/voice/recorder')>()),
  startRecording: net.record,
}))

registerAppMessages(i18n)
installVoiceMessages(i18n.global)

const FLOW = 'p.m.0001'
const CHAT = 'p.m.0001.0002-CH'
const AUDIO = { audioId: `aud_${'b'.repeat(32)}`, mediaType: 'audio/mp4', size: 9, text: '오늘 회의 정리해 줘', url: `/api/v1/flows/${FLOW}/message-audio/aud_b/content` }

const model = (id: string, extra: Partial<VoiceService['models'][number]> = {}) => ({
  id, name: id, sizeBytes: 487_601_967, installed: true, realtime: false, isDefault: false, available: true, unavailableReason: null, languages: [], gpuRequired: false, ...extra,
})
const CATALOG: VoiceService[] = [
  { id: 'whisper.cpp', name: 'whisper.cpp', version: '1.8.2', license: 'MIT', installed: true, available: true, unavailableReason: null,
    models: [model('small', { isDefault: true }), model('base', { installed: false, sizeBytes: 147_951_465 })] },
  { id: 'sherpa-onnx', name: 'sherpa-onnx', version: '1.12.20', license: 'Apache-2.0', installed: false, available: true, unavailableReason: null,
    models: [model('zipformer-en-20m', { installed: false, realtime: true })] },
]
const status = (extra: Partial<VoiceStatus> = {}): VoiceStatus => ({ enabled: true, state: 'on', defaultSelection: { service: 'whisper.cpp', model: 'small' }, error: null, canManage: false, ...extra })
const PREFS = { action: 'text' as const, service: '', model: '', realtime: false }

function recording(blob = new Blob(['voice'], { type: 'audio/webm' })) {
  const handle = { stop: vi.fn(async () => ({ blob, filename: 'recording.webm', durationMs: 3000 })), cancel: vi.fn() }
  net.record.mockResolvedValueOnce(handle)
  return handle
}

let wrappers: VueWrapper[] = []
function mountComposer(props: Record<string, unknown> = {}) {
  const wrapper = mount(ConversationComposer, {
    props: {
      chatId: CHAT, projectId: 'p', groupId: FLOW, disabled: false, settings: DEFAULT_CHAT_SETTINGS, savingSettings: false,
      pendingSourceMode: null, providers: [], selectedProviderId: '', providersStatus: 'ready', hasProviders: false, sending: false,
      running: false, runStarting: false, cancelling: false, copyingMention: false, manualCopyText: null,
      onSend: vi.fn(async () => true),
      ...props,
    },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true } },
  })
  wrappers.push(wrapper)
  return wrapper
}

const mic = (w: VueWrapper) => w.find('[data-test="conv-voice-mic"]')
const input = (w: VueWrapper) => w.get('[data-test="conv-input"]').element as HTMLTextAreaElement

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  resetVoiceStore()
  for (const fn of Object.values(net)) fn.mockReset()
  net.status.mockResolvedValue(status())
  net.catalog.mockResolvedValue(CATALOG)
  net.prefs.mockResolvedValue(PREFS)
  net.savePrefs.mockImplementation(async (patch: object) => ({ ...PREFS, ...patch }))
  net.removeAudio.mockResolvedValue(undefined)
  URL.createObjectURL = vi.fn(() => 'blob:audio')
  URL.revokeObjectURL = vi.fn()
  // jsdom has no media playback.
  window.HTMLMediaElement.prototype.play = vi.fn(async () => undefined)
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  resetVoiceStore()
})

describe('the mic in the composer', () => {
  it('is not offered to a user before installation', async () => {
    net.status.mockResolvedValue(status({ state: 'not_installed', enabled: false, defaultSelection: null }))
    net.catalog.mockResolvedValue([])
    const w = mountComposer()
    await flushPromises()
    expect(mic(w).exists()).toBe(false)
  })

  it('names the model in use and is pressable while voice input is on', async () => {
    const w = mountComposer()
    await flushPromises()
    expect(mic(w).attributes('title')).toBe('음성 입력 · small')
    expect(mic(w).attributes('disabled')).toBeUndefined()
  })

  it('stays visible but cannot be pressed while an administrator has it off', async () => {
    net.status.mockResolvedValue(status({ state: 'off', enabled: false }))
    const w = mountComposer()
    await flushPromises()
    expect(mic(w).attributes('title')).toBe('음성 입력 · 관리자가 꺼 둠')
    expect(mic(w).attributes('disabled')).toBeDefined()
  })
})

describe('글로: the transcript goes into the input', () => {
  it('records, shows the pill, then puts the transcript after what is typed', async () => {
    const handle = recording()
    net.transcribe.mockResolvedValue({ text: '정리해 줘', elapsedMs: 2100, service: 'whisper.cpp', model: 'small' })
    const w = mountComposer()
    await flushPromises()
    await w.get('[data-test="conv-input"]').setValue('오늘 회의')
    await mic(w).trigger('click')
    await flushPromises()
    expect(w.find('[data-test="conv-voice-pill"]').text()).toContain('small')
    await mic(w).trigger('click')
    await flushPromises()
    expect(handle.stop).toHaveBeenCalled()
    expect(net.transcribe).toHaveBeenCalledWith(expect.any(Blob), 'recording.webm')
    expect(input(w).value).toBe('오늘 회의 정리해 줘')
    expect(w.find('[data-test="conv-voice-pill"]').exists()).toBe(false)
  })

  it('✕ throws the recording away without transcribing', async () => {
    const handle = recording()
    const w = mountComposer()
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    await w.get('[data-test="conv-voice-discard"]').trigger('click')
    await flushPromises()
    expect(handle.cancel).toHaveBeenCalled()
    expect(net.transcribe).not.toHaveBeenCalled()
    expect(w.find('[data-test="conv-voice-pill"]').exists()).toBe(false)
  })

  it('says why when the server refuses (voice switched off meanwhile)', async () => {
    recording()
    const { VoiceRequestError } = await import('@/features/conversation/voice/api')
    net.transcribe.mockRejectedValue(new VoiceRequestError('off', 'voice_disabled', 409))
    const w = mountComposer()
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    expect(w.get('[data-test="conv-voice-notice"]').text()).toContain('음성 입력이 꺼져 있습니다.')
    expect(input(w).value).toBe('')
  })

  it('a denied microphone is explained', async () => {
    const { MicrophoneError } = await import('@/features/conversation/voice/recorder')
    net.record.mockRejectedValueOnce(new MicrophoneError('denied'))
    const w = mountComposer()
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    expect(w.get('[data-test="conv-voice-notice"]').text()).toContain('마이크 사용이 허용되지 않았습니다')
  })
})

describe('음성: the recording is sent as a voice message', () => {
  it('uploads to the Flow and sends the transcript with the audio id; typed text stays', async () => {
    net.prefs.mockResolvedValue({ ...PREFS, action: 'audio' })
    recording()
    net.upload.mockResolvedValue({ audio: AUDIO, elapsedMs: 900 })
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await flushPromises()
    expect(mic(w).attributes('title')).toBe('음성 그대로 보내기 · small')
    await w.get('[data-test="conv-input"]').setValue('따로 쓰던 글')
    await mic(w).trigger('click')
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    expect(net.upload).toHaveBeenCalledWith(FLOW, expect.any(Blob), 'recording.webm')
    expect(onSend).toHaveBeenCalledTimes(1)
    const [text, snapshot] = onSend.mock.calls[0] as unknown as [string, { audio?: string; body: string }]
    expect(text).toBe(AUDIO.text)
    expect(snapshot).toMatchObject({ body: AUDIO.text, audio: AUDIO.audioId })
    expect(input(w).value).toBe('따로 쓰던 글')
  })

  it('when the message cannot go out now, the transcript is kept in the input and the upload removed', async () => {
    net.prefs.mockResolvedValue({ ...PREFS, action: 'audio' })
    recording()
    net.upload.mockResolvedValue({ audio: AUDIO, elapsedMs: 900 })
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend, aiPickStatus: 'loading' })
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    await mic(w).trigger('click')
    await flushPromises()
    expect(onSend).not.toHaveBeenCalled()
    expect(net.removeAudio).toHaveBeenCalledWith(FLOW, AUDIO.audioId)
    expect(input(w).value).toBe(AUDIO.text)
  })
})

describe('⚙ voice section', () => {
  function mountSettings() {
    const w = mount(ComposerVoiceSettings, { attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(w)
    return w
  }

  it('saves 녹음 후 at once', async () => {
    const w = mountSettings()
    await flushPromises()
    await w.get('[data-test="conv-voice-action-audio"]').trigger('click')
    await flushPromises()
    expect(net.savePrefs).toHaveBeenCalledWith({ action: 'audio' })
    expect(w.get('[data-test="conv-voice-action-audio"]').classes()).toContain('on')
  })

  it('a user sees installed models only; realtime is off for a model without it', async () => {
    net.catalog.mockResolvedValue([{ ...CATALOG[0], models: [CATALOG[0].models[0]] }])
    const w = mountSettings()
    await flushPromises()
    await w.get('[data-test="conv-voice-model"]').trigger('click')
    expect(w.findAll('[data-test="conv-voice-model-list"] [data-model]').map((r) => r.attributes('data-model'))).toEqual(['small'])
    expect(w.find('[data-test^="conv-voice-install-"]').exists()).toBe(false)
    expect(w.get('[data-test="conv-voice-realtime"]').attributes('disabled')).toBeDefined()
  })

  it('a user sees "관리자가 꺼 둠" while voice input is off', async () => {
    net.status.mockResolvedValue(status({ state: 'off', enabled: false }))
    const w = mountSettings()
    await flushPromises()
    expect(w.find('[data-test="conv-voice-admin-off"]').exists()).toBe(true)
  })

  it('an administrator installs a model of another service in place', async () => {
    net.status.mockResolvedValue(status({ canManage: true }))
    net.install.mockResolvedValue('vi_1')
    net.installJob.mockResolvedValue({ installId: 'vi_1', service: 'sherpa-onnx', model: 'zipformer-en-20m', step: 'downloading', receivedBytes: 10, totalBytes: 100, done: false, error: null })
    const w = mountSettings()
    await flushPromises()
    await w.get('[data-test="conv-voice-service"]').trigger('click')
    await w.get('[data-service="sherpa-onnx"]').trigger('click')
    // Not installed yet: the model list opens with 설치, and the current model stays in use meanwhile.
    expect(w.get('[data-test="conv-voice-model"]').text()).toContain('설치할 모델 고르기')
    expect(net.savePrefs).not.toHaveBeenCalled()
    await w.get('[data-test="conv-voice-install-zipformer-en-20m"]').trigger('click')
    await flushPromises()
    expect(net.install).toHaveBeenCalledWith({ service: 'sherpa-onnx', model: 'zipformer-en-20m' })
    expect(w.find('[data-test="conv-voice-install-cancel"]').exists()).toBe(true)
  })

  it('picking an installed model saves the pair', async () => {
    net.catalog.mockResolvedValue([{ ...CATALOG[0], models: [CATALOG[0].models[0], model('medium')] }])
    const w = mountSettings()
    await flushPromises()
    await w.get('[data-test="conv-voice-model"]').trigger('click')
    await w.get('[data-model="medium"]').trigger('click')
    await flushPromises()
    expect(net.savePrefs).toHaveBeenCalledWith({ service: 'whisper.cpp', model: 'medium', realtime: false })
  })
})

describe('voice message player', () => {
  it('fetches the recording through the session on the first press and plays it', async () => {
    net.blob.mockResolvedValue(new Blob(['m4a'], { type: 'audio/mp4' }))
    const w = mount(TimelineAudio, { props: { audio: AUDIO }, global: { plugins: [i18n] } })
    wrappers.push(w)
    expect(net.blob).not.toHaveBeenCalled()
    await w.get('[data-test="tl-audio-play"]').trigger('click')
    await flushPromises()
    expect(net.blob).toHaveBeenCalledWith(AUDIO.url)
    expect(w.get('[data-test="tl-audio-player"]').attributes('src')).toBe('blob:audio')
  })
})
