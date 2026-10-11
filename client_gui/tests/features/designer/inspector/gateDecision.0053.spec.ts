/**
 * flowgate.v02.0053 T#9 — the 확인 card's ✓ 확인 · ✕ 반려 on the gate decision API (P0008 S6-1): read the Flow's
 * current run, usable only while this gate is open in a running run, the decision sent for this gate (a reject
 * with its optional reason), the run answered after it, and a refusal shown in the tooltip only (no code).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const runRead = vi.hoisted(() => vi.fn())
const gateDecide = vi.hoisted(() => vi.fn())
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return {
    ...actual,
    runCurrentRead: (...args: unknown[]) => runRead(...args),
    runGateDecide: (...args: unknown[]) => gateDecide(...args),
  }
})

import GateActions from '@/features/designer/inspector/features/gateCard/GateActions.vue'
import { SELECTIONS, prepareInspectorI18n, targetOf } from './support/inspectorSupport'

const i18n = prepareInspectorI18n()
const GATE = targetOf(SELECTIONS.graphGate)

const run = (status: string, gate: string) => ({ runId: 'run_g1', status, nodes: [{ nodeKey: 'gn_a', status: 'done' }, { nodeKey: GATE.nodeId, status: gate }] })

function refusal(code: string, status = 409) {
  return Object.assign(new Error('Request failed'), { isAxiosError: true, response: { status, data: { ok: false, error: { code, message: code, params: {} } } } })
}

let wrapper: VueWrapper<any> | null = null

async function mountActions() {
  wrapper = mount(GateActions, { props: { target: GATE }, global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const confirmButton = (w: VueWrapper<any>) => w.get('[data-test="gate-confirm"]')
const rejectButton = (w: VueWrapper<any>) => w.get('[data-test="gate-reject"]')
const body = () => document.body

beforeEach(() => {
  runRead.mockReset()
  gateDecide.mockReset()
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('gate decision (flowgate.v02.0053 T#9)', () => {
  it('reads the Flow’s run; an open gate in a running run can be confirmed, and the answer is the run after it', async () => {
    runRead.mockResolvedValue(run('running', 'open'))
    gateDecide.mockResolvedValue(run('running', 'done'))
    const w = await mountActions()
    expect(runRead).toHaveBeenCalledWith(GATE.flowId)
    expect(confirmButton(w).attributes('disabled')).toBeUndefined()
    expect(confirmButton(w).attributes('title')).toBeUndefined()

    await confirmButton(w).trigger('click')
    await flushPromises()
    expect(gateDecide).toHaveBeenCalledWith('run_g1', GATE.nodeId, 'confirm', undefined)
    // Decided: the gate is no longer open, so both wait again.
    expect(confirmButton(w).attributes('disabled')).toBeDefined()
    expect(confirmButton(w).attributes('title')).toBe('이 확인 차례가 오면 확인할 수 있습니다')
  })

  it('✕ 반려 ▾ asks for an optional reason and sends it with the reject', async () => {
    runRead.mockResolvedValue(run('running', 'open'))
    gateDecide.mockResolvedValue(run('running', 'rejected'))
    const w = await mountActions()
    await rejectButton(w).trigger('click')
    await flushPromises()
    expect(body().querySelector('[data-test="gate-reject-dialog"]')).not.toBeNull()
    const area = body().querySelector<HTMLTextAreaElement>('[data-test="gate-reject-reason"] textarea, textarea[data-test="gate-reject-reason"]')!
    area.value = '범위가 빠졌습니다'
    area.dispatchEvent(new Event('input'))
    await flushPromises()
    body().querySelector<HTMLButtonElement>('[data-test="gate-reject-submit"]')!.click()
    await flushPromises()
    expect(gateDecide).toHaveBeenCalledWith('run_g1', GATE.nodeId, 'reject', '범위가 빠졌습니다')
    expect(body().querySelector('[data-test="gate-reject-dialog"]')).toBeNull()
  })

  it('no run, a stopped run or another turn: both disabled with the reason in the tooltip, nothing sent', async () => {
    for (const [answer, tip] of [
      [null, '실행 중일 때 확인할 수 있습니다'],
      [run('stopped', 'open'), '실행 중일 때 확인할 수 있습니다'],
      [run('running', 'pending'), '이 확인 차례가 오면 확인할 수 있습니다'],
    ] as const) {
      runRead.mockResolvedValue(answer)
      const w = await mountActions()
      expect(confirmButton(w).attributes('disabled')).toBeDefined()
      expect(rejectButton(w).attributes('disabled')).toBeDefined()
      expect(confirmButton(w).attributes('title')).toBe(tip)
      await confirmButton(w).trigger('click')
      expect(gateDecide).not.toHaveBeenCalled()
      wrapper?.unmount()
      wrapper = null
    }
  })

  it('a change of the gate’s state on the Canvas reads the run again (also without the event stream)', async () => {
    runRead.mockResolvedValueOnce(run('running', 'pending')).mockResolvedValue(run('running', 'open'))
    const w = await mountActions()
    expect(confirmButton(w).attributes('disabled')).toBeDefined()
    await w.setProps({ target: { ...GATE, canvasState: 'in_progress', nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null } } })
    await flushPromises()
    expect(runRead).toHaveBeenCalledTimes(2)
    expect(confirmButton(w).attributes('disabled')).toBeUndefined()
  })

  it('a refused decision shows its reason in plain words in the tooltip and reads the run again', async () => {
    runRead.mockResolvedValue(run('running', 'open'))
    gateDecide.mockRejectedValue(refusal('run_policy_undecided'))
    const w = await mountActions()
    await confirmButton(w).trigger('click')
    await flushPromises()
    expect(confirmButton(w).attributes('title')).toBe('확인 규칙이 아직 정해지지 않아 확인하지 못했습니다')
    expect(w.get('[data-test="inspector-gate-actions"]').classes()).toContain('is-failed')
    expect(w.text()).not.toContain('run_policy_undecided')
    expect(runRead).toHaveBeenCalledTimes(2)
  })
})
