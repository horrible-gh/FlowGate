// One voice state per page, shared by the chat composer (mic, ⚙ voice section) and 시스템 › 음성 입력.
// The server is the source of truth: status, catalog and personal settings are read again after every change.
// An installation keeps running on the server when the page closes; its id is kept in this browser so a reopened
// page goes on showing its progress.

import { computed, reactive, readonly } from 'vue'
import {
  DEFAULT_VOICE_PREFS,
  VoiceRequestError,
  cancelVoiceInstall,
  fetchVoiceCatalog,
  fetchVoiceInstall,
  fetchVoicePrefs,
  fetchVoiceStatus,
  removeVoiceModel,
  restartVoice,
  saveVoicePrefs,
  setVoiceDefault,
  setVoiceEnabled,
  startVoiceInstall,
  type VoiceInstallJob,
  type VoiceModel,
  type VoicePrefs,
  type VoiceSelection,
  type VoiceService,
  type VoiceStatus,
} from './api'

export const INSTALL_POLL_MS = 1000
const INSTALL_KEY = 'flowgate.voice.install'

interface VoiceStoreState {
  status: VoiceStatus | null
  catalog: VoiceService[]
  prefs: VoicePrefs
  loading: boolean
  loaded: boolean
  /** The last load failure's code (null after a successful load). */
  loadError: string | null
  /** The installation this page follows (running or just finished with an error). */
  install: VoiceInstallJob | null
  installError: string | null
  /** The model whose installation this page saw finish successfully last. */
  lastInstalled: VoiceSelection | null
  busy: boolean
}

/** What to do once an installation started from this page succeeds. */
export type AfterInstall = 'default' | 'personal' | null

const state = reactive<VoiceStoreState>({
  status: null,
  catalog: [],
  prefs: { ...DEFAULT_VOICE_PREFS },
  loading: false,
  loaded: false,
  loadError: null,
  install: null,
  installError: null,
  lastInstalled: null,
  busy: false,
})

let loading: Promise<void> | null = null
let pollTimer: ReturnType<typeof setTimeout> | null = null
let afterInstall: { installId: string; then: AfterInstall } | null = null

const codeOf = (error: unknown): string => (error instanceof VoiceRequestError ? error.code ?? 'failed' : 'failed')

export const sameSelection = (a: VoiceSelection | null | undefined, b: VoiceSelection | null | undefined): boolean =>
  !!a && !!b && a.service === b.service && a.model === b.model

export function findService(catalog: readonly VoiceService[], serviceId: string): VoiceService | null {
  return catalog.find((s) => s.id === serviceId) ?? null
}

export function findModel(catalog: readonly VoiceService[], selection: VoiceSelection | null | undefined): VoiceModel | null {
  if (!selection) return null
  return findService(catalog, selection.service)?.models.find((m) => m.id === selection.model) ?? null
}

/**
 * The model this person's recordings go to: their own pick while it is installed, otherwise the administrator's
 * default (the server resolves it the same way).
 */
export function effectiveSelection(prefs: VoicePrefs, status: VoiceStatus | null, catalog: readonly VoiceService[]): VoiceSelection | null {
  const own = prefs.service && prefs.model ? { service: prefs.service, model: prefs.model } : null
  if (own && findModel(catalog, own)?.installed) return own
  const fallback = status?.defaultSelection ?? null
  return fallback && findModel(catalog, fallback)?.installed !== false ? fallback : null
}

/** Realtime is used only when switched on and the model in use supports it. */
export function realtimeActive(prefs: VoicePrefs, model: VoiceModel | null): boolean {
  return prefs.action === 'text' && prefs.realtime && !!model?.realtime
}

/** A short label for a model in tight spots (the recording pill, the mic tooltip). */
export function shortModelName(model: VoiceModel | null, selection: VoiceSelection | null): string {
  const name = model?.name ?? selection?.model ?? ''
  return name.replace(/^Streaming\s+/i, '').replace(/\s+int8$/i, '')
}

function rememberInstall(id: string | null): void {
  try {
    if (id) localStorage.setItem(INSTALL_KEY, id)
    else localStorage.removeItem(INSTALL_KEY)
  } catch { /* storage unavailable: progress is followed for this page only */ }
}

function rememberedInstall(): string | null {
  try { return localStorage.getItem(INSTALL_KEY) } catch { return null }
}

async function readAll(): Promise<void> {
  const [status, catalog, prefs] = await Promise.all([
    fetchVoiceStatus(),
    fetchVoiceCatalog(),
    fetchVoicePrefs().catch(() => state.prefs),
  ])
  state.status = status
  state.catalog = catalog
  state.prefs = prefs
  state.loaded = true
  state.loadError = null
}

