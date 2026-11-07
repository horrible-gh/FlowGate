/** Flow-lifetime pending identity. Age changes the notice and retries, never identity or retention. */
export const STALE_AFTER_MS = 10 * 60 * 1000
export const COMMAND_UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i
export interface RunCommandScope { username: string; projectId: string; flowId: string }
export interface RunCommandRecord extends RunCommandScope {
  v: 1
  commandRequestId: string
  mode: 'step' | 'full'
  createdAt: number
  attempts: number
  state: 'sending' | 'unconfirmed' | 'legacy_unconfirmed'
}
export type CommandRecordRead = { kind: 'empty' } | { kind: 'pending'; record: RunCommandRecord } | { kind: 'corrupt' | 'unavailable' }
export interface CommandStorage { getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void }
const part = (s: string) => encodeURIComponent(s).replace(/\./g, '%2E')
// Deliberately outside flowgate.user.{username}.*, which logout removes.
export const runCommandKey = (s: RunCommandScope) => `flowgate.runCommand.v1.${part(s.username)}.${part(s.projectId)}.${part(s.flowId)}`
export function createRunCommandStore(options: {
  storage?: () => CommandStorage
  now?: () => number
  uuid?: () => string
  lock?: <T>(key: string, body: () => Promise<T>) => Promise<T>
} = {}) {
  const storage = options.storage ?? (() => globalThis.localStorage)
  const now = options.now ?? Date.now
  function read(scope: RunCommandScope): CommandRecordRead {
    let text: string | null
    try { text = storage().getItem(runCommandKey(scope)) } catch { return { kind: 'unavailable' } }
    if (text === null) return { kind: 'empty' }
    try {
      const r = JSON.parse(text) as RunCommandRecord
      if (!r || typeof r !== 'object' || Array.isArray(r)
        || Object.keys(r).some(key => !['v', 'commandRequestId', 'username', 'projectId', 'flowId', 'mode', 'createdAt', 'attempts', 'state'].includes(key))
        || r.v !== 1 || !COMMAND_UUID.test(r.commandRequestId) || r.username !== scope.username
        || r.projectId !== scope.projectId || r.flowId !== scope.flowId || !['step', 'full'].includes(r.mode)
        || !Number.isFinite(r.createdAt) || r.createdAt < 0 || !Number.isSafeInteger(r.attempts) || r.attempts < 1
        || !['sending', 'unconfirmed', 'legacy_unconfirmed'].includes(r.state)) return { kind: 'corrupt' }
      return { kind: 'pending', record: { ...r, commandRequestId: r.commandRequestId.toLowerCase() } }
    } catch { return { kind: 'corrupt' } }
  }
  function put(record: RunCommandRecord) { storage().setItem(runCommandKey(record), JSON.stringify(record)) }
  function clear(scope: RunCommandScope, cid: string) {
    const current = read(scope)
    if (current.kind === 'pending' && current.record.commandRequestId === cid) storage().removeItem(runCommandKey(scope))
  }
  function sending(scope: RunCommandScope, mode: 'step' | 'full'): RunCommandRecord {
    const current = read(scope)
    if (current.kind === 'corrupt' || current.kind === 'unavailable') throw new Error('run_command_storage_unavailable')
    if (current.kind === 'pending' && current.record.mode !== mode) throw new Error('run_command_mode_requires_void')
    const r: RunCommandRecord = current.kind === 'pending'
      ? { ...current.record, attempts: current.record.attempts + 1, state: 'sending' }
      : { ...scope, v: 1, commandRequestId: (options.uuid ?? (() => crypto.randomUUID()))().toLowerCase(), mode, createdAt: now(), attempts: 1, state: 'sending' }
    if (!COMMAND_UUID.test(r.commandRequestId)) throw new Error('run_command_invalid_identity')
    put(r) // Must succeed before any start POST.
    return r
  }
  function retain(record: RunCommandRecord, legacy = false) {
    const current = read(record)
    if (current.kind === 'pending' && current.record.commandRequestId === record.commandRequestId)
      put({ ...current.record, state: legacy ? 'legacy_unconfirmed' : 'unconfirmed' })
  }
  async function withLock<T>(scope: RunCommandScope, body: () => Promise<T>): Promise<T> {
    if (options.lock) return options.lock(runCommandKey(scope), body)
    if (globalThis.navigator?.locks) return navigator.locks.request(runCommandKey(scope), body)
    return body()
  }
  return { read, put, clear, sending, retain, withLock, now,
    // Only the explicit UI acknowledgement may discard corrupt data.
    discardCorrupt(scope: RunCommandScope) { if (read(scope).kind === 'corrupt') storage().removeItem(runCommandKey(scope)) },
  }
}
export type RunCommandStore = ReturnType<typeof createRunCommandStore>
