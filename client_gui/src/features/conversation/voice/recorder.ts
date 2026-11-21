// Browser microphone capture for voice input.
//
// - File recording (MediaRecorder): webm/opus on Chrome and Edge, mp4/aac (m4a) on Safari. The server accepts webm,
//   m4a, mp3 and wav and stores audio messages as m4a.
// - Realtime capture: mono 16 kHz signed 16-bit little-endian PCM, the only input the live transcription route takes.
// Both stop by themselves at the server's 2-minute limit.

export const MAX_RECORDING_MS = 120_000
export const STREAM_SAMPLE_RATE = 16_000

export interface RecordedAudio {
  blob: Blob
  filename: string
  durationMs: number
}

export type MicrophoneProblem = 'unsupported' | 'denied' | 'no_device' | 'failed'

export class MicrophoneError extends Error {
  constructor(readonly problem: MicrophoneProblem, message: string = problem) {
    super(message)
    this.name = 'MicrophoneError'
  }
}

const CANDIDATE_TYPES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4;codecs=mp4a.40.2', 'audio/mp4', 'audio/aac'] as const

/** The first container this browser can record, or '' to let the browser choose. */
export function pickRecorderType(isSupported: (type: string) => boolean = (type) =>
  typeof MediaRecorder !== 'undefined' && typeof MediaRecorder.isTypeSupported === 'function' && MediaRecorder.isTypeSupported(type)): string {
  return CANDIDATE_TYPES.find((type) => isSupported(type)) ?? ''
}

/** File name with the extension the server's format check expects. */
export function recordingFilename(mimeType: string): string {
  const type = mimeType.toLowerCase()
  if (type.includes('webm')) return 'recording.webm'
  if (type.includes('mp4') || type.includes('aac') || type.includes('m4a')) return 'recording.m4a'
  if (type.includes('ogg')) return 'recording.ogg'
  if (type.includes('wav')) return 'recording.wav'
  return 'recording.webm'
}

export function microphoneSupported(): boolean {
  return typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia
}

async function openMicrophone(): Promise<MediaStream> {
  if (!microphoneSupported()) throw new MicrophoneError('unsupported')
  try {
    return await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } })
  } catch (error) {
    const name = (error as { name?: string } | null)?.name ?? ''
    if (name === 'NotAllowedError' || name === 'SecurityError') throw new MicrophoneError('denied')
    if (name === 'NotFoundError' || name === 'OverconstrainedError') throw new MicrophoneError('no_device')
    throw new MicrophoneError('failed', String((error as Error | null)?.message ?? error))
  }
}

function release(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop())
}

export interface RecordingHandle {
  /** Stops and resolves the recorded audio (an empty blob when nothing was captured). */
  stop(): Promise<RecordedAudio>
  /** Stops and throws the audio away. */
  cancel(): void
}

/**
 * Starts recording a file. `onLimit` runs when the 2-minute limit stops the recording by itself; the caller then
 * calls `stop()` to collect the audio as usual.
 */
export async function startRecording(options: { maxMs?: number; onLimit?: () => void } = {}): Promise<RecordingHandle> {
  if (typeof MediaRecorder === 'undefined') throw new MicrophoneError('unsupported')
  const stream = await openMicrophone()
  const type = pickRecorderType()
  let recorder: MediaRecorder
  try {
    recorder = type ? new MediaRecorder(stream, { mimeType: type }) : new MediaRecorder(stream)
  } catch (error) {
    release(stream)
    throw new MicrophoneError('failed', String((error as Error | null)?.message ?? error))
  }
  const chunks: Blob[] = []
  const started = Date.now()
  let ended: Promise<void> | null = null
  recorder.ondataavailable = (event) => { if (event.data && event.data.size > 0) chunks.push(event.data) }
  const finish = (): Promise<void> => {
    if (!ended) {
      ended = new Promise<void>((resolve) => {
        if (recorder.state === 'inactive') { resolve(); return }
        recorder.onstop = () => resolve()
        recorder.stop()
      }).finally(() => release(stream))
    }
    return ended
  }
  const limit = setTimeout(() => { void finish().then(() => options.onLimit?.()) }, options.maxMs ?? MAX_RECORDING_MS)
  recorder.start(1000)
  return {
    async stop() {
      clearTimeout(limit)
      await finish()
      const mimeType = recorder.mimeType || type || 'audio/webm'
      return { blob: new Blob(chunks, { type: mimeType }), filename: recordingFilename(mimeType), durationMs: Math.min(Date.now() - started, options.maxMs ?? MAX_RECORDING_MS) }
    },
    cancel() {
      clearTimeout(limit)
      void finish()
      chunks.length = 0
    },
  }
}

