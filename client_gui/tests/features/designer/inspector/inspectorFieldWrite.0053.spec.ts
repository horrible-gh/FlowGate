/**
 * flowgate.v02.0053 T#7 — the Inspector's common write of one value (useInspectorFieldWrite) with the
 * resource it answers into (useInspectorResource.replace): each change sent at once, its value pending (shown)
 * until answered, changes one after another in the order made, a refusal kept per field and the resource read
 * again, a new target dropping the pending values and the refusal, an answer for an earlier target not applied.
 * The same field changed again before an answer: only the answer to the field's latest change settles its pending
 * value or failure — on → off → on with every answer held keeps the last on shown, and an earlier change refused while
 * the next one waits leaves no failure once that next one is taken.
 */
import { flushPromises } from '@vue/test-utils'
import { effectScope, ref } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { useInspectorFieldWrite, useInspectorResource, type InspectorEventScope } from '@/features/designer/inspector/useInspectorResource'

interface Settings {
  readonly values: Readonly<Record<string, unknown>>
}

function setup(write: (key: string, field: string, value: unknown) => Promise<Settings>, read = vi.fn(async (key: string) => ({ values: { key } }) as Settings)) {
  const key = ref<string | null>('f\nn1')
  const scope = (): InspectorEventScope | null => (key.value ? { projectId: 'p', flowId: 'f', docId: key.value } : null)
  const effect = effectScope()
  const made = effect.run(() => {
    const resource = useInspectorResource<Settings>({ target: scope, read, events: [] })
    const writes = useInspectorFieldWrite<Settings>({ target: scope, resource, write })
    return { resource, writes }
  })!
  return { key, read, effect, ...made }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useInspectorFieldWrite (flowgate.v02.0053 T#7)', () => {
  it('sends a value at once; it is pending until the answer replaces the resource', async () => {
    const answer = deferred<Settings>()
    const write = vi.fn(() => answer.promise)
    const { resource, writes, effect } = setup(write)
    await flushPromises()
    const sent = writes.write('restart', 2)
    // shown at once; sent as soon as nothing earlier waits
    expect(writes.pending.value).toEqual({ restart: 2 })
    await flushPromises()
    expect(write).toHaveBeenCalledWith('f\nn1', 'restart', 2)
    expect(writes.busy.value).toBe(true)
    answer.resolve({ values: { restart: 2 } })
    expect(await sent).toBe(true)
    expect(writes.pending.value).toEqual({})
    expect(writes.busy.value).toBe(false)
    expect(resource.data.value).toEqual({ values: { restart: 2 } })
    expect(resource.status.value).toBe('ready')
    effect.stop()
  })

  it('changes go one after another in the order made; a later value of the same field stays pending', async () => {
    const answers = [deferred<Settings>(), deferred<Settings>()]
    const write = vi.fn().mockImplementationOnce(() => answers[0].promise).mockImplementationOnce(() => answers[1].promise)
    const { writes, resource, effect } = setup(write)
    await flushPromises()
    void writes.write('restart', 1)
    void writes.write('restart', 2)
    await flushPromises()
    expect(write).toHaveBeenCalledTimes(1)
    expect(writes.pending.value).toEqual({ restart: 2 })
    answers[0].resolve({ values: { restart: 1 } })
    await flushPromises()
    expect(write).toHaveBeenCalledTimes(2)
    expect(write.mock.calls[1]).toEqual(['f\nn1', 'restart', 2])
    expect(writes.pending.value).toEqual({ restart: 2 })
    expect(resource.data.value).toEqual({ values: { restart: 1 } })
    answers[1].resolve({ values: { restart: 2 } })
    await flushPromises()
    expect(writes.pending.value).toEqual({})
    expect(resource.data.value).toEqual({ values: { restart: 2 } })
    effect.stop()
  })

  it('a refusal keeps the field and what was thrown, reads the resource again; the next write of that field clears it', async () => {
    const refusal = { response: { status: 409 } }
    const write = vi.fn().mockRejectedValueOnce(refusal).mockResolvedValueOnce({ values: { restart: 1 } })
    const { writes, read, effect } = setup(write)
    await flushPromises()
    expect(await writes.write('restart', 1)).toBe(false)
    expect(writes.failure.value).toEqual({ field: 'restart', error: refusal })
    expect(writes.pending.value).toEqual({})
    expect(read).toHaveBeenCalledTimes(2)
    // a write of another field keeps it; one of the same field clears it
    void writes.write('review_count', 1)
    expect(writes.failure.value?.field).toBe('restart')
    expect(await writes.write('restart', 1)).toBe(true)
    expect(writes.failure.value).toBeNull()
    effect.stop()
  })

  it('the same field changed again before its answers (on → off → on): an earlier answer keeps the latest value pending', async () => {
    const answers = [deferred<Settings>(), deferred<Settings>(), deferred<Settings>()]
    const write = vi
      .fn()
      .mockImplementationOnce(() => answers[0].promise)
      .mockImplementationOnce(() => answers[1].promise)
      .mockImplementationOnce(() => answers[2].promise)
    const { writes, resource, effect } = setup(write)
    await flushPromises()
    const sent = [writes.write('push', true), writes.write('push', false), writes.write('push', true)]
    expect(writes.pending.value).toEqual({ push: true })
    await flushPromises()
    // the first answer (true) is not the latest change: the last true stays pending
    answers[0].resolve({ values: { push: true } })
    await flushPromises()
    expect(write).toHaveBeenCalledTimes(2)
    expect(writes.pending.value).toEqual({ push: true })
    expect(writes.busy.value).toBe(true)
    // the second answer (false) replaces the data, but the last true is still on its way and is what is shown
    answers[1].resolve({ values: { push: false } })
    await flushPromises()
    expect(write).toHaveBeenCalledTimes(3)
    expect(resource.data.value).toEqual({ values: { push: false } })
    expect(writes.pending.value).toEqual({ push: true })
    expect(writes.busy.value).toBe(true)
    answers[2].resolve({ values: { push: true } })
    expect(await Promise.all(sent)).toEqual([true, true, true])
    expect(writes.pending.value).toEqual({})
    expect(writes.busy.value).toBe(false)
    expect(resource.data.value).toEqual({ values: { push: true } })
    expect(write.mock.calls.map(([, field, value]) => [field, value])).toEqual([
      ['push', true],
      ['push', false],
      ['push', true],
    ])
    effect.stop()
  })

  it('an earlier change refused while the field’s next change waits: no failure is kept over it, and its success shows none', async () => {
    const answers = [deferred<Settings>(), deferred<Settings>()]
    const write = vi
      .fn()
      .mockImplementationOnce(() => answers[0].promise)
      .mockImplementationOnce(() => answers[1].promise)
    const { writes, read, resource, effect } = setup(write)
    await flushPromises()
    const first = writes.write('push', true)
    const second = writes.write('push', false)
    await flushPromises()
    answers[0].reject({ response: { status: 409 } })
    expect(await first).toBe(false)
    // read again after the refusal; the next change of the field is still the value shown
    expect(read).toHaveBeenCalledTimes(2)
    expect(writes.failure.value).toBeNull()
    expect(writes.pending.value).toEqual({ push: false })
    await flushPromises()
    expect(write).toHaveBeenCalledTimes(2)
    answers[1].resolve({ values: { push: false } })
    expect(await second).toBe(true)
    expect(writes.failure.value).toBeNull()
    expect(writes.pending.value).toEqual({})
    expect(resource.data.value).toEqual({ values: { push: false } })
    effect.stop()
  })

  it('the latest change refused keeps its failure; another field’s success keeps it, the field’s next change clears it', async () => {
    const answers = [deferred<Settings>(), deferred<Settings>(), deferred<Settings>()]
    const write = vi
      .fn()
      .mockImplementationOnce(() => answers[0].promise)
      .mockImplementationOnce(() => answers[1].promise)
      .mockImplementationOnce(() => answers[2].promise)
    const { writes, effect } = setup(write)
    await flushPromises()
    const refused = writes.write('push', true)
    await flushPromises()
    const refusal = { response: { status: 422 } }
    answers[0].reject(refusal)
    expect(await refused).toBe(false)
    expect(writes.failure.value).toEqual({ field: 'push', error: refusal })
    expect(writes.pending.value).toEqual({})
    // another field's success keeps it
    const other = writes.write('keep_branch', true)
    await flushPromises()
    answers[1].resolve({ values: { keep_branch: true } })
    expect(await other).toBe(true)
    expect(writes.failure.value).toEqual({ field: 'push', error: refusal })
    // the field's next change clears it as soon as it is made, and its success leaves none
    const again = writes.write('push', false)
    expect(writes.failure.value).toBeNull()
    await flushPromises()
    answers[2].resolve({ values: { push: false } })
    expect(await again).toBe(true)
    expect(writes.failure.value).toBeNull()
    effect.stop()
  })

  it('a new target drops the pending values and the refusal; an answer for the earlier target is not applied', async () => {
    const answer = deferred<Settings>()
    const write = vi.fn().mockRejectedValueOnce({}).mockImplementationOnce(() => answer.promise)
    const { writes, resource, key, effect } = setup(write)
    await flushPromises()
    await writes.write('restart', 1)
    expect(writes.failure.value).not.toBeNull()
    const sent = writes.write('time_limit', 120)
    key.value = 'f\nn2'
    await flushPromises()
    expect(writes.failure.value).toBeNull()
    expect(writes.pending.value).toEqual({})
    answer.resolve({ values: { stale: true } })
    expect(await sent).toBe(false)
    expect(resource.data.value).toEqual({ values: { key: 'f\nn2' } })
    effect.stop()
  })

  it('nothing to write without a target', async () => {
    const write = vi.fn()
    const { writes, key, effect } = setup(write)
    key.value = null
    await flushPromises()
    expect(await writes.write('restart', 1)).toBe(false)
    expect(write).not.toHaveBeenCalled()
    effect.stop()
  })
})
