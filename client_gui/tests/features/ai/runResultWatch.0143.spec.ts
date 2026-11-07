import { describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { toFlowCommandResult } from '@/features/project/commandResultApi'
import { parseDesignerSession } from '@/features/project/designerSessionStorage'
import { createRunResultWatch, readFailureKind, runResultKey, RUN_RESULT_RETRY } from '@/features/ai/runResult/runResultWatch'

// flowgate.v02.0143 T#1 (NR flowgate.v02.0143.0003 §7 격리 시나리오, T §3 R1~R7) — the real createRunResultWatch driven by a
// stand-in load (the server's command result) and a stand-in timer (`schedule`): when the result window opens, a read
// that fails, a past result read while the run went on again, another command's answer and an F5.

function raw(over: Record<string, unknown> = {}) {
  return {
    run_id: 'run_1', flow_id: 'f', command_seq: 5, latest: true, scope: 'full', started_at: '2026-11-07T00:00:00.000+00:00',
    ended_at: '2026-11-07T00:00:01.000+00:00', ended: true, end_seq: 9, state: 'completed', idle_reason: 'completed',
    next_state: 'completed', counts: { done: 1, failed: 0, waiting: 0 }, nodes: [], failure: null, failures: [],
    roles: { instruction: [], execution: [], review: [] }, message_count: 0, messages: [],
    ...over,
  }
}
const result = (over: Record<string, unknown> = {}) => toFlowCommandResult(raw(over))
const failed = (over: Record<string, unknown> = {}) => result({ state: 'failed', idle_reason: 'needs_intervention',
  failure: { node_key: 'gn_1', title: 'c', code: 'call_failed', detail: 'exit_nonzero' }, ...over })
const waiting = (over: Record<string, unknown> = {}) => result({ state: 'waiting', continues: true,
  idle_reason: 'needs_intervention', next_state: 'needs_intervention', ...over })
const idle = (seq: number, runId = 'run_1') => ({ runId, status: 'idle', lastEventSeq: seq })
const running = (seq: number, runId = 'run_1') => ({ runId, status: 'running', lastEventSeq: seq })

/** A load whose answers the test settles one by one (in the order they were asked). */
function deferredLoad() {
  const calls: { args: unknown[]; resolve: (r: unknown) => void; reject: (e: unknown) => void }[] = []
  const load = vi.fn((...args: unknown[]) => new Promise<any>((resolve, reject) => calls.push({ args, resolve, reject })))
  return { load, calls }
}

/** A stand-in timer: the retries wait until the test runs them. */
function timers() {
  const queue: { run: () => void; ms: number; cancelled: boolean }[] = []
  const schedule = vi.fn((run: () => void, ms: number) => {
    const entry = { run, ms, cancelled: false }
    queue.push(entry)
    return () => { entry.cancelled = true }
  })
  const live = () => queue.filter((e) => !e.cancelled)
  const runNext = async () => {
    const entry = live()[0]
    entry.cancelled = true
    entry.run()
    await flushPromises()
  }
  return { schedule, queue, live, runNext }
}

const networkError = () => Object.assign(new Error('Network Error'), { network: true })
const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { status })

describe('R1 / R2 / R4 — when the window opens (kept as it was)', () => {
  it('R1: a completed command opens the window once', async () => {
    const load = vi.fn(async () => result())
    const watch = createRunResultWatch({ load })
    watch.arm('f', idle(4))
    watch.observe('f', running(6))
    expect(load).not.toHaveBeenCalled()
    watch.observe('f', idle(9))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(true)
    expect(watch.state.seq).toBe(1)
    expect(Object.keys(watch.pending)).toEqual([])
    watch.observe('f', idle(10))
    expect(load).toHaveBeenCalledTimes(1)
  })

  it('R2: waiting (continues) keeps the watch with no window, then the completed end opens it once', async () => {
    const answers = [waiting({ end_seq: 6 }), result({ end_seq: 12 })]
    const load = vi.fn(async () => answers.shift()!)
    const watch = createRunResultWatch({ load })
    watch.arm('f', idle(4))
    watch.observe('f', idle(6))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])
    watch.observe('f', running(8))
    watch.observe('f', idle(12))
    await flushPromises()
    expect(load).toHaveBeenLastCalledWith('run_1', 5)
    expect(watch.state.resultOpen).toBe(true)
    expect(watch.state.seq).toBe(1)
  })

  it('R4: a stopped command ends the watch with no window', async () => {
    const watch = createRunResultWatch({ load: vi.fn(async () => result({ state: 'stopped', idle_reason: 'stopped' })) })
    watch.arm('f', idle(4))
    watch.observe('f', idle(7))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual([])
  })
})

