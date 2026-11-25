// Voice input API adapter. Every call goes to the FlowGate server only (browser -> FlowGate -> local voice module);
// no external speech service is ever called from the browser. Contract: server/VOICE_API.md.
//
//   status / catalog          GET    /voice/status, /voice/catalog (ordinary users see installed models only)
//   install (admin)           POST   /voice/installs, GET|DELETE /voice/installs/{id}
//   remove (admin)            DELETE /voice/services/{service}/models/{model}
//   default / on-off (admin)  PUT    /voice/default, /voice/enabled, POST /voice/restart
//   transcribe                POST   /voice/transcriptions (multipart `file`, optional paired service/model)
//   test (admin)              POST   /voice/test
//   message audio             POST   /flows/{flow}/message-audio, GET …/{id}/content, DELETE …/{id}
//   realtime                  POST   /voice/streams, POST …/{id}/chunks?index=N[&final=true], DELETE …/{id}
//   personal settings         GET/PATCH /me/ui-settings (voice_action, voice_service, voice_model, voice_realtime)

import {
  deleteRequest,
  downloadBlobRequest,
  getRequest,
  patchRequest,
  postFormRequest,
  postRequest,
  putRequest,
} from '@shared/api'

const API = '/api/v1'
const VOICE = `${API}/voice`
/** The personal settings route (same envelope as the other /me/ui-settings fields). */
const UI_SETTINGS_PATH = `${API}/me/ui-settings`

interface UiSettingsResponse {
  settings?: Record<string, unknown>
}

export type VoiceStateName = 'not_installed' | 'installing' | 'off' | 'on' | 'error'

export interface VoiceSelection {
  service: string
  model: string
}

export interface VoiceStatus {
  enabled: boolean
  state: VoiceStateName
  defaultSelection: VoiceSelection | null
  error: { code: string; message: string } | null
  canManage: boolean
  /** Removed models still on disk or stored as the default; shown as retired, never activated. */
  retired: readonly VoiceRetiredModel[]
}

export interface VoiceRetiredModel {
  service: string
  model: string
  name: string
  installed: boolean
  wasDefault: boolean
}

export interface VoiceModel {
  id: string
  name: string
  sizeBytes: number
  installed: boolean
  realtime: boolean
  isDefault: boolean
  available: boolean
  unavailableReason: string | null
  languages: readonly string[]
  gpuRequired: boolean
  license?: string
  licenseUrl?: string
  source?: string
  sourceUrl?: string
  defaultEligible?: boolean
}

/** One downloaded file of a voice service (engine, weights, runtime, …) and its license. */
export interface VoiceComponent {
  filename: string
  componentType: string
  license: string
  distribution: string
  url: string
  /** F86 contract 1: the license text URL of this component; null when the server has none. */
  licenseUrl?: string | null
}

export interface VoiceService {
  id: string
  name: string
  version: string
  license: string
  installed: boolean
  available: boolean
  unavailableReason: string | null
  components?: readonly VoiceComponent[]
  nativeNotices?: readonly string[]
  /** F86 contract 1: fixed notice keys, shown as `t('gui.licenses.notice.<key>')`; empty when the server sends none. */
  noticeKeys?: readonly string[]
  models: readonly VoiceModel[]
}

export type VoiceInstallStep =
  | 'queued' | 'downloading' | 'extracting' | 'testing' | 'complete' | 'cancelling' | 'cancelled' | 'error' | 'interrupted' | string

export interface VoiceInstallJob {
  installId: string
  service: string
  model: string
  step: VoiceInstallStep
  receivedBytes: number
  totalBytes: number
  done: boolean
  error: { code: string; message: string } | null
}

export interface VoiceTranscription {
  text: string
  elapsedMs: number
  service: string
  model: string
}

export interface TurnAudio {
  audioId: string
  mediaType: string
  size: number
  text: string
  /** Authenticated content endpoint; fetched with the session's request, never used as a bare `<audio src>`. */
  url: string
}

export type VoiceAction = 'text' | 'audio'

export interface VoicePrefs {
  action: VoiceAction
  /** Empty strings = use the administrator's default model. */
  service: string
  model: string
  realtime: boolean
}

export const DEFAULT_VOICE_PREFS: VoicePrefs = { action: 'text', service: '', model: '', realtime: false }

export interface VoiceStreamSession {
  streamId: string
  sampleRate: number
  maxSeconds: number
  service: string
  model: string
}

