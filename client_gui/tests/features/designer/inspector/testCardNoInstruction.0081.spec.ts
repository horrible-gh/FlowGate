// F81-9 (flowgate.v02test.0081.0017-T): a test (TS) card has no instruction on the screen. The Canvas has the
// Inspector's two buttons, a TS document always opens as the scenario window, and the slot the server keeps the TS in is
// only used for the document API.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick, ref } from 'vue'
import { isTestCardLines, testScenarioStorageSlot } from '@/features/designer/center/graph/testCardLines'
import { cardDocumentContext } from '@/features/designer/inspector/openDocument'
import {
  requestTestCardScenario, testCardScenarioRequest, testCardScenarioRequested,
} from '@/features/designer/inspector/features/testSpec/testCardSession'
import TestCardWindowHost from '@/features/designer/inspector/features/testSpec/TestCardWindowHost.vue'

const fixture = vi.hoisted(() => ({ canOpen: null as any, openWindow: vi.fn(() => true) }))
vi.mock('@/features/designer/inspector/features/testSpec/useTestCard', async () => {
  const { computed } = await import('vue')
  return {
    useTestCard: (target: () => unknown) => computed(() => target()
      ? { canOpen: (kind: string) => kind === 'scenario' && fixture.canOpen.value, openWindow: fixture.openWindow, windows: {} }
      : null),
  }
})

const line = (slot: string, docId: string | null, docType: string | null = 'TS') => ({ slot, docId, docType })
const target = (nodeId: string, cardDocuments: object[] = [], flowId = 'f1') =>
  ({ projectId: 'p1', flowId, nodeId, nodeType: 'document', cardDocuments }) as never

describe('F81-9 test card lines', () => {
  it('knows a test card by a 시나리오 line or a TS / TSR document, and no other card', () => {
    expect(isTestCardLines([line('instruction', 'a'), line('report', 'b', 'TSR')])).toBe(true)
    expect(isTestCardLines([line('scenario', 'a', null)])).toBe(true)
    expect(isTestCardLines([line('report', 'b', 'tsr ')])).toBe(true)
    expect(isTestCardLines([line('instruction', 'a', 'T'), line('report', 'b', 'TR')])).toBe(false)
    expect(isTestCardLines([])).toBe(false)
    expect(isTestCardLines(null)).toBe(false)
  })
  it('names where the server keeps the TS: instruction while an instruction line carries it, else 시나리오', () => {
    expect(testScenarioStorageSlot([line('instruction', 'a'), line('scenario', 'a')], 'a')).toBe('instruction')
    expect(testScenarioStorageSlot([line('scenario', 'a')], 'a')).toBe('scenario')
    expect(testScenarioStorageSlot([line('instruction', 'other'), line('scenario', 'a')], 'a')).toBe('scenario')
    expect(testScenarioStorageSlot([line('instruction', 'a')], null)).toBe('scenario')
  })
  it('opens a TS from either line with the storage slot as the document API context (no screen owner mapping)', () => {
    const shared = target('gn_t', [line('instruction', 'ts'), line('scenario', 'ts'), line('report', 'tsr', 'TSR')])
    expect(cardDocumentContext(shared, 'ts', 'scenario')?.ownerSlot).toBe('instruction')
    expect(cardDocumentContext(shared, 'ts')?.ownerSlot).toBe('instruction')
    expect(cardDocumentContext(shared, 'tsr')?.ownerSlot).toBe('report')
    const scenarioOnly = target('gn_t', [line('scenario', 'ts'), line('report', 'tsr', 'TSR')])
    expect(cardDocumentContext(scenarioOnly, 'ts', 'instruction')?.ownerSlot).toBe('scenario')
    expect(cardDocumentContext(scenarioOnly, 'ts')?.ownerSlot).toBe('scenario')
    // Another card's instruction is left as it is.
    const task = target('gn_w', [line('instruction', 't', 'T'), line('report', 'tr', 'TR')])
    expect(cardDocumentContext(task, 't')?.ownerSlot).toBe('instruction')
  })
})

describe('F81-9 the Canvas 시나리오 button opens the scenario window', () => {
  let wrapper: VueWrapper<any> | null = null
  beforeEach(() => { fixture.canOpen = ref(false); fixture.openWindow.mockClear(); testCardScenarioRequest.value = null })
  afterEach(() => { wrapper?.unmount(); wrapper = null; testCardScenarioRequest.value = null })

  it('matches a request to its card only', () => {
    const request = { projectId: 'p1', flowId: 'f1', nodeKey: 'gn_t' }
    expect(testCardScenarioRequested(request, target('gn_t'))).toBe(true)
    expect(testCardScenarioRequested(request, target('gn_x'))).toBe(false)
    expect(testCardScenarioRequested(request, target('gn_t', [], 'f2'))).toBe(false)
    expect(testCardScenarioRequested(null, target('gn_t'))).toBe(false)
    expect(testCardScenarioRequested(request, null)).toBe(false)
  })
  it('opens the scenario window once the selected card is the requested one and its scenario can open', async () => {
    wrapper = mount(TestCardWindowHost, { props: { target: null } })
    requestTestCardScenario({ projectId: 'p1', flowId: 'f1', nodeKey: 'gn_t' })
    await wrapper.setProps({ target: target('gn_t') }); await flushPromises()
    expect(fixture.openWindow).not.toHaveBeenCalled()
    fixture.canOpen.value = true; await nextTick(); await flushPromises()
    expect(fixture.openWindow).toHaveBeenCalledTimes(1)
    expect(fixture.openWindow).toHaveBeenCalledWith('scenario')
    expect(testCardScenarioRequest.value).toBeNull()
  })
  it('opens at once when the requested card is already selected', async () => {
    fixture.canOpen.value = true
    wrapper = mount(TestCardWindowHost, { props: { target: target('gn_t') } })
    requestTestCardScenario({ projectId: 'p1', flowId: 'f1', nodeKey: 'gn_t' }); await nextTick(); await flushPromises()
    expect(fixture.openWindow).toHaveBeenCalledWith('scenario')
  })
  it('drops the request when another card is selected', async () => {
    fixture.canOpen.value = true
    wrapper = mount(TestCardWindowHost, { props: { target: null } })
    requestTestCardScenario({ projectId: 'p1', flowId: 'f1', nodeKey: 'gn_t' })
    await wrapper.setProps({ target: target('gn_other') }); await flushPromises()
    expect(testCardScenarioRequest.value).toBeNull()
    await wrapper.setProps({ target: target('gn_t') }); await flushPromises()
    expect(fixture.openWindow).not.toHaveBeenCalled()
  })
})