describe('R3 — a read that fails keeps the watch and reads the same sequence again', () => {
  it('R3: one 5xx / network failure, then the same read again succeeds: one window (the old loss reversed)', async () => {
    for (const error of [httpError(503), networkError()]) {
      const clock = timers()
      const answers: (() => Promise<any>)[] = [async () => { throw error }, async () => result()]
      const load = vi.fn((..._args: unknown[]) => answers.shift()!())
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(9))
      await flushPromises()
      expect(Object.keys(watch.pending)).toEqual(['f'])                      // not dropped
      expect(watch.state.resultOpen).toBe(false)
      expect(clock.live()).toHaveLength(1)
      expect(clock.live()[0].ms).toBe(RUN_RESULT_RETRY.delayMs(1))
      watch.observe('f', idle(9))                                            // the same run again: the retry reads it
      expect(load).toHaveBeenCalledTimes(1)
      await clock.runNext()
      expect(load).toHaveBeenCalledTimes(2)
      expect(load.mock.calls[1]).toEqual(load.mock.calls[0])                 // the same read: run_1, latest
      expect(watch.state.resultOpen).toBe(true)
      expect(watch.state.seq).toBe(1)
      expect(Object.keys(watch.pending)).toEqual([])
    }
  })

  it('R3: the same sequence is read again by its command seq once the command is known', async () => {
    const clock = timers()
    const answers: (() => Promise<any>)[] = [async () => waiting({ end_seq: 6 }), async () => { throw httpError(502) },
      async () => failed({ end_seq: 11 })]
    const load = vi.fn((..._args: unknown[]) => answers.shift()!())
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', idle(4))
    watch.observe('f', idle(6))
    await flushPromises()
    watch.observe('f', idle(11))
    await flushPromises()
    expect(load.mock.calls[1]).toEqual(['run_1', 5])
    await clock.runNext()
    expect(load.mock.calls[2]).toEqual(['run_1', 5])
    expect(watch.state.result?.state).toBe('failed')
    expect(watch.state.seq).toBe(1)
  })

  it('R3b: past the retry limit the watch stalls (no endless retry), then a newer run or a resync reads it once more', async () => {
    for (const how of ['newer', 'resync'] as const) {
      const clock = timers()
      let fail = true
      const load = vi.fn(async (..._args: unknown[]) => {
        if (fail) throw networkError()
        return result()
      })
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(9))
      await flushPromises()
      while (clock.live().length > 0) await clock.runNext()
      expect(load).toHaveBeenCalledTimes(1 + RUN_RESULT_RETRY.limit)        // bounded: first read + limit retries
      expect(clock.queue.map((e) => e.ms)).toEqual([1000, 2000, 4000])      // backing off
      expect(watch.pending.f.stalled).toBe(true)
      expect(Object.keys(watch.pending)).toEqual(['f'])
      for (let i = 0; i < 3; i += 1) watch.observe('f', idle(9))            // the same run: nothing by itself
      await flushPromises()
      expect(load).toHaveBeenCalledTimes(1 + RUN_RESULT_RETRY.limit)
      expect(clock.live()).toHaveLength(0)
      fail = false
      if (how === 'newer') watch.observe('f', idle(10))
      else watch.recheck('f', idle(9))                                       // the run read again after a reconnect
      await flushPromises()
      expect(load).toHaveBeenCalledTimes(2 + RUN_RESULT_RETRY.limit)
      expect(watch.state.resultOpen).toBe(true)
      expect(watch.state.seq).toBe(1)
    }
  })

  it('R3b: a recheck that fails again stalls again with no timer of its own', async () => {
    const clock = timers()
    const load = vi.fn(async (..._args: unknown[]) => { throw httpError(500) })
    const watch = createRunResultWatch({ load, schedule: clock.schedule, retry: { limit: 1, delayMs: () => 10 } })
    watch.arm('f', idle(4))
    watch.observe('f', idle(9))
    await flushPromises()
    await clock.runNext()
    expect(watch.pending.f.stalled).toBe(true)
    watch.recheck('f', idle(9))
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(3)
    expect(watch.pending.f.stalled).toBe(true)
    expect(clock.live()).toHaveLength(0)
  })

  it('R3c: a gone command (404 CommandNotFound) or a refused read (401 / 403) ends the watch at once: no window, no retry', async () => {
    for (const status of [404, 403, 401]) {
      const clock = timers()
      const load = vi.fn(async (..._args: unknown[]) => { throw httpError(status) })
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(9))
      await flushPromises()
      expect(Object.keys(watch.pending)).toEqual([])
      expect(watch.state.resultOpen).toBe(false)
      expect(clock.queue).toHaveLength(0)
      watch.observe('f', idle(10))
      expect(load).toHaveBeenCalledTimes(1)
    }
  })

  it('the failure kinds: 404 / 401 / 403 final; network, 5xx, 408, 429 and no status passing', () => {
    expect([404, 401, 403].map((status) => readFailureKind(httpError(status)))).toEqual(['final', 'final', 'final'])
    expect([500, 502, 503, 408, 429].map((status) => readFailureKind(httpError(status)))).toEqual(Array(5).fill('passing'))
    expect(readFailureKind(networkError())).toBe('passing')
    expect(readFailureKind(new Error('x'))).toBe('passing')
    expect(readFailureKind(null)).toBe('passing')
  })

  it('the retry timer goes with its watch: forget, clear, the next command and dispose', async () => {
    for (const end of ['forget', 'clear', 'arm', 'dispose'] as const) {
      const clock = timers()
      const load = vi.fn(async (..._args: unknown[]) => { throw httpError(503) })
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(9))
      await flushPromises()
      expect(clock.live()).toHaveLength(1)
      if (end === 'forget') watch.forget('f')
      else if (end === 'clear') watch.clear()
      else if (end === 'arm') watch.arm('f', idle(9))
      else watch.dispose()
      expect(clock.live()).toHaveLength(0)
    }
  })
})

