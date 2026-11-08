/** Flow-lifetime pending identity. Age changes the notice and retries, never identity or retention. */
// flowgate.v02.0156 T1 R1: the default identity source works on an insecure LAN origin too (Web Crypto randomUUID is
// secure-context only). The explicit relative `.ts` path resolves in Vite, vitest, vue-tsc and Node type stripping.
import { randomUuid } from '../../../../../../client/shared/utils/uuid.ts'
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
/**
 * 0156 T1 R4: an identity that may have been sent. sending() journals it under its OWN key (one key per identity, so no
 * tab ever rewrites another tab's entry) before the start POST may go; only a server-confirmed resolution (accepted
 * answer, found, voided, flow gone, a first-attempt refusal) removes it. Looked up only, never resent from the journal.
 */
export interface RunCommandOrphan { commandRequestId: string; mode: 'step' | 'full'; createdAt: number }
/** Unresolved journaled identities of one scope at which a new identity is refused. Nothing is ever evicted. */
export const RUN_COMMAND_ORPHAN_LIMIT = 20
export type CommandRecordRead = { kind: 'empty' } | { kind: 'pending'; record: RunCommandRecord } | { kind: 'corrupt' | 'unavailable' }
export interface CommandStorage {
  getItem(key: string): string | null; setItem(key: string, value: string): void; removeItem(key: string): void
  /** Enumeration (Web Storage) — the journal holds one key per identity. */
  readonly length: number; key(index: number): string | null
}
/** The body's check right before it sends (an injected cross-tab lock renews or refuses there). */
export type CommandFence = () => Promise<void>
const part = (s: string) => encodeURIComponent(s).replace(/\./g, '%2E')
// Deliberately outside flowgate.user.{username}.*, which logout removes.
export const runCommandKey = (s: RunCommandScope) => `flowgate.runCommand.v1.${part(s.username)}.${part(s.projectId)}.${part(s.flowId)}`
/** Journal key prefix of a scope; each journaled identity is `<prefix><commandRequestId>`. */
export const runCommandOrphanKey = (s: RunCommandScope) => `flowgate.runCommandOrphan.v1.${part(s.username)}.${part(s.projectId)}.${part(s.flowId)}.`
const orphanEntryKey = (s: RunCommandScope, cid: string) => runCommandOrphanKey(s) + cid.toLowerCase()
/** 0156 T1 R2: a store refusal carries its code (the toast names it); the original exception's name only. */
function refusal(code: string, cause?: unknown): Error {
  const name = (cause as { name?: unknown } | null)?.name
  return Object.assign(new Error(''), { code }, typeof name === 'string' ? { errorName: name } : {})
}
export function createRunCommandStore(options: {
  storage?: () => CommandStorage
  now?: () => number
  uuid?: () => string
  lock?: <T>(key: string, body: (fence?: CommandFence) => Promise<T>) => Promise<T>
} = {}) {
  const storage = options.storage ?? (() => globalThis.localStorage)
  const now = options.now ?? Date.now
  const uuid = options.uuid ?? randomUuid
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
  /**
   * Every journaled identity of the scope, oldest first. The key names the identity, so even unreadable entry bytes keep
   * it (mode unknown -> 'step', age unknown -> 0). Unreadable storage lists nothing: the record read then refuses sending.
   */
  function orphans(scope: RunCommandScope): RunCommandOrphan[] {
    const prefix = runCommandOrphanKey(scope)
    const list: RunCommandOrphan[] = []
    try {
      const s = storage()
      const keys: string[] = []
      for (let i = 0; i < s.length; i++) { const k = s.key(i); if (k !== null && k.startsWith(prefix)) keys.push(k) }
      for (const k of keys) {
        const cid = k.slice(prefix.length)
        if (!COMMAND_UUID.test(cid)) continue
        let entry: Partial<RunCommandOrphan> | null = null
        try { entry = JSON.parse(s.getItem(k) ?? 'null') as Partial<RunCommandOrphan> | null } catch { entry = null }
        list.push({ commandRequestId: cid.toLowerCase(),
          mode: entry && (entry.mode === 'step' || entry.mode === 'full') ? entry.mode : 'step',
          createdAt: entry && Number.isFinite(entry.createdAt) && (entry.createdAt as number) >= 0 ? entry.createdAt as number : 0 })
      }
    } catch { return [] }
    return list.sort((x, y) => x.createdAt - y.createdAt)
  }
  /** Journals an identity under its own key (idempotent). Throws when storage refuses: the caller must not send. */
  function keepOrphan(record: RunCommandRecord) {
    const key = orphanEntryKey(record, record.commandRequestId)
    if (storage().getItem(key) === null) {
      storage().setItem(key, JSON.stringify({ commandRequestId: record.commandRequestId, mode: record.mode, createdAt: record.createdAt }))
    }
  }
  /** Only a server-confirmed resolution (accepted, found, voided, flow gone, first-attempt refusal) drops an identity. */
  function dropOrphan(scope: RunCommandScope, cid: string) {
    storage().removeItem(orphanEntryKey(scope, cid))
  }
  function clear(scope: RunCommandScope, cid: string) {
    const current = read(scope)
    if (current.kind === 'pending' && current.record.commandRequestId === cid) storage().removeItem(runCommandKey(scope))
  }
  function sending(scope: RunCommandScope, mode: 'step' | 'full'): RunCommandRecord {
    const current = read(scope)
    if (current.kind === 'corrupt' || current.kind === 'unavailable') throw refusal('run_command_storage_unavailable')
    if (current.kind === 'pending' && current.record.mode !== mode) throw refusal('run_command_mode_requires_void')
    const r: RunCommandRecord = current.kind === 'pending'
      ? { ...current.record, attempts: current.record.attempts + 1, state: 'sending' }
      : { ...scope, v: 1, commandRequestId: uuid().toLowerCase(), mode, createdAt: now(), attempts: 1, state: 'sending' }
    if (!COMMAND_UUID.test(r.commandRequestId)) throw refusal('run_command_invalid_identity')
    // 0156 T1 R4: a new identity waits while too many earlier ones are unresolved; none of them is ever evicted for it.
    if (current.kind === 'empty' && orphans(scope).length >= RUN_COMMAND_ORPHAN_LIMIT) throw refusal('run_command_unresolved')
    // Must succeed before any start POST.
    try { put(r) } catch (error) { throw refusal('run_command_storage_unavailable', error) }
    // 0156 T1 R4 compare-and-set: another tab's identity written meanwhile wins; this one was never sent.
    const stored = read(scope)
    if (stored.kind !== 'pending' || stored.record.commandRequestId !== r.commandRequestId) throw refusal('run_command_busy')
    // 0156 T1 R4: journaled under its own key before it may be sent, so no other tab's record write can lose it.
    try { keepOrphan(r) } catch (error) { throw refusal('run_command_storage_unavailable', error) }
    return r
  }
  function retain(record: RunCommandRecord, legacy = false) {
    const current = read(record)
    if (current.kind === 'pending' && current.record.commandRequestId === record.commandRequestId)
      put({ ...current.record, state: legacy ? 'legacy_unconfirmed' : 'unconfirmed' })
    // 0156 T1 R4: another tab replaced the record of this sent identity; its journal entry keeps it for lookup.
    else keepOrphan(record)
  }
  /** The body gets a fence to call right before sending: the injected lock's, else a no-op (Web Locks never expire). */
  async function withLock<T>(scope: RunCommandScope, body: (fence: CommandFence) => Promise<T>): Promise<T> {
    const open: CommandFence = async () => {}
    if (options.lock) return options.lock(runCommandKey(scope), (fence) => body(fence ?? open))
    if (globalThis.navigator?.locks) return navigator.locks.request(runCommandKey(scope), () => body(open))
    return body(open)
  }
  return { read, put, clear, sending, retain, withLock, now, orphans, dropOrphan,
    // Only the explicit UI acknowledgement may discard corrupt data.
    discardCorrupt(scope: RunCommandScope) { if (read(scope).kind === 'corrupt') storage().removeItem(runCommandKey(scope)) },
  }
}
export type RunCommandStore = ReturnType<typeof createRunCommandStore>
