import { beforeEach, describe, expect, it, vi } from 'vitest'

// Voice input requests and pure helpers: the server contract in server/VOICE_API.md, the audio id on a turn, the
// browser recording formats and the 16 kHz PCM conversion of live input.
const net = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: vi.fn(),
  deleteRequest: vi.fn(),
  postFormRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ ...net, extractApiErrorMessage: (_e: unknown, fallback: string) => fallback }))

import { postTurn, toTurn } from '@/features/conversation/api'
import {
  VoiceRequestError,
  fetchVoiceCatalog,
  fetchVoiceStatus,
  openVoiceStream,
  removeVoiceModel,
  saveVoicePrefs,
  sendVoiceChunk,
  startVoiceInstall,
  toVoicePrefs,
  transcribeAudio,
  uploadMessageAudio,
} from '@/features/conversation/voice/api'
import { downsampleToPcm16, pcmBytes, pickRecorderType, recordingFilename } from '@/features/conversation/voice/recorder'
import { effectiveSelection, realtimeActive } from '@/features/conversation/voice/voiceStore'
import { formatBytes, voiceErrorKey } from '@/features/conversation/voice/messages'

const CATALOG = {
  ok: true,
  services: [
    { id: 'whisper.cpp', name: 'whisper.cpp', version: '1.8.2', license: 'MIT', installed: true, available: true, unavailable_reason: null,
      models: [
        { id: 'small', name: 'small', size_bytes: 487601967, installed: true, realtime: false, is_default: true, available: true, unavailable_reason: null, languages: ['ko'] },
        { id: 'base', name: 'base', size_bytes: 147951465, installed: false, realtime: false, is_default: false, available: true, unavailable_reason: null },
      ] },
    { id: 'sherpa-onnx', name: 'sherpa-onnx', version: '1.12.20', license: 'Apache-2.0', installed: true, available: true, unavailable_reason: null,
      models: [{ id: 'zipformer-en-20m', name: 'Streaming Zipformer English 20M', size_bytes: 127887156, installed: true, realtime: true, is_default: false, available: true }] },
  ],
}

const formOf = (call: unknown[]) => call[1] as FormData

beforeEach(() => {
  for (const fn of Object.values(net)) fn.mockReset()
})

describe('status and catalog', () => {
  it('maps status, including the default selection and can_manage', async () => {
    net.getRequest.mockResolvedValue({ data: { ok: true, enabled: true, state: 'on', default: { service: 'whisper.cpp', model: 'small' }, error: null, can_manage: true } })
    const status = await fetchVoiceStatus()
    expect(net.getRequest).toHaveBeenCalledWith('/api/v1/voice/status')
    expect(status).toEqual({ enabled: true, state: 'on', defaultSelection: { service: 'whisper.cpp', model: 'small' }, error: null, canManage: true })
  })

  it('treats an unknown state as an error and a missing default as none', async () => {
    net.getRequest.mockResolvedValue({ data: { state: 'weird', default: null } })
    expect(await fetchVoiceStatus()).toMatchObject({ state: 'error', defaultSelection: null, enabled: false, canManage: false })
  })

  it('maps the two-level catalog (service → models)', async () => {
    net.getRequest.mockResolvedValue({ data: CATALOG })
    const services = await fetchVoiceCatalog()
    expect(services.map((s) => s.id)).toEqual(['whisper.cpp', 'sherpa-onnx'])
    expect(services[0].models[0]).toMatchObject({ id: 'small', sizeBytes: 487601967, installed: true, isDefault: true, realtime: false })
    expect(services[1].models[0]).toMatchObject({ realtime: true })
  })
})

describe('administration', () => {
  it('starts an installation with the pair and returns its id', async () => {
    net.postRequest.mockResolvedValue({ data: { ok: true, install_id: 'vi_1' } })
    expect(await startVoiceInstall({ service: 'whisper.cpp', model: 'base' })).toBe('vi_1')
    expect(net.postRequest).toHaveBeenCalledWith('/api/v1/voice/installs', { service: 'whisper.cpp', model: 'base' })
  })

  it('deletes a model by its path and reports what went with it', async () => {
    net.deleteRequest.mockResolvedValue({ data: { ok: true, service_removed: true, voice_disabled: false } })
    expect(await removeVoiceModel({ service: 'whisper.cpp', model: 'small' })).toEqual({ serviceRemoved: true, voiceDisabled: false })
    expect(net.deleteRequest).toHaveBeenCalledWith('/api/v1/voice/services/whisper.cpp/models/small')
  })

  it('keeps the server error code', async () => {
    net.postRequest.mockRejectedValue({ response: { status: 507, data: { ok: false, error: { code: 'voice_disk_full', message: 'Insufficient disk space' } } } })
    const error = await startVoiceInstall({ service: 'whisper.cpp', model: 'base' }).catch((e) => e)
    expect(error).toBeInstanceOf(VoiceRequestError)
    expect(error).toMatchObject({ code: 'voice_disk_full', status: 507 })
  })

  it('names a request that never reached the server', async () => {
    net.getRequest.mockRejectedValue(new Error('Network Error'))
    expect(await fetchVoiceStatus().catch((e) => e.code)).toBe('network')
  })
})