describe('R5 / R6 / R6b — a result is taken only while it is still the command\'s latest word', () => {
  it('R5: a failure shown and closed, then the same command completing: no other window', async () => {
    const answers = [failed(), result({ end_seq: 15 })]
    const load = vi.fn(async () => answers.shift()!)
    const watch = createRunResultWatch({ load })
    watch.arm('f', idle(4))
    watch.observe('f', idle(9))
    await flushPromises()
    expect(watch.state.result?.state).toBe('failed')
    watch.closeResult()
    watch.observe('f', running(12))
    watch.observe('f', idle(15))
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(1)
    expect(watch.state.resultOpen).toBe(false)
    expect(watch.state.seq).toBe(1)
  })

  it('R6: a past failure that answers after the run went on again opens nothing; the next idle read decides', async () => {
    for (const final of ['completed', 'failed'] as const) {
      const { load, calls } = deferredLoad()
      const watch = createRunResultWatch({ load })
      watch.arm('f', idle(1))
      watch.observe('f', idle(2))                                            // idle #2: the read starts
      expect(calls).toHaveLength(1)
      watch.observe('f', running(3))                                         // the Q&A answered: running #3
      calls[0].resolve(failed({ end_seq: 2 }))                               // ... then the old failure arrives
      await flushPromises()
      expect(watch.state.resultOpen).toBe(false)
      expect(Object.keys(watch.pending)).toEqual(['f'])
      expect(watch.pending.f.latestSeq).toBe(3)
      expect(watch.pending.f.latestStatus).toBe('running')
      watch.observe('f', idle(7))                                            // idle again: read the command again
      expect(calls).toHaveLength(2)
      expect(calls[1].args).toEqual(['run_1', 5])
      calls[1].resolve(final === 'completed' ? result({ end_seq: 7 }) : failed({ end_seq: 2 }))
      await flushPromises()
      expect(watch.state.result?.state).toBe(final)                          // the real end, or the stable failure
      expect(watch.state.seq).toBe(1)
      expect(Object.keys(watch.pending)).toEqual([])
    }
  })

  it('R6: an idle run waiting for its read is dropped once the run goes on past it', async () => {
    const { load, calls } = deferredLoad()
    const watch = createRunResultWatch({ load })
    watch.arm('f', idle(1))
    watch.observe('f', idle(2))
    watch.observe('f', idle(3))                                              // queued while #2 is read
    watch.observe('f', running(4))                                           // ... the run went on past it
    calls[0].resolve(failed({ end_seq: 2 }))
    await flushPromises()
    expect(calls).toHaveLength(1)                                            // #3 is not read
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])
  })

  it('R6b: an answer of another command or another run is never taken', async () => {
    const answers = [waiting({ end_seq: 6 }), result({ command_seq: 7, end_seq: 9 }), result({ run_id: 'run_x', end_seq: 10 }),
      result({ end_seq: 11 })]
    const load = vi.fn(async () => answers.shift()!)
    const clock = timers()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', idle(4))
    watch.observe('f', idle(6))
    await flushPromises()
    expect(watch.pending.f.commandSeq).toBe(5)
    watch.observe('f', idle(9))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)                               // command 7's answer
    watch.observe('f', idle(10))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)                               // run_x's answer
    expect(Object.keys(watch.pending)).toEqual(['f'])
    watch.observe('f', idle(11))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(true)                                // its own
    expect(watch.state.result?.commandSeq).toBe(5)
  })

  it('R6b: before the command is known, an answer of another run than the one read is not taken', async () => {
    const answers = [result({ run_id: 'run_x' }), result()]
    const load = vi.fn(async () => answers.shift()!)
    const clock = timers()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', null)
    watch.observe('f', idle(3))
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(watch.pending.f.commandSeq).toBeNull()
    watch.observe('f', idle(4))
    await flushPromises()
    expect(watch.state.result?.runId).toBe('run_1')
  })
})