export interface VoiceStreamResult {
  text: string
  final: boolean
  nextChunk: number
  elapsedMs: number
}

/** A refused or failed voice call; `code` is the server's stable error code (voice_disabled, voice_disk_full, …). */
export class VoiceRequestError extends Error {
  constructor(message: string, readonly code: string | null, readonly status: number | null) {
    super(message)
    this.name = 'VoiceRequestError'
  }
}

function toVoiceError(error: unknown): VoiceRequestError {
  if (error instanceof VoiceRequestError) return error
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response
  const status = typeof response?.status === 'number' ? response.status : null
  const body = (response?.data && typeof response.data === 'object' ? response.data : {}) as {
    error?: { code?: unknown; message?: unknown } | string
    detail?: unknown
  }
  const nested = body.error && typeof body.error === 'object' ? body.error : null
  const detail = body.detail && typeof body.detail === 'object' ? (body.detail as { code?: unknown; message?: unknown }) : null
  const code = typeof nested?.code === 'string' ? nested.code
    : typeof detail?.code === 'string' ? detail.code
      : typeof body.error === 'string' ? body.error
        : typeof body.detail === 'string' && /^[a-z_]+$/.test(body.detail) ? body.detail
          : status === null ? 'network' : null
  const message = typeof nested?.message === 'string' ? nested.message
    : typeof detail?.message === 'string' ? detail.message
      : error instanceof Error ? error.message : String(error)
  return new VoiceRequestError(message, code, status)
}

async function call<T>(request: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    throw toVoiceError(error)
  }
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '')
const textOrNull = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)

function toSelection(raw: unknown): VoiceSelection | null {
  const value = raw as { service?: unknown; model?: unknown } | null
  return value && typeof value.service === 'string' && typeof value.model === 'string' && value.service && value.model
    ? { service: value.service, model: value.model }
    : null
}

function toErrorInfo(raw: unknown): { code: string; message: string } | null {
  const value = raw as { code?: unknown; message?: unknown } | null
  return value && typeof value.code === 'string' ? { code: value.code, message: text(value.message) } : null
}

const STATES: readonly VoiceStateName[] = ['not_installed', 'installing', 'off', 'on', 'error']

export function toVoiceStatus(raw: unknown): VoiceStatus {
  const value = (raw ?? {}) as Record<string, unknown>
  const state = STATES.includes(value.state as VoiceStateName) ? (value.state as VoiceStateName) : 'error'
  return {
    enabled: value.enabled === true,
    state,
    defaultSelection: toSelection(value.default),
    error: toErrorInfo(value.error),
    canManage: value.can_manage === true,
    retired: Array.isArray(value.retired) ? value.retired.flatMap((raw) => {
      const r = raw as Record<string, unknown> | null
      return r && typeof r.service === 'string' && typeof r.model === 'string'
        ? [{ service: r.service, model: r.model, name: text(r.name) || r.model, installed: r.installed === true, wasDefault: r.was_default === true }]
        : []
    }) : [],
  }
}

function toModel(raw: Record<string, unknown>): VoiceModel {
  return {
    id: text(raw.id),
    name: text(raw.name) || text(raw.id),
    sizeBytes: Number(raw.size_bytes) || 0,
    installed: raw.installed === true,
    realtime: raw.realtime === true,
    isDefault: raw.is_default === true,
    available: raw.available !== false,
    unavailableReason: textOrNull(raw.unavailable_reason),
    languages: Array.isArray(raw.languages) ? raw.languages.filter((l): l is string => typeof l === 'string') : [],
    gpuRequired: raw.gpu_required === true,
    license: text(raw.license),
    licenseUrl: text(raw.license_url),
    source: text(raw.source),
    sourceUrl: text(raw.source_url),
    defaultEligible: raw.default_eligible !== false,
  }
}