describe('transcription and voice messages', () => {
  it('sends the recording as multipart `file` and no model (the server resolves the person\'s choice)', async () => {
    net.postFormRequest.mockResolvedValue({ data: { ok: true, text: '안녕하세요', elapsed_ms: 1234, service: 'whisper.cpp', model: 'small' } })
    const blob = new Blob(['x'], { type: 'audio/webm' })
    const result = await transcribeAudio(blob, 'recording.webm')
    expect(net.postFormRequest.mock.calls[0][0]).toBe('/api/v1/voice/transcriptions')
    const form = formOf(net.postFormRequest.mock.calls[0])
    expect((form.get('file') as File).name).toBe('recording.webm')
    expect(form.has('service')).toBe(false)
    expect(result).toEqual({ text: '안녕하세요', elapsedMs: 1234, service: 'whisper.cpp', model: 'small' })
  })

  it('sends a paired service and model when given', async () => {
    net.postFormRequest.mockResolvedValue({ data: { text: 'x' } })
    await transcribeAudio(new Blob(['x']), 'recording.m4a', { service: 'sherpa-onnx', model: 'sensevoice-small-int8' })
    const form = formOf(net.postFormRequest.mock.calls[0])
    expect([form.get('service'), form.get('model')]).toEqual(['sherpa-onnx', 'sensevoice-small-int8'])
  })

  it('uploads a voice message to the Flow and returns the audio with its transcript', async () => {
    const audio = { audio_id: `aud_${'a'.repeat(32)}`, media_type: 'audio/mp4', size: 9, text: '회의 정리', url: '/flowgate/api/v1/flows/p.m.0001/message-audio/aud_x/content' }
    net.postFormRequest.mockResolvedValue({ data: { ok: true, audio, elapsed_ms: 800 } })
    const result = await uploadMessageAudio('p.m.0001', new Blob(['x']), 'recording.webm')
    expect(net.postFormRequest.mock.calls[0][0]).toBe('/api/v1/flows/p.m.0001/message-audio')
    expect(result).toEqual({ audio: { audioId: audio.audio_id, mediaType: 'audio/mp4', size: 9, text: '회의 정리', url: audio.url }, elapsedMs: 800 })
  })

  it('a turn carries `audio_id` when sent and `audio` when read', async () => {
    net.postRequest.mockResolvedValue({ data: { turn: null, head_seq: 3 } })
    await postTurn('p.m.0001.0002-CH', { body: '회의 정리', idempotencyKey: 'k', basedOnSeq: 2, audioId: 'aud_1' })
    expect(net.postRequest.mock.calls[0][1]).toEqual({ body: '회의 정리', idempotency_key: 'k', based_on_seq: 2, audio_id: 'aud_1' })
    const turn = toTurn({ seq: 3, speaker: 'user', body: '회의 정리', audio: { audio_id: 'aud_1', media_type: 'audio/mp4', size: 5, text: '회의 정리', url: '/u' } } as never)
    expect(turn.audio).toEqual({ audioId: 'aud_1', mediaType: 'audio/mp4', size: 5, text: '회의 정리', url: '/u' })
    expect(toTurn({ seq: 4, speaker: 'user', body: 'x', audio: null } as never).audio).toBeUndefined()
  })

  it('a text-only turn sends no audio_id', async () => {
    net.postRequest.mockResolvedValue({ data: { turn: null, head_seq: 3 } })
    await postTurn('c', { body: 'x', idempotencyKey: 'k', basedOnSeq: 0 })
    expect(net.postRequest.mock.calls[0][1]).not.toHaveProperty('audio_id')
  })
})