// flowgate.v02.0143 TR#1 rework (review rej_01M6SBRB5MN4DCTT): the races the review reproduced on the real watch.
describe('TR rework — a retry, an answer not taken and another run never open a past window', () => {
  /** A timer whose cancel comes too late: the callback still runs (a retry firing just as the run went on). */
  function lateTimers() {
    const queue: (() => void)[] = []
    const schedule = vi.fn((run: () => void) => {
      queue.push(run)
      return () => {}
    })
    return { schedule, queue }
  }

  it('P1: the run going on while a retry waits holds the retry; the next idle run reads and a stable end shows once', async () => {
    for (const final of ['completed', 'failed'] as const) {
      const clock = timers()
      const { load, calls } = deferredLoad()
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(9))
      calls[0].reject(httpError(503))
      await flushPromises()
      expect(clock.live()).toHaveLength(1)                                   // the retry of idle #9 waits
      watch.observe('f', running(10))                                        // the Q&A answered: running #10
      expect(clock.live()).toHaveLength(0)                                   // held: idle #9 is a past run now
      clock.queue[0].run()                                                   // even a callback that still runs reads nothing
      await flushPromises()
      expect(calls).toHaveLength(1)
      expect(watch.state.resultOpen).toBe(false)
      expect(Object.keys(watch.pending)).toEqual(['f'])
      watch.observe('f', idle(9))                                            // a late copy of idle #9: still a past run
      expect(calls).toHaveLength(1)
      watch.observe('f', idle(12))                                           // the next idle run reads
      expect(calls).toHaveLength(2)
      expect(calls[1].args).toEqual(['run_1'])
      calls[1].resolve(final === 'completed' ? result({ end_seq: 12 }) : failed({ end_seq: 12 }))
      await flushPromises()
      expect(watch.state.result?.state).toBe(final)                          // the real end, or the stable failure, once
      expect(watch.state.seq).toBe(1)
      expect(Object.keys(watch.pending)).toEqual([])
      expect(clock.live()).toHaveLength(0)
    }
  })

  it('P1: just before the retry — the review\'s sequence (idle #9, 503, running #10, the timer, an old failure) opens nothing', async () => {
    const clock = lateTimers()
    const { load, calls } = deferredLoad()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', idle(4))
    watch.observe('f', idle(9))
    calls[0].reject(httpError(503))
    await flushPromises()
    expect(clock.queue).toHaveLength(1)
    watch.observe('f', running(10))
    clock.queue[0]()                                                         // the timer fires right after the run went on
    await flushPromises()
    expect(calls).toHaveLength(1)                                            // no read of the old idle #9
    expect(watch.state.resultOpen).toBe(false)
    expect(Object.keys(watch.pending)).toEqual(['f'])
    watch.observe('f', idle(14))
    calls[1].resolve(failed({ end_seq: 14 }))
    await flushPromises()
    expect(watch.state.result?.state).toBe('failed')
    expect(watch.state.seq).toBe(1)
  })

  it('P1: the run going on while the failing read is still out schedules no retry at all', async () => {
    const clock = timers()
    const { load, calls } = deferredLoad()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', idle(4))
    watch.observe('f', idle(9))
    watch.observe('f', running(10))
    calls[0].reject(networkError())
    await flushPromises()
    expect(clock.queue).toHaveLength(0)
    expect(watch.pending.f.stalled).toBe(false)
    watch.observe('f', idle(11))
    expect(calls).toHaveLength(2)
    calls[1].resolve(result({ end_seq: 11 }))
    await flushPromises()
    expect(watch.state.result?.state).toBe('completed')
  })

  it('P2: an answer of another command or run leaves its sequence unread; a later valid answer of the same sequence opens once', async () => {
    for (const wrong of [{ command_seq: 999 }, { run_id: 'run_x' }]) {
      const clock = timers()
      const { load, calls } = deferredLoad()
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(6))
      calls[0].resolve(waiting({ end_seq: 6 }))
      await flushPromises()
      expect(watch.pending.f.commandSeq).toBe(5)
      expect(watch.pending.f.seenSeq).toBe(6)
      watch.observe('f', idle(11))                                           // the final idle run: no larger seq follows
      calls[1].resolve(failed({ end_seq: 11, ...wrong }))
      await flushPromises()
      expect(watch.state.resultOpen).toBe(false)                             // not taken
      expect(watch.pending.f.seenSeq).toBe(6)                                // #11 is still unread
      expect(clock.live()).toHaveLength(1)                                   // a bounded re-read of #11
      watch.observe('f', idle(11))                                           // the same run again: the re-read reads it
      expect(calls).toHaveLength(2)
      await clock.runNext()
      expect(calls).toHaveLength(3)
      expect(calls[2].args).toEqual(['run_1', 5])
      calls[2].resolve(failed({ end_seq: 11 }))
      await flushPromises()
      expect(watch.state.result?.state).toBe('failed')
      expect(watch.state.result?.commandSeq).toBe(5)
      expect(watch.state.seq).toBe(1)
      expect(Object.keys(watch.pending)).toEqual([])
    }
  })

  it('P2: answers not taken are re-read a bounded number of times; then the same sequence is read on a recheck', async () => {
    const clock = timers()
    let answer = result({ command_seq: 999, end_seq: 11 })
    const load = vi.fn(async (..._args: unknown[]) => answer)
    const watch = createRunResultWatch({ load, schedule: clock.schedule, retry: { limit: 2, delayMs: () => 10 } })
    watch.restore([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }], [])
    watch.observe('f', idle(11))
    await flushPromises()
    while (clock.live().length > 0) await clock.runNext()
    expect(load).toHaveBeenCalledTimes(3)                                    // first read + limit re-reads
    expect(watch.pending.f.stalled).toBe(true)
    expect(watch.pending.f.seenSeq).toBe(-1)
    watch.observe('f', idle(11))                                             // stalled: nothing by itself
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(3)
    answer = result({ end_seq: 11 })
    watch.recheck('f', idle(11))                                             // the run read again (resync)
    await flushPromises()
    expect(load).toHaveBeenCalledTimes(4)
    expect(watch.state.result?.state).toBe('completed')
    expect(watch.state.seq).toBe(1)
  })

  it('P2: another run (running or idle) seen while the command\'s run is read ends the watch; the late answer opens nothing', async () => {
    for (const other of [running(1, 'run_2'), idle(1, 'run_2')]) {
      const clock = timers()
      const { load, calls } = deferredLoad()
      const watch = createRunResultWatch({ load, schedule: clock.schedule })
      watch.arm('f', idle(4))
      watch.observe('f', idle(6))
      calls[0].resolve(waiting({ end_seq: 6 }))
      await flushPromises()
      expect(watch.pending.f.commandRunId).toBe('run_1')
      watch.observe('f', idle(11))                                           // run_1's result is being read
      expect(calls).toHaveLength(2)
      watch.observe('f', other)                                              // ... and run_2 took the Flow
      expect(Object.keys(watch.pending)).toEqual([])                         // at once, read going on or not
      calls[1].resolve(failed({ end_seq: 11 }))                              // the old run_1 failure arrives late
      await flushPromises()
      expect(watch.state.resultOpen).toBe(false)
      expect(watch.state.seq).toBe(0)
      expect(calls).toHaveLength(2)
      expect(clock.queue).toHaveLength(0)
    }
  })

  it('P2: another run seen while a retry waits ends the watch and its timer', async () => {
    const clock = timers()
    const { load, calls } = deferredLoad()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.restore([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }], [])
    watch.observe('f', idle(11))
    calls[0].reject(httpError(502))
    await flushPromises()
    expect(clock.live()).toHaveLength(1)
    watch.observe('f', running(1, 'run_2'))
    expect(clock.live()).toHaveLength(0)
    expect(Object.keys(watch.pending)).toEqual([])
    clock.queue[0].run()
    await flushPromises()
    expect(calls).toHaveLength(1)
    expect(watch.state.resultOpen).toBe(false)
  })

  it('before the command is known, another run seen during the read is not taken for the old run\'s answer', async () => {
    const clock = timers()
    const { load, calls } = deferredLoad()
    const watch = createRunResultWatch({ load, schedule: clock.schedule })
    watch.arm('f', idle(4))
    watch.observe('f', idle(9))
    watch.observe('f', running(1, 'run_2'))
    calls[0].resolve(failed({ end_seq: 9 }))                                 // run_1's answer: run_2 came since
    await flushPromises()
    expect(watch.state.resultOpen).toBe(false)
    expect(watch.pending.f.commandSeq).toBeNull()
    expect(clock.queue).toHaveLength(0)
    watch.observe('f', idle(3, 'run_2'))                                     // run_2's own read decides
    expect(calls[1].args).toEqual(['run_2'])
    calls[1].resolve(result({ run_id: 'run_2', command_seq: 1, end_seq: 3 }))
    await flushPromises()
    expect(watch.state.result?.runId).toBe('run_2')
    expect(watch.state.seq).toBe(1)
  })
})