export function toVoiceCatalog(raw: unknown): VoiceService[] {
  const services = (raw as { services?: unknown } | null)?.services
  if (!Array.isArray(services)) return []
  return services.flatMap((entry) => {
    const s = entry as Record<string, unknown> | null
    if (!s || typeof s.id !== 'string') return []
    const models = Array.isArray(s.models)
      ? s.models.flatMap((m) => (m && typeof (m as Record<string, unknown>).id === 'string' ? [toModel(m as Record<string, unknown>)] : []))
      : []
    return [{
      id: s.id,
      name: text(s.name) || s.id,
      version: text(s.version),
      license: text(s.license),
      components: Array.isArray(s.components) ? s.components.map((raw) => {
        const c = raw as Record<string, unknown>
        return {
          filename: text(c.filename), componentType: text(c.component_type), license: text(c.license),
          distribution: text(c.distribution), url: text(c.url), licenseUrl: textOrNull(c.license_url),
        }
      }) : [],
      nativeNotices: Array.isArray(s.native_notices) ? s.native_notices.filter((n): n is string => typeof n === 'string') : [],
      noticeKeys: Array.isArray(s.notice_keys) ? s.notice_keys.filter((k): k is string => typeof k === 'string' && k !== '') : [],
      installed: s.installed === true || models.some((m) => m.installed),
      available: s.available !== false,
      unavailableReason: textOrNull(s.unavailable_reason),
      models,
    }]
  })
}

export function toInstallJob(raw: unknown): VoiceInstallJob {
  const value = (raw ?? {}) as Record<string, unknown>
  return {
    installId: text(value.install_id),
    service: text(value.service),
    model: text(value.model),
    step: text(value.step) || 'queued',
    receivedBytes: Number(value.received_bytes) || 0,
    totalBytes: Number(value.total_bytes) || 0,
    done: value.done === true,
    error: toErrorInfo(value.error),
  }
}

function toTranscription(raw: unknown): VoiceTranscription {
  const value = (raw ?? {}) as Record<string, unknown>
  return { text: text(value.text), elapsedMs: Number(value.elapsed_ms) || 0, service: text(value.service), model: text(value.model) }
}

export function toTurnAudio(raw: unknown): TurnAudio | null {
  const value = raw as Record<string, unknown> | null
  if (!value || typeof value.audio_id !== 'string' || typeof value.url !== 'string') return null
  return { audioId: value.audio_id, mediaType: text(value.media_type) || 'audio/mp4', size: Number(value.size) || 0, text: text(value.text), url: value.url }
}

const enc = encodeURIComponent

// ── Status, catalog, administration ──────────────────────────────────────────────────

export const fetchVoiceStatus = async (): Promise<VoiceStatus> => toVoiceStatus(await call(() => getRequest<unknown>(`${VOICE}/status`)))

export const fetchVoiceCatalog = async (): Promise<VoiceService[]> => toVoiceCatalog(await call(() => getRequest<unknown>(`${VOICE}/catalog`)))

export async function startVoiceInstall(selection: VoiceSelection): Promise<string> {
  const data = await call(() => postRequest<{ install_id?: unknown }>(`${VOICE}/installs`, selection))
  if (typeof data?.install_id !== 'string') throw new VoiceRequestError('install id missing', null, null)
  return data.install_id
}

export const fetchVoiceInstall = async (installId: string): Promise<VoiceInstallJob> =>
  toInstallJob(await call(() => getRequest<unknown>(`${VOICE}/installs/${enc(installId)}`)))

export const cancelVoiceInstall = async (installId: string): Promise<VoiceInstallJob> =>
  toInstallJob(await call(() => deleteRequest<unknown>(`${VOICE}/installs/${enc(installId)}`)))

export async function removeVoiceModel(selection: VoiceSelection): Promise<{ serviceRemoved: boolean; voiceDisabled: boolean }> {
  const data = await call(() => deleteRequest<{ service_removed?: unknown; voice_disabled?: unknown }>(
    `${VOICE}/services/${enc(selection.service)}/models/${enc(selection.model)}`))
  return { serviceRemoved: data?.service_removed === true, voiceDisabled: data?.voice_disabled === true }
}

export const setVoiceDefault = async (selection: VoiceSelection): Promise<VoiceStatus> =>
  toVoiceStatus(await call(() => putRequest<unknown>(`${VOICE}/default`, selection)))

export const setVoiceEnabled = async (enabled: boolean): Promise<VoiceStatus> =>
  toVoiceStatus(await call(() => putRequest<unknown>(`${VOICE}/enabled`, { enabled })))

export const restartVoice = async (): Promise<VoiceStatus> => toVoiceStatus(await call(() => postRequest<unknown>(`${VOICE}/restart`, {})))

// ── Transcription and message audio ──────────────────────────────────────────────────

function audioForm(file: Blob, filename: string, selection?: VoiceSelection | null): FormData {
  const form = new FormData()
  form.append('file', file, filename)
  if (selection?.service && selection.model) {
    form.append('service', selection.service)
    form.append('model', selection.model)
  }
  return form
}