/** Reads status, catalog and personal settings (once, unless `force`). Never throws; see `loadError`. */
export function loadVoice(force = false): Promise<void> {
  if (loading) return loading
  if (state.loaded && !force) return Promise.resolve()
  state.loading = true
  loading = readAll()
    .catch((error) => { state.loadError = codeOf(error) })
    .finally(() => {
      state.loading = false
      loading = null
      if (state.status?.canManage) resumeInstall()
    })
  return loading
}

function schedulePoll(id: string): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = setTimeout(() => { void poll(id) }, INSTALL_POLL_MS)
}

async function poll(id: string): Promise<void> {
  pollTimer = null
  try {
    const job = await fetchVoiceInstall(id)
    state.install = job
    if (!job.done) { schedulePoll(id); return }
    rememberInstall(null)
    const then = afterInstall?.installId === id ? afterInstall.then : null
    afterInstall = null
    state.installError = job.error && job.error.code !== 'voice_install_cancelled' ? job.error.code : null
    if (!state.installError) state.install = null
    await loadVoice(true)
    if (!job.error) await applyAfterInstall({ service: job.service, model: job.model }, then)
  } catch (error) {
    if (codeOf(error) === 'voice_install_not_found') {
      rememberInstall(null)
      state.install = null
      return
    }
    schedulePoll(id)
  }
}

async function applyAfterInstall(selection: VoiceSelection, then: AfterInstall): Promise<void> {
  try {
    if (then === 'personal') {
      await updatePrefs({ ...selection, realtime: !!findModel(state.catalog, selection)?.realtime })
    } else if (then === 'default' && !sameSelection(state.status?.defaultSelection, selection)) {
      await chooseDefault(selection)
    }
  } catch (error) {
    state.installError = codeOf(error)
  }
  state.lastInstalled = selection
}

/** Picks up an installation started earlier (this page or another one) and follows it. */
function resumeInstall(): void {
  const id = state.install && !state.install.done ? state.install.installId : rememberedInstall()
  if (id && !pollTimer) void poll(id)
}

async function run<T>(work: () => Promise<T>): Promise<T> {
  state.busy = true
  try { return await work() } finally { state.busy = false }
}

/**
 * Administrator: starts installing a model (the service's program comes with its first model). `then` = what to do
 * when it succeeds: make it the system default, or this person's own pick. (The very first model becomes the default
 * and switches voice input on by itself, on the server.)
 */
export async function installModel(selection: VoiceSelection, then: AfterInstall = null): Promise<void> {
  state.installError = null
  state.lastInstalled = null
  const id = await run(() => startVoiceInstall(selection))
  rememberInstall(id)
  afterInstall = { installId: id, then: then === 'default' && findModel(state.catalog, selection)?.defaultEligible === false ? null : then }
  state.install = { installId: id, service: selection.service, model: selection.model, step: 'queued', receivedBytes: 0, totalBytes: 0, done: false, error: null }
  if (state.status) state.status = { ...state.status, state: 'installing' }
  schedulePoll(id)
}

export async function cancelInstall(): Promise<void> {
  const job = state.install
  if (!job || job.done) return
  state.install = { ...job, step: 'cancelling' }
  try { await cancelVoiceInstall(job.installId) } finally { if (!pollTimer) schedulePoll(job.installId) }
}

export function dismissInstallError(): void {
  state.installError = null
  if (state.install?.done) state.install = null
}

export async function deleteModel(selection: VoiceSelection): Promise<{ serviceRemoved: boolean; voiceDisabled: boolean }> {
  try { return await run(() => removeVoiceModel(selection)) } finally { await loadVoice(true) }
}

export async function chooseDefault(selection: VoiceSelection): Promise<void> {
  try { state.status = await run(() => setVoiceDefault(selection)) } finally { await loadVoice(true) }
}

export async function switchVoice(enabled: boolean): Promise<void> {
  try { state.status = await run(() => setVoiceEnabled(enabled)) } finally { await loadVoice(true) }
}

export async function restartVoiceModule(): Promise<void> {
  try { state.status = await run(() => restartVoice()) } finally { await loadVoice(true) }
}

/** Saves this person's voice settings; the saved answer replaces the shown ones. */
export async function updatePrefs(patch: Partial<VoicePrefs>): Promise<void> {
  state.prefs = await run(() => saveVoicePrefs(patch))
}

/** Test seam: forget everything (and stop following an installation). */
export function resetVoiceStore(): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
  loading = null
  afterInstall = null
  Object.assign(state, { status: null, catalog: [], prefs: { ...DEFAULT_VOICE_PREFS }, loading: false, loaded: false, loadError: null, install: null, installError: null, lastInstalled: null, busy: false })
}

export function useVoice() {
  const selection = computed(() => effectiveSelection(state.prefs, state.status, state.catalog))
  const model = computed(() => findModel(state.catalog, selection.value))
  const installedModels = computed(() => state.catalog.flatMap((s) => s.models.filter((m) => m.installed).map((m) => ({ service: s, model: m }))))
  return { state: readonly(state), selection, model, installedModels }
}