/** Averages `input` (at `inputRate`) down to 16 kHz signed 16-bit samples. */
export function downsampleToPcm16(input: Float32Array, inputRate: number, outputRate = STREAM_SAMPLE_RATE): Int16Array {
  const ratio = inputRate / outputRate
  const length = ratio <= 1 ? input.length : Math.floor(input.length / ratio)
  const out = new Int16Array(length)
  for (let i = 0; i < length; i++) {
    let value: number
    if (ratio <= 1) {
      value = input[i] ?? 0
    } else {
      const start = Math.floor(i * ratio)
      const end = Math.min(input.length, Math.floor((i + 1) * ratio))
      let sum = 0
      for (let j = start; j < end; j++) sum += input[j] ?? 0
      value = end > start ? sum / (end - start) : 0
    }
    const clamped = Math.max(-1, Math.min(1, value))
    out[i] = clamped < 0 ? Math.round(clamped * 0x8000) : Math.round(clamped * 0x7fff)
  }
  return out
}

/** Little-endian bytes of the samples (the stream route reads pcm_s16le). */
export function pcmBytes(samples: Int16Array): Uint8Array {
  const bytes = new Uint8Array(samples.length * 2)
  const view = new DataView(bytes.buffer)
  samples.forEach((sample, i) => view.setInt16(i * 2, sample, true))
  return bytes
}

export interface PcmCaptureHandle {
  /** Stops capturing and returns the samples not yet handed to `onChunk`. */
  stop(): Int16Array
}

/**
 * Captures the microphone as 16 kHz PCM16 and hands it over in chunks of `chunkMs` (default 1 s). `onLimit` runs
 * once 2 minutes of samples have been captured; capture stops by itself then.
 */
export async function startPcmCapture(options: { onChunk: (samples: Int16Array) => void; chunkMs?: number; maxMs?: number; onLimit?: () => void }): Promise<PcmCaptureHandle> {
  const AudioContextClass = (globalThis as { AudioContext?: typeof AudioContext; webkitAudioContext?: typeof AudioContext }).AudioContext
    ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
  if (!AudioContextClass) throw new MicrophoneError('unsupported')
  const stream = await openMicrophone()
  let context: AudioContext
  try {
    context = new AudioContextClass()
  } catch (error) {
    release(stream)
    throw new MicrophoneError('failed', String((error as Error | null)?.message ?? error))
  }
  const source = context.createMediaStreamSource(stream)
  // ScriptProcessorNode is deprecated but available everywhere without a separate worklet module.
  const processor = context.createScriptProcessor(4096, 1, 1)
  const chunkSamples = Math.round(STREAM_SAMPLE_RATE * (options.chunkMs ?? 1000) / 1000)
  const maxSamples = Math.round(STREAM_SAMPLE_RATE * (options.maxMs ?? MAX_RECORDING_MS) / 1000)
  let buffered: Int16Array[] = []
  let bufferedLength = 0
  let total = 0
  let stopped = false
  const take = (): Int16Array => {
    const out = new Int16Array(bufferedLength)
    let at = 0
    for (const part of buffered) { out.set(part, at); at += part.length }
    buffered = []
    bufferedLength = 0
    return out
  }
  const shutdown = () => {
    if (stopped) return
    stopped = true
    processor.onaudioprocess = null
    try { source.disconnect(); processor.disconnect() } catch { /* already disconnected */ }
    release(stream)
    void context.close().catch(() => undefined)
  }
  processor.onaudioprocess = (event) => {
    if (stopped) return
    let samples = downsampleToPcm16(event.inputBuffer.getChannelData(0), context.sampleRate)
    if (total + samples.length > maxSamples) samples = samples.subarray(0, Math.max(0, maxSamples - total))
    total += samples.length
    buffered.push(samples)
    bufferedLength += samples.length
    while (bufferedLength >= chunkSamples) {
      const all = take()
      options.onChunk(all.subarray(0, chunkSamples))
      if (all.length > chunkSamples) { buffered = [all.slice(chunkSamples)]; bufferedLength = all.length - chunkSamples }
    }
    if (total >= maxSamples) {
      shutdown()
      options.onLimit?.()
    }
  }
  source.connect(processor)
  processor.connect(context.destination)
  return {
    stop() {
      shutdown()
      return take()
    },
  }
}
