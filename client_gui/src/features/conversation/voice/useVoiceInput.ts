// The chat composer's voice input: record → (a) transcribe and put the text into the input, (b) send the recording as
// a voice message, or (c) live: stream PCM while speaking and show the partial text, confirmed on stop.
// Which one follows the person's voice settings (녹음 후 글로/음성, 실시간) and the model in use.
// The server resolves the model itself (personal pick, else the administrator default), so no model is sent here.

import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { DraftOwner } from '../drafts'
import {
  VoiceRequestError,
  cancelVoiceStream,
  deleteMessageAudio,
  openVoiceStream,
  sendVoiceChunk,
  transcribeAudio,
  uploadMessageAudio,
  type TurnAudio,
} from './api'
import { MicrophoneError, pcmBytes, startPcmCapture, startRecording, type PcmCaptureHandle, type RecordingHandle } from './recorder'
import { realtimeActive, useVoice } from './voiceStore'

export type VoicePhase = 'idle' | 'starting' | 'recording' | 'processing'
export type VoiceMode = 'text' | 'audio' | 'live'

export interface VoiceInputOptions {
  owner: () => DraftOwner | null
  /** Final text for the input (text and live modes). */
  onText: (text: string) => void
  /** Sends an uploaded recording as a message; resolves true when the server accepted it. */
  onAudio: (audio: TurnAudio, owner: DraftOwner) => Promise<boolean>
}

/** An error to show: an i18n key under gui.voice.errors (code) or the 2-minute notice. */
export interface VoiceNotice {
  kind: 'error' | 'info'
  code: string
}

export function errorCodeOf(error: unknown): string {
  if (error instanceof MicrophoneError) return `mic_${error.problem}`
  if (error instanceof VoiceRequestError) return error.code ?? 'failed'
  return 'failed'
}

