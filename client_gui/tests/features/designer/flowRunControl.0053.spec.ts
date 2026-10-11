/**
 * flowgate.v02.0053 T#9 — the top band's run buttons on the server Graph run (center/run/flowRunControl.ts):
 * 전체 실행 starts (or resumes) the run, 다음 단계 runs one step (start / resume, then stop at once), 중지
 * stops it; the edited draft is confirmed first; a refusal is kept with its server code and the run read
 * again; every answer reads the Canvas again. A 중지 pressed while a start / resume is answered (the
 * button shows ■ 중지 then) is kept and sent once the run is known. And the Center session reads the run
 * with every Graph read and forgets it with the tab.
 */
import { flushPromises } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { computed, reactive } from 'vue'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'
import { createFlowRunControl, type FlowRunState } from '@/features/designer/center/run/flowRunControl'

const P = 'p'
const F = 'p.ui.0001'

type Request = 'current' | 'start' | 'resume'

interface FakeRunSource extends FlowGraphSource {
  calls: string[]
  run: FlowRunInfo | null
  refuseStart: string | null
  /** A request named here is sent (recorded) but answered only once the promise settles. */
  holds: Partial<Record<Request, Promise<void>>>
}

function fakeSource(initial: FlowRunInfo | null = null): FakeRunSource {
  let seq = 0
  const source: FakeRunSource = {
    ...fixtureFlowGraphSource,
    calls: [],
    run: initial,
    refuseStart: null,
    holds: {},
    loadRun: async (_p, flowId) => {
      source.calls.push(`current ${flowId}`)
      await source.holds.current
      return source.run
    },
    startRun: async (_p, flowId) => {
      source.calls.push(`start ${flowId}`)
      await source.holds.start
      if (source.refuseStart) throw Object.assign(new Error('refused'), { code: source.refuseStart })
      seq += 1
      source.run = { runId: `run_${seq}`, status: 'running', openNodeKeys: ['gn_a'] }
      return source.run
    },
    stopRun: async (_p, runId) => {
      source.calls.push(`stop ${runId}`)
      source.run = { ...source.run!, status: 'stopped' }
      return source.run
    },
    resumeRun: async (_p, runId) => {
      source.calls.push(`resume ${runId}`)
      await source.holds.resume
      source.run = { ...source.run!, status: 'running' }
      return source.run
    },
  }
  return source
}

/** Holds the answer to the next such request until the returned release is called. */
function hold(source: FakeRunSource, request: Request): () => void {
  let release!: () => void
  source.holds[request] = new Promise<void>((resolve) => (release = resolve))
  return () => {
    delete source.holds[request]
    release()
  }
}

function control(source: FakeRunSource, prepare: () => boolean | Promise<boolean> = () => true) {
  const runs = reactive<Record<string, FlowRunState>>({})
  const refresh = vi.fn()
  let open = true
  const ctl = createFlowRunControl({ source, projectId: () => P, isOpen: () => open, prepare, refresh }, runs)
  return { ctl, runs, refresh, close: () => (open = false) }
}