describe('R7 — F5: the watch comes back from the snapshot, a shown result is never shown again', () => {
  it('a result already shown before the F5 is not shown again; the watch ends', async () => {
    const before = createRunResultWatch({ load: vi.fn(async () => result()) })
    before.arm('f', idle(4))
    before.observe('f', idle(9))
    await flushPromises()
    expect(before.state.resultOpen).toBe(true)
    const saved = before.persisted()
    expect(saved.seen).toEqual([{ flowId: 'f', keys: [runResultKey(result())] }])

    // The window was shown; the same command is armed again by a stale snapshot entry.
    const load = vi.fn(async () => result())
    const after = createRunResultWatch({ load })
    after.restore([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }], saved.seen)
    after.observe('f', idle(9))
    await flushPromises()
    expect(load).toHaveBeenCalledWith('run_1', 5)                            // its own command, by its seq
    expect(after.state.resultOpen).toBe(false)
    expect(Object.keys(after.pending)).toEqual([])
  })

  it('a watch armed before the F5 whose result was not shown opens it once after the F5', async () => {
    for (const final of [result(), failed()]) {
      const before = createRunResultWatch({ load: vi.fn(async () => waiting({ end_seq: 6 })) })
      before.arm('f', idle(4))
      before.observe('f', idle(6))
      await flushPromises()
      const saved = before.persisted()
      expect(saved.watches).toEqual([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }])
      // Through the snapshot as sessionStorage keeps it.
      const snapshot = parseDesignerSession(JSON.stringify({
        v: 1, projectId: 'p', explorer: { selection: null, expandedGroupIds: [] }, tabs: [{ flowId: 'f', name: 'F', groupId: null }],
        activeFlowId: 'f', leftMode: 'project', rightTool: 'palette', runWatches: saved.watches, runSeen: saved.seen,
      }))!
      expect(snapshot.runWatches).toEqual(saved.watches)
      const load = vi.fn(async () => final)
      const after = createRunResultWatch({ load })
      after.restore(snapshot.runWatches!, snapshot.runSeen ?? [])
      after.observe('f', idle(9))
      await flushPromises()
      expect(after.state.result?.state).toBe(final.state)
      expect(after.state.seq).toBe(1)
      after.observe('f', idle(10))
      expect(load).toHaveBeenCalledTimes(1)
    }
  })

  it('a watch armed before the command was known reads the latest command after the F5', async () => {
    const before = createRunResultWatch({ load: vi.fn() })
    before.arm('f', idle(4))
    const saved = before.persisted()
    expect(saved.watches).toEqual([{ flowId: 'f', runId: 'run_1', commandSeq: null, afterSeq: 4 }])
    const load = vi.fn(async () => result())
    const after = createRunResultWatch({ load })
    after.restore(saved.watches, [])
    after.observe('f', idle(4))                                              // the run as it was before the command
    expect(load).not.toHaveBeenCalled()
    after.observe('f', idle(9))
    await flushPromises()
    expect(load).toHaveBeenCalledWith('run_1')
    expect(after.state.resultOpen).toBe(true)
  })

  it('a restored watch whose command is gone (404) or refused (403) ends with no window', async () => {
    for (const status of [404, 403]) {
      const load = vi.fn(async (..._args: unknown[]) => { throw httpError(status) })
      const after = createRunResultWatch({ load })
      after.restore([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }], [])
      after.observe('f', idle(9))
      await flushPromises()
      expect(Object.keys(after.pending)).toEqual([])
      expect(after.state.resultOpen).toBe(false)
    }
  })

  it('the snapshot keeps only the entries that read, and the shown results stay bounded', async () => {
    const snapshot = parseDesignerSession(JSON.stringify({
      v: 1, projectId: 'p', explorer: { selection: null, expandedGroupIds: [] }, tabs: [], activeFlowId: null,
      leftMode: 'project', rightTool: 'palette',
      runWatches: [{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }, { flowId: '', runId: 'r' }, 'x',
        { flowId: 'g', runId: null, commandSeq: 3, afterSeq: 1 }],
      runSeen: [{ flowId: 'f', keys: ['a', 1] }, { flowId: 'h' }],
    }))!
    expect(snapshot).not.toBeNull()
    expect(snapshot.runWatches).toEqual([{ flowId: 'f', runId: 'run_1', commandSeq: 5, afterSeq: 4 }])
    expect(snapshot.runSeen).toEqual([{ flowId: 'f', keys: ['a'] }])
    const plain = parseDesignerSession(JSON.stringify({
      v: 1, projectId: 'p', explorer: { selection: null, expandedGroupIds: [] }, tabs: [], activeFlowId: null,
      leftMode: 'project', rightTool: 'palette',
    }))!
    expect('runWatches' in plain).toBe(false)
    expect('runSeen' in plain).toBe(false)

    const watch = createRunResultWatch({ load: vi.fn() })
    watch.restore([], [{ flowId: 'f', keys: Array.from({ length: 30 }, (_, i) => `k${i}`) }])
    expect(watch.persisted().seen[0].keys).toHaveLength(20)
    expect(watch.persisted().seen[0].keys[19]).toBe('k29')
  })
})
