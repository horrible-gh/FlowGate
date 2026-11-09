/**
 * flowgate.v02.0156 T5 (E1): the consumed record is separate from the observation dedupe. Light unit checks only.
 */
import { describe, expect, it } from 'vitest'
import { createRunFailurePublisher, resolveSessionStorage } from '@/features/designer/shell/runFailureToast'
import type { FlowRunFailure } from '@/features/designer/center/run/flowRunControl'

const f = (over: Record<string, unknown> = {}) => ({
  kind: 'run', seq: 1, status: 503, code: 'mystery', message: 'same', approval: null, refusal: null,
  scope: 's1', publicationId: 's1:failure:1', ...over,
}) as unknown as FlowRunFailure
const memory = () => { const m = new Map<string, string>(); return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m } }

describe('0156 T5 E1 consumed record', () => {
  it('a) a shown failure is not accepted again after clear() (tab round trip)', () => {
    const p = createRunFailurePublisher()
    const first = f()
    expect(p.accept(first)).toBe(true)
    p.clear()
    expect(p.accept(first)).toBe(false)
    expect(p.accept({ ...first })).toBe(false)
  })
  it('b) a new publicationId is accepted even with the same code and message', () => {
    const p = createRunFailurePublisher()
    expect(p.accept(f())).toBe(true)
    p.clear()
    expect(p.accept(f({ publicationId: 's1:failure:2' }))).toBe(true)
    expect(p.accept(f({ scope: 's2' }))).toBe(true)
  })
  it('c) policy: shown (closed or auto-hidden or still open) is consumed; observation clear only resets dedupe', () => {
    const p = createRunFailurePublisher()
    const a = f()
    expect(p.accept(a)).toBe(true); expect(p.accept(a)).toBe(false)
    p.clear(); p.clear()
    expect(p.accept(a)).toBe(false)
  })
  it('d) the record is bounded and persisted records are owner-separated', () => {
    const p = createRunFailurePublisher()
    for (let i = 0; i < 70; i += 1) expect(p.accept(f({ publicationId: `s1:failure:${i}` }))).toBe(true)
    p.clear() // observation dedupe is separate; only the bounded consumed record can still refuse
    expect(p.accept(f({ publicationId: 's1:failure:0' }))).toBe(true) // oldest evicted
    expect(p.accept(f({ publicationId: 's1:failure:69' }))).toBe(false)
    const storage = memory(); let owner: string | null = 'kim'
    const kim = createRunFailurePublisher({ storage, owner: () => owner })
    const pending = (scope: string) => f({ scope, publicationId: `${scope}:pending:c1`, commandRequestId: 'c1', outcome: 'unconfirmed', stage: 'command_lookup' })
    expect(kim.accept(pending('a'))).toBe(true)
    owner = 'lee'
    expect(createRunFailurePublisher({ storage, owner: () => owner }).accept(pending('b'))).toBe(true)
    owner = 'kim'
    expect(createRunFailurePublisher({ storage, owner: () => owner }).accept(pending('c'))).toBe(false) // reload, same owner
    owner = null
    expect(createRunFailurePublisher({ storage, owner: () => owner }).accept(pending('d'))).toBe(true)
  })
  it('e) a real new failure after reload still shows: unconfirmed start retry, rejected, approval refusal', () => {
    const storage = memory(); const owner = () => 'kim'
    const a = createRunFailurePublisher({ storage, owner })
    expect(a.accept(f({ commandRequestId: 'c1', outcome: 'unconfirmed', stage: 'start' }))).toBe(true)
    const b = createRunFailurePublisher({ storage, owner })
    expect(b.accept(f({ scope: 'x', publicationId: 'x:failure:1', commandRequestId: 'c1', outcome: 'unconfirmed', stage: 'start' }))).toBe(true)
    expect(b.accept(f({ scope: 'x', publicationId: 'x:failure:2', outcome: 'rejected', stage: 'start' }))).toBe(true)
    expect(b.accept(f({ scope: 'x', publicationId: 'x:failure:3', kind: 'approval' }))).toBe(true)
  })
  it('g) a denied sessionStorage getter yields memory-only storage and the first failure is still shown once', () => {
    Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, get() { throw new DOMException('denied', 'SecurityError') } })
    try {
      let storage: ReturnType<typeof resolveSessionStorage> = memory()
      expect(() => { storage = resolveSessionStorage() }).not.toThrow()
      expect(storage).toBeNull()
      const p = createRunFailurePublisher({ storage, owner: () => 'kim' })
      const first = f({ commandRequestId: 'c1', outcome: 'unconfirmed', stage: 'command_lookup', publicationId: 's1:pending:c1' })
      expect(p.accept(first)).toBe(true)
      p.clear()
      expect(p.accept(first)).toBe(false)
    } finally { delete (globalThis as Record<string, unknown>).sessionStorage }
  })
  it('h) storage methods that throw keep the memory consumed record and the first display', () => {
    const broken = { getItem: () => { throw new DOMException('denied', 'SecurityError') }, setItem: () => { throw new DOMException('quota', 'QuotaExceededError') } }
    const p = createRunFailurePublisher({ storage: broken, owner: () => 'kim' })
    const first = f({ commandRequestId: 'c1', outcome: 'unconfirmed', stage: 'command_lookup', publicationId: 's1:pending:c1' })
    expect(p.accept(first)).toBe(true)
    p.clear()
    expect(p.accept(first)).toBe(false)
    expect(p.accept(f({ publicationId: 's1:failure:9' }))).toBe(true)
  })
  it('f) legacy failures without keys keep object identity policy, and clear() resets it', () => {
    const p = createRunFailurePublisher()
    const legacy = { kind: 'run', seq: 1, status: null, code: null, message: '', approval: null, refusal: null } as unknown as FlowRunFailure
    expect(p.accept(legacy)).toBe(true); expect(p.accept(legacy)).toBe(false); expect(p.accept({ ...legacy })).toBe(true)
    p.clear(); expect(p.accept(legacy)).toBe(true)
  })
})