describe('flowRunControl — the run buttons (flowgate.v02.0053 T#9)', () => {
  it('전체 실행 starts a run and keeps it going; 중지 stops it; 전체 실행 again resumes the same run', async () => {
    const source = fakeSource()
    const { ctl, runs, refresh } = control(source)
    expect(ctl.available).toBe(true)

    expect(await ctl.run(F, 'all')).toBe(true)
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`])
    expect(runs[F]).toMatchObject({ run: { runId: 'run_1', status: 'running' }, mode: 'all', action: null, error: null })
    expect(refresh).toHaveBeenCalledWith(F)

    expect(await ctl.stop(F)).toBe(true)
    expect(source.calls.slice(2)).toEqual(['stop run_1'])
    expect(runs[F]).toMatchObject({ run: { status: 'stopped' }, mode: null })

    expect(await ctl.run(F, 'all')).toBe(true)
    expect(source.calls.slice(3)).toEqual([`current ${F}`, 'resume run_1'])
    expect(runs[F].run?.status).toBe('running')
  })

  it('다음 단계 runs one step: start (or resume) and stop at once; on a run already going it only stops', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source)
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`, 'stop run_1'])
    expect(runs[F]).toMatchObject({ run: { status: 'stopped' }, mode: null })

    expect(await ctl.run(F, 'next')).toBe(true)
    expect(source.calls.slice(3)).toEqual([`current ${F}`, 'resume run_1', 'stop run_1'])

    source.run = { runId: 'run_9', status: 'running', openNodeKeys: [] }
    expect(await ctl.run(F, 'next')).toBe(true)
    expect(source.calls.slice(6)).toEqual([`current ${F}`, 'stop run_9'])
  })

  it('shows the button while the answer is on its way, and takes no second 다음 단계 / 전체 실행 meanwhile', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source)
    const going = ctl.run(F, 'all')
    expect(runs[F].action).toBe('all')
    expect(await ctl.run(F, 'next')).toBe(false)
    expect(await ctl.run(F, 'all')).toBe(false)
    expect(await going).toBe(true)
    expect(runs[F]).toMatchObject({ action: null, run: { status: 'running' }, mode: 'all' })
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`])
  })

  // The Shell shows the waiting button as ■ 중지 (enabled): its click must not be lost.
  it('a 중지 pressed while the start is answered is kept, and the run is stopped as soon as the start names it', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source)
    const release = hold(source, 'start')
    const going = ctl.run(F, 'all')
    await flushPromises()
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`])
    expect(runs[F].action).toBe('all')

    const stopping = ctl.stop(F)
    await flushPromises()
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`])
    release()
    expect(await going).toBe(true)
    expect(await stopping).toBe(true)
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`, 'stop run_1'])
    expect(source.run?.status).toBe('stopped')
    expect(runs[F]).toMatchObject({ run: { runId: 'run_1', status: 'stopped' }, action: null, mode: null, error: null })

    // The kept 중지 went with that action: the next 전체 실행 resumes and keeps going.
    expect(await ctl.run(F, 'all')).toBe(true)
    expect(source.calls.slice(3)).toEqual([`current ${F}`, 'resume run_1'])
    expect(runs[F]).toMatchObject({ run: { status: 'running' }, mode: 'all' })
  })

  it('a 중지 pressed while the resume is answered stops the resumed run (once, for 다음 단계 too)', async () => {
    const source = fakeSource({ runId: 'run_4', status: 'stopped', openNodeKeys: [] })
    const { ctl, runs } = control(source)
    let release = hold(source, 'resume')
    let going = ctl.run(F, 'all')
    await flushPromises()
    let stopping = ctl.stop(F)
    release()
    expect(await going).toBe(true)
    expect(await stopping).toBe(true)
    expect(source.calls).toEqual([`current ${F}`, 'resume run_4', 'stop run_4'])
    expect(runs[F]).toMatchObject({ run: { status: 'stopped' }, mode: null })

    release = hold(source, 'resume')
    going = ctl.run(F, 'next')
    await flushPromises()
    stopping = ctl.stop(F)
    release()
    expect(await going).toBe(true)
    expect(await stopping).toBe(true)
    expect(source.calls.slice(3)).toEqual([`current ${F}`, 'resume run_4', 'stop run_4'])
    expect(runs[F].run?.status).toBe('stopped')
  })

  it('a 중지 pressed before the start went out starts nothing; a refused start keeps its refusal', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source)
    const release = hold(source, 'current')
    const going = ctl.run(F, 'all')
    await flushPromises()
    const stopping = ctl.stop(F)
    release()
    expect(await going).toBe(false)
    expect(await stopping).toBe(true)
    await flushPromises()
    expect(source.calls.filter((call) => !call.startsWith('current'))).toEqual([])
    expect(runs[F]).toMatchObject({ run: null, action: null, mode: null, error: null })

    // A start refused while a 중지 waited: nothing ran, the refusal is told, and 중지 did not stop anything.
    source.refuseStart = 'run_policy_undecided'
    const refused = hold(source, 'start')
    const failing = ctl.run(F, 'all')
    await flushPromises()
    const stopped = ctl.stop(F)
    refused()
    expect(await failing).toBe(false)
    expect(await stopped).toBe(false)
    expect(runs[F].error?.code).toBe('run_policy_undecided')
    expect(source.calls.filter((call) => call.startsWith('stop'))).toEqual([])
  })

  it('a draft that could not be confirmed starts nothing', async () => {
    const source = fakeSource()
    const { ctl, runs } = control(source, async () => false)
    expect(await ctl.run(F, 'all')).toBe(false)
    expect(source.calls.filter((call) => !call.startsWith('current'))).toEqual([])
    expect(runs[F].run).toBeNull()
  })

  it('a refusal keeps the server code (a new seq each time) and reads the run again', async () => {
    const source = fakeSource()
    source.refuseStart = 'run_policy_undecided'
    const { ctl, runs } = control(source)
    expect(await ctl.run(F, 'all')).toBe(false)
    await flushPromises()
    expect(runs[F].error).toMatchObject({ code: 'run_policy_undecided', seq: 1 })
    expect(source.calls).toEqual([`current ${F}`, `start ${F}`, `current ${F}`])
    expect(await ctl.run(F, 'all')).toBe(false)
    expect(runs[F].error?.seq).toBe(2)
    source.refuseStart = null
    expect(await ctl.run(F, 'all')).toBe(true)
    expect(runs[F].error).toBeNull()
  })

  it('a read keeps the server run; an answer for a closed tab is dropped; forget / clear drop the state', async () => {
    const source = fakeSource({ runId: 'run_7', status: 'stopped', openNodeKeys: [] })
    const { ctl, runs, close } = control(source)
    ctl.read(F)
    await flushPromises()
    expect(runs[F].run).toMatchObject({ runId: 'run_7', status: 'stopped' })
    ctl.forget(F)
    expect(runs[F]).toBeUndefined()

    close()
    ctl.read(F)
    await flushPromises()
    expect(runs[F]).toBeUndefined()
    ctl.clear()
    expect(Object.keys(runs)).toEqual([])
  })

  it('a source that runs nothing (the sample source) offers no run', async () => {
    const { ctl } = control({ ...fixtureFlowGraphSource } as FakeRunSource)
    expect(ctl.available).toBe(false)
    expect(await ctl.run(F, 'all')).toBe(false)
  })
})