describe('live input', () => {
  it('opens a stream and posts raw PCM chunks in order, the last one with final=true', async () => {
    net.postRequest.mockResolvedValue({ data: { ok: true, stream_id: 'vs_1', sample_rate: 16000, channels: 1, format: 'pcm_s16le', max_seconds: 120, service: 'sherpa-onnx', model: 'zipformer-en-20m' } })
    expect(await openVoiceStream()).toMatchObject({ streamId: 'vs_1', sampleRate: 16000 })
    expect(net.postRequest).toHaveBeenCalledWith('/api/v1/voice/streams', {})
    net.postFormRequest.mockResolvedValue({ data: { text: 'hello', final: true, next_chunk: 3, elapsed_ms: 10 } })
    expect(await sendVoiceChunk('vs_1', 2, new Blob([]), true)).toEqual({ text: 'hello', final: true, nextChunk: 3, elapsedMs: 10 })
    expect(net.postFormRequest.mock.calls[0][0]).toBe('/api/v1/voice/streams/vs_1/chunks?index=2&final=true')
  })

  it('converts 48 kHz float samples to 16 kHz little-endian PCM16', () => {
    const input = new Float32Array(48).fill(0.5)
    const out = downsampleToPcm16(input, 48000)
    expect(out.length).toBe(16)
    expect(out[0]).toBe(Math.round(0.5 * 0x7fff))
    expect(Array.from(downsampleToPcm16(new Float32Array([-1, 2]), 16000))).toEqual([-32768, 32767])
    expect(Array.from(pcmBytes(new Int16Array([1, -2])))).toEqual([1, 0, 0xfe, 0xff])
  })
})

describe('recording formats', () => {
  it('prefers webm/opus, falls back to mp4 (Safari), and names the file for the server', () => {
    expect(pickRecorderType((t) => t.startsWith('audio/webm'))).toBe('audio/webm;codecs=opus')
    expect(pickRecorderType((t) => t === 'audio/mp4')).toBe('audio/mp4')
    expect(pickRecorderType(() => false)).toBe('')
    expect(recordingFilename('audio/webm;codecs=opus')).toBe('recording.webm')
    expect(recordingFilename('audio/mp4')).toBe('recording.m4a')
  })
})

describe('personal settings and the model in use', () => {
  it('reads the four voice fields and saves service and model together', async () => {
    expect(toVoicePrefs({ settings: { voice_action: 'audio', voice_service: 'whisper.cpp', voice_model: 'small', voice_realtime: true } }))
      .toEqual({ action: 'audio', service: 'whisper.cpp', model: 'small', realtime: true })
    expect(toVoicePrefs({ settings: {} })).toEqual({ action: 'text', service: '', model: '', realtime: false })
    net.patchRequest.mockResolvedValue({ data: { settings: { voice_service: 'whisper.cpp', voice_model: 'small' } } })
    await saveVoicePrefs({ service: 'whisper.cpp', model: 'small', realtime: false })
    expect(net.patchRequest).toHaveBeenCalledWith('/api/v1/me/ui-settings', { voice_service: 'whisper.cpp', voice_model: 'small', voice_realtime: false })
  })

  it('uses the person\'s installed pick, else the administrator default', async () => {
    net.getRequest.mockResolvedValue({ data: CATALOG })
    const catalog = await fetchVoiceCatalog()
    const status = { enabled: true, state: 'on' as const, defaultSelection: { service: 'whisper.cpp', model: 'small' }, error: null, canManage: false }
    const prefs = { action: 'text' as const, service: 'sherpa-onnx', model: 'zipformer-en-20m', realtime: true }
    expect(effectiveSelection(prefs, status, catalog)).toEqual({ service: 'sherpa-onnx', model: 'zipformer-en-20m' })
    // A deleted (or never installed) personal pick falls back to the default.
    expect(effectiveSelection({ ...prefs, service: 'whisper.cpp', model: 'base' }, status, catalog)).toEqual({ service: 'whisper.cpp', model: 'small' })
    expect(realtimeActive(prefs, catalog[1].models[0])).toBe(true)
    expect(realtimeActive({ ...prefs, action: 'audio' }, catalog[1].models[0])).toBe(false)
    expect(realtimeActive(prefs, catalog[0].models[0])).toBe(false)
  })

  it('formats sizes and falls back to the generic error text for an unknown code', () => {
    expect(formatBytes(487601967)).toBe('488MB')
    expect(formatBytes(1533763059)).toBe('1.5GB')
    expect(voiceErrorKey('voice_disk_full', () => true)).toBe('gui.voice.errors.voice_disk_full')
    expect(voiceErrorKey('something_new', () => false)).toBe('gui.voice.errors.failed')
  })
})
