/**
 * v02gui.0015 T2 (NR0006 §6-6) — a card whose current attempt did not take an earlier attempt's uncommitted changes says
 * so: the run read's `uncarried_changes` ({ attempts, files, unknown }) is kept by both run transports and shown on the
 * Canvas card as a 미인계 chip (hover: how many attempts / files, that they are kept and not carried). None: no chip.
 * Every string has its ko / en / ja form.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { toVueFlowNodes, type FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import { toUncarriedChanges } from '@/features/project/flowCanvasApi'
import { toFlowRun } from '@/features/project/flowRunApi'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()

describe('the run read keeps uncarried_changes', () => {
  it('a count of earlier attempts is kept; none or a broken shape is null', () => {
    expect(toUncarriedChanges({ attempts: 2, files: 5, unknown: false })).toEqual({ attempts: 2, files: 5, unknown: false })
    expect(toUncarriedChanges({ attempts: 1, files: 0, unknown: true })).toEqual({ attempts: 1, files: 0, unknown: true })
    expect(toUncarriedChanges(null)).toBeNull()
    expect(toUncarriedChanges({ attempts: 0, files: 3 })).toBeNull()
    expect(toUncarriedChanges({ attempts: 'x' })).toBeNull()
    expect(toUncarriedChanges({ attempts: 1 })).toEqual({ attempts: 1, files: 0, unknown: false })
  })

  it('the run transport keeps it on its node, only when sent', () => {
    const run = toFlowRun({ run_id: 'run', flow_id: 'f', status: 'running', node_runs: [
      { node_key: 'a', status: 'open', uncarried_changes: { attempts: 1, files: 2, unknown: false } },
      { node_key: 'b', status: 'open', uncarried_changes: null },
      { node_key: 'c', status: 'open' },
    ] })!
    const [a, b, c] = run.nodeRuns!
    expect(a.uncarriedChanges).toEqual({ attempts: 1, files: 2, unknown: false })
    expect(b.uncarriedChanges).toBeNull()
    expect('uncarriedChanges' in c).toBe(false)
  })
})

describe('the Canvas card carries the chip', () => {
  const graph = (uncarried: unknown) => ({
    flowId: 'f', nodes: [{ id: 'gn_a', kind: 'plan', title: '카드', state: null, x: 0, y: 0,
      nodeRun: { status: 'open', openedAt: null, finishedAt: null, resultDocId: null, uncarriedChanges: uncarried } }],
    edges: [],
  }) as unknown as FlowGraph

  it('from the node run; none: no chip', () => {
    expect(toVueFlowNodes(graph({ attempts: 1, files: 2, unknown: false }), null)[0].data.uncarried)
      .toEqual({ files: 2, attempts: 1, unknown: false })
    expect(toVueFlowNodes(graph(null), null)[0].data.uncarried).toBeUndefined()
  })
})

describe('FlowNodeCard shows 미인계', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })
  const card = (uncarried?: FlowGateNodeData['uncarried']) => {
    const data: FlowGateNodeData = { kind: 'plan', title: '카드', state: null, selected: false, ...(uncarried ? { uncarried } : {}) }
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_a', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    return wrapper.find('[data-test="designer-canvas-node-uncarried"]')
  }

  it('ko: the count and the hover sentence', () => {
    const chip = card({ attempts: 1, files: 2, unknown: false })
    expect(chip.text()).toBe('미인계 2')
    expect(chip.attributes('title')).toBe(
      '이전 시도 1개의 커밋 안 된 변경 2개 파일을 이번 시도는 이어받지 않았습니다. 변경은 지워지지 않고 이전 작업 폴더(또는 중지 시 보존본)에 남아 있습니다.')
  })

  it('a count that could not be read is said so', () => {
    const chip = card({ attempts: 1, files: 0, unknown: true })
    expect(chip.text()).toBe('미인계 ?')
    expect(chip.attributes('title')).toContain('일부 시도는 변경 수를 읽지 못했습니다.')
  })

  it('none: no chip', () => {
    expect(card().exists()).toBe(false)
  })

  it('every string has its ko / en / ja form', () => {
    for (const messages of [ko, en, ja]) {
      const strings = messages.designer.center.uncarried
      for (const key of ['flag', 'flag_unknown', 'title', 'unknown'] as const) expect(typeof strings[key]).toBe('string')
    }
    i18n.global.locale.value = 'en'
    expect(card({ attempts: 2, files: 3, unknown: false }).text()).toBe('Not carried 3')
  })
})