describe('Center session — the run goes with the Graph (flowgate.v02.0053 T#9)', () => {
  it('reads the run with the Graph, runs and stops it, and forgets it with the tab', async () => {
    const source = fakeSource({ runId: 'run_3', status: 'stopped', openNodeKeys: [] })
    const center = createDesignerCenterSession({ source })
    center.setProject(P)
    expect(center.canRun).toBe(true)
    center.openFlow({ projectId: P, flowId: F, flowName: 'Canvas' })
    await flushPromises()
    expect(center.activeRun.value?.run).toMatchObject({ runId: 'run_3', status: 'stopped' })
    // What the Shell shows is derived (computed): every change of the run state must reach it.
    const shown = computed(() => `${center.activeRun.value?.action ?? '-'} ${center.activeRun.value?.run?.status ?? '-'}`)
    expect(shown.value).toBe('- stopped')

    const going = center.runFlow(F, 'all')
    expect(shown.value).toBe('all stopped')
    expect(await going).toBe(true)
    expect(shown.value).toBe('- running')
    expect(center.activeRun.value).toMatchObject({ run: { status: 'running' }, mode: 'all' })
    expect(await center.stopRun(F)).toBe(true)
    expect(shown.value).toBe('- stopped')

    center.closeFlow(F)
    expect(center.activeRun.value).toBeNull()
    expect(center.state.runs[F]).toBeUndefined()
    center.dispose()
  })
})