export function useVoiceInput(options: VoiceInputOptions) {
  const voice = useVoice()
  const phase = ref<VoicePhase>('idle')
  const mode = ref<VoiceMode>('text')
  const seconds = ref(0)
  const processingSeconds = ref(0)
  const liveText = ref('')
  const notice = ref<VoiceNotice | null>(null)

  let generation = 0
  let sessionOwner: DraftOwner | null = null
  const ownerKey = (owner: DraftOwner | null) => owner && JSON.stringify([
    owner.userId, owner.projectId, owner.groupId, owner.intentId, owner.chatId,
  ])
  // Synchronous invalidation also covers leaving and returning before an upload resolves.
  watch(() => ownerKey(options.owner()), () => discard(), { flush: 'sync' })

  function isCurrent(gen: number): boolean {
    if (gen !== generation) return false
    // The login identity can change outside Vue reactivity; check it at async boundaries too.
    if (ownerKey(sessionOwner) !== ownerKey(options.owner())) { discard(); return false }
    return true
  }
  let tick: ReturnType<typeof setInterval> | null = null
  let processingTick: ReturnType<typeof setInterval> | null = null
  let recording: RecordingHandle | null = null
  let capture: PcmCaptureHandle | null = null
  let streamId: string | null = null
  let chunkIndex = 0
  let chunkQueue: Promise<void> = Promise.resolve()
  let streamFailed: unknown = null

  const busy = computed(() => phase.value !== 'idle')

  function clearTimers(): void {
    if (tick) clearInterval(tick)
    if (processingTick) clearInterval(processingTick)
    tick = null
    processingTick = null
  }

  function beginProcessing(): void {
    if (tick) clearInterval(tick)
    tick = null
    phase.value = 'processing'
    processingSeconds.value = 0
    processingTick = setInterval(() => { processingSeconds.value += 1 }, 1000)
  }

  function finish(): void {
    clearTimers()
    phase.value = 'idle'
    recording = null
    capture = null
    streamId = null
  }

  function fail(error: unknown): void {
    notice.value = { kind: 'error', code: errorCodeOf(error) }
    finish()
    liveText.value = ''
  }

  function enqueueChunk(samples: Int16Array, final: boolean, gen: number): Promise<void> {
    if (!isCurrent(gen)) return Promise.resolve()
    const index = chunkIndex++
    const bytes = pcmBytes(samples)
    chunkQueue = chunkQueue.then(async () => {
      if (!isCurrent(gen) || !streamId || streamFailed) return
      try {
        const result = await sendVoiceChunk(streamId, index, new Blob([bytes.buffer as ArrayBuffer], { type: 'application/octet-stream' }), final)
        if (isCurrent(gen)) liveText.value = result.text
      } catch (error) {
        if (isCurrent(gen)) streamFailed = error
      }
    })
    return chunkQueue
  }

  async function start(): Promise<void> {
    if (phase.value !== 'idle') return
    const owner = options.owner()
    if (!owner) return
    sessionOwner = { ...owner }
    const gen = ++generation
    notice.value = null
    liveText.value = ''
    seconds.value = 0
    const prefs = voice.state.prefs
    mode.value = prefs.action === 'audio' ? 'audio' : realtimeActive(prefs, voice.model.value) ? 'live' : 'text'
    phase.value = 'starting'
    const onLimit = () => {
      if (!isCurrent(gen) || phase.value !== 'recording') return
      notice.value = { kind: 'info', code: 'limit' }
      void stop()
    }
    try {
      if (mode.value === 'live') {
        const session = await openVoiceStream()
        if (!isCurrent(gen)) { void cancelVoiceStream(session.streamId).catch(() => undefined); return }
        streamId = session.streamId
        chunkIndex = 0
        chunkQueue = Promise.resolve()
        streamFailed = null
        try {
          const handle = await startPcmCapture({ onChunk: (samples) => { void enqueueChunk(samples, false, gen) }, onLimit })
          if (!isCurrent(gen)) { handle.stop(); return }
          capture = handle
        } catch (error) {
          void cancelVoiceStream(session.streamId).catch(() => undefined)
          throw error
        }
      } else {
        const handle = await startRecording({ onLimit })
        if (!isCurrent(gen)) { handle.cancel(); return }
        recording = handle
      }
      if (!isCurrent(gen)) return
      phase.value = 'recording'
      tick = setInterval(() => { seconds.value += 1 }, 1000)
    } catch (error) {
      if (isCurrent(gen)) fail(error)
    }
  }

  async function stop(): Promise<void> {
    if (phase.value !== 'recording') return
    const gen = generation
    if (!isCurrent(gen) || !sessionOwner) return
    const owner = { ...sessionOwner }
    beginProcessing()
    try {
      if (mode.value === 'live') {
        const rest = capture?.stop() ?? new Int16Array(0)
        capture = null
        await enqueueChunk(rest, true, gen)
        if (!isCurrent(gen)) return
        if (streamFailed) throw streamFailed
        const text = liveText.value.trim()
        liveText.value = ''
        if (text) options.onText(text)
        else notice.value = { kind: 'error', code: 'voice_empty_transcription' }
        finish()
        return
      }
      const audio = await recording!.stop()
      if (!isCurrent(gen)) return
      recording = null
      if (audio.blob.size === 0) throw new VoiceRequestError('empty recording', 'voice_empty_transcription', null)
      if (mode.value === 'text') {
        const result = await transcribeAudio(audio.blob, audio.filename)
        if (!isCurrent(gen)) return
        if (result.text.trim()) options.onText(result.text.trim())
        else notice.value = { kind: 'error', code: 'voice_empty_transcription' }
        finish()
        return
      }
      const flowId = owner.groupId
      if (!flowId) throw new VoiceRequestError('no flow', 'message_audio_forbidden', null)
      const uploaded = await uploadMessageAudio(flowId, audio.blob, audio.filename)
      if (!isCurrent(gen)) {
        // Cancelled while uploading: the unsent upload is removed (it would expire after 24h anyway).
        void deleteMessageAudio(flowId, uploaded.audio.audioId).catch(() => undefined)
        return
      }
      finish()
      await options.onAudio(uploaded.audio, owner)
    } catch (error) {
      if (isCurrent(gen)) fail(error)
    }
  }

  /** Throws the recording away (or stops waiting for its transcription). */
  function discard(): void {
    generation++
    recording?.cancel()
    capture?.stop()
    if (streamId) void cancelVoiceStream(streamId).catch(() => undefined)
    liveText.value = ''
    finish()
  }

  function toggle(): void {
    if (phase.value === 'idle') void start()
    else if (phase.value === 'recording') void stop()
  }

  function dismissNotice(): void {
    notice.value = null
  }

  onBeforeUnmount(discard)

  return { phase, mode, seconds, processingSeconds, liveText, notice, busy, start, stop, toggle, discard, dismissNotice }
}

export type VoiceInput = ReturnType<typeof useVoiceInput>

export function formatClock(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}