export const transcribeAudio = async (file: Blob, filename: string, selection?: VoiceSelection | null): Promise<VoiceTranscription> =>
  toTranscription(await call(() => postFormRequest<unknown>(`${VOICE}/transcriptions`, audioForm(file, filename, selection))))

/** Administrator test of one installed model; works while voice input is switched off. */
export const testTranscribe = async (file: Blob, filename: string, selection: VoiceSelection): Promise<VoiceTranscription> =>
  toTranscription(await call(() => postFormRequest<unknown>(`${VOICE}/test`, audioForm(file, filename, selection))))

const messageAudioPath = (flowId: string) => `${API}/flows/${enc(flowId)}/message-audio`

export async function uploadMessageAudio(flowId: string, file: Blob, filename: string, selection?: VoiceSelection | null): Promise<{ audio: TurnAudio; elapsedMs: number }> {
  const data = await call(() => postFormRequest<{ audio?: unknown; elapsed_ms?: unknown }>(messageAudioPath(flowId), audioForm(file, filename, selection)))
  const audio = toTurnAudio(data?.audio)
  if (!audio) throw new VoiceRequestError('audio missing', null, null)
  return { audio, elapsedMs: Number(data?.elapsed_ms) || 0 }
}

export async function deleteMessageAudio(flowId: string, audioId: string): Promise<void> {
  await call(() => deleteRequest<unknown>(`${messageAudioPath(flowId)}/${enc(audioId)}`))
}

/** The server lists `url` with its deployment prefix; the request client already carries it. */
export function audioRequestPath(url: string): string {
  const at = url.indexOf(`${API}/`)
  return at >= 0 ? url.slice(at) : url
}

export const fetchMessageAudioBlob = async (url: string): Promise<Blob> => call(() => downloadBlobRequest(audioRequestPath(url)))

// ── Realtime ─────────────────────────────────────────────────────────────────────────

export async function openVoiceStream(selection?: VoiceSelection | null): Promise<VoiceStreamSession> {
  const body = selection?.service && selection.model ? { service: selection.service, model: selection.model } : {}
  const data = await call(() => postRequest<Record<string, unknown>>(`${VOICE}/streams`, body))
  if (typeof data?.stream_id !== 'string') throw new VoiceRequestError('stream id missing', null, null)
  return {
    streamId: data.stream_id,
    sampleRate: Number(data.sample_rate) || 16000,
    maxSeconds: Number(data.max_seconds) || 120,
    service: text(data.service),
    model: text(data.model),
  }
}

export async function sendVoiceChunk(streamId: string, index: number, pcm: Blob, final: boolean): Promise<VoiceStreamResult> {
  const form = new FormData()
  form.append('file', pcm, `chunk-${index}.pcm`)
  const path = `${VOICE}/streams/${enc(streamId)}/chunks?index=${index}${final ? '&final=true' : ''}`
  const data = await call(() => postFormRequest<Record<string, unknown>>(path, form))
  return { text: text(data?.text), final: data?.final === true, nextChunk: Number(data?.next_chunk) || index + 1, elapsedMs: Number(data?.elapsed_ms) || 0 }
}

export async function cancelVoiceStream(streamId: string): Promise<void> {
  await call(() => deleteRequest<unknown>(`${VOICE}/streams/${enc(streamId)}`))
}

// ── Personal settings ────────────────────────────────────────────────────────────────

export function toVoicePrefs(body: UiSettingsResponse | null | undefined): VoicePrefs {
  const s = (body?.settings ?? {}) as Record<string, unknown>
  return {
    action: s.voice_action === 'audio' ? 'audio' : 'text',
    service: text(s.voice_service),
    model: text(s.voice_model),
    realtime: s.voice_realtime === true,
  }
}

export const fetchVoicePrefs = async (): Promise<VoicePrefs> => toVoicePrefs(await call(() => getRequest<UiSettingsResponse>(UI_SETTINGS_PATH)))

/** PATCH only the voice fields given; service and model always travel together. */
export async function saveVoicePrefs(patch: Partial<VoicePrefs>): Promise<VoicePrefs> {
  const body: Record<string, unknown> = {}
  if (patch.action !== undefined) body.voice_action = patch.action
  if (patch.service !== undefined || patch.model !== undefined) {
    body.voice_service = patch.service ?? ''
    body.voice_model = patch.model ?? ''
  }
  if (patch.realtime !== undefined) body.voice_realtime = patch.realtime
  return toVoicePrefs(await call(() => patchRequest<UiSettingsResponse>(UI_SETTINGS_PATH, body)))
}
