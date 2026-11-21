/**
 * where a card result's merge shows outside its tab (mockup v7): the Canvas card (the hourglass names
 * the card it waits for; nothing is added below the card; 병합 충돌 opens the card's tab), the Center column turning
 * "open this card's merge" into a selection and a tab request, the notification runs tab and the Git panel (no dead end:
 * "카드에서 처리합니다 [카드로 이동]").
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, inject } from 'vue'
import { mergeWindows, closeMergeWindow } from '@/features/designer/inspector/features/merge/mergeWindows'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import NotificationRunsTab from '@/features/designer/top/notifications/NotificationRunsTab.vue'
import { DesignerGitPanel } from '@/features/git'
import { G, P, createFakeGitSource, row, status, trConflicts } from '../git/fakeGitSource'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const t = (key: string, params: Record<string, unknown> = {}) => i18n.global.t(key, params)
const wrappers: VueWrapper<any>[] = []
beforeEach(() => { i18n.global.locale.value = 'ko' })
afterEach(() => { wrappers.splice(0).forEach((w) => w.unmount()); mergeWindows.splice(0); document.body.innerHTML = '' })

function heard(run: () => unknown): Promise<unknown[]> {
  const out: unknown[] = []
  const listener = (event: Event) => out.push((event as CustomEvent).detail)
  window.addEventListener('fg:open_card_merge', listener)
  return Promise.resolve(run()).then(async () => { await flushPromises(); window.removeEventListener('fg:open_card_merge', listener); return out })
}

describe('Canvas card', () => {
  const render = (data: Record<string, unknown>) => {
    const w = mount(FlowNodeCard, { props: { id: 'gn_2', data: { kind: 'task', title: 'Card', selected: false, state: null, ...data } as any }, global: { plugins: [i18n], stubs: { Handle: true } } })
    wrappers.push(w)
    return w
  }

  it('a waiting merge is an hourglass that names the card it waits for, in the head row only', () => {
    const w = render({ integration: 'pending', integrationBlockedTitle: '업로드 대기열 이전', integrationBlockedConflict: true })
    const mark = w.get('.flow-node-head [data-test="designer-canvas-node-integration"]')
    expect(mark.attributes('title')).toBe(t('gui.designer.center.integration.waiting_for', { title: '업로드 대기열 이전' }))
    expect(mark.attributes('title')).toBe('‘업로드 대기열 이전’ 카드 충돌 해결을 기다리는 중')
    // behind a card that is merging (not conflicting) it says so
    const merging = render({ integration: 'pending', integrationBlockedTitle: '작업C' })
    expect(merging.get('[data-test="designer-canvas-node-integration"]').attributes('title')).toBe('‘작업C’ 카드의 병합이 끝나기를 기다리는 중')
    expect(w.find('.flow-node-footer').exists()).toBe(false)
    expect(w.findAll('.flow-node-card > *').map((el) => el.classes()[0])).not.toContain('flow-node-below')
  })

  it('without a named card it waits for its turn', () => {
    expect(render({ integration: 'pending' }).get('[data-test="designer-canvas-node-integration"]').attributes('title'))
      .toBe(t('gui.designer.center.integration.waiting_turn'))
  })

  it('the conflict button keeps its footer place and names the card tab', async () => {
    const w = render({ integration: 'conflict' })
    const button = w.get('.flow-node-footer [data-test="designer-canvas-node-conflict"]')
    expect(button.attributes('title')).toBe(`${t('gui.designer.center.integration.conflict')} · ${t('gui.designer.center.integration.open_conflict')}`)
    await button.trigger('click')
    expect(w.emitted('conflict')).toEqual([['gn_2']])
  })

  it('the Center sends the card conflict to the card tab, never to the Git tool panel', () => {
    const source = readFileSync(resolve(__dirname, '../../../src/features/designer/center/DesignerCenter.vue'), 'utf8')
    expect(source).toMatch(/@node-conflict="openCardMerge\(activeTab\.flowId, \$event\)"/)
    expect(source).not.toMatch(/@node-conflict="emit\('show-tool'/)
    expect(source).toMatch(/new CustomEvent\('fg:open_card_merge', \{ detail: \{ flowId, nodeKey \} \}\)/)
  })

  it('the mapping carries the blocking card title for a waiting merge only', () => {
    const graph = {
      flowId: 'f', edges: [],
      nodes: [
        { id: 'gn_4', kind: 'task', title: '업로드 대기열 이전', x: 0, y: 0, nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, integrationStatus: 'conflict', integrationReason: 'merge_session:31' } },
        { id: 'gn_2', kind: 'task', title: '승인 화면', x: 200, y: 0, nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, integrationStatus: 'pending', integrationReason: 'session_busy', integrationBlockedBy: 'gn_4' } },
      ],
    } as unknown as FlowGraph
    const [conflict, waiting] = toVueFlowNodes(graph, null)
    expect(waiting.data!.integration).toBe('pending')
    expect(waiting.data!.integrationBlockedTitle).toBe('업로드 대기열 이전')
    expect(waiting.data!.integrationBlockedConflict).toBe(true)
    expect(conflict.data!.integrationBlockedTitle).toBeUndefined()
  })
})

describe('Center column: one "open this card merge" event', () => {
  function fakeCenter() {
    const graph = {
      flowId: 'f1', edges: [],
      nodes: [
        { id: 'gn_4', kind: 'task', title: 'Four', x: 0, y: 0, nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: null, integrationStatus: 'conflict', integrationReason: 'merge_session:31' } },
      ],
    }
    return {
      state: { projectId: 'flowgate', activeFlowId: 'f1', tabs: [{ flowId: 'f1', name: 'F1' }], graphs: { f1: { status: 'ready', graph } } },
      activeSelection: { value: null }, activeGraphLoad: { value: null }, activeView: { value: null }, canvasSize: { value: null }, activeNodeSizes: { value: {} },
      activeSelectedNode: { value: null }, canEditNodeTitles: false,
      runMarks: () => ({ lock: null, nodeRuns: new Map() }),
      activateFlow: vi.fn(), openFlow: vi.fn(), selectNode: vi.fn(), commitViewport: vi.fn(), recordViewport: vi.fn(), refreshFlow: vi.fn(),
    }
  }
  let provided: any = null
  const DockStub = defineComponent({ setup() { provided = inject(Symbol.for('flowgate.designer.bottomDockInspector')); return () => h('div') } })

  async function mountColumn(center: ReturnType<typeof fakeCenter>) {
    const w = mount(DesignerCenterColumn, { props: { center: center as any, bottomHeight: 200 }, global: { plugins: [i18n], stubs: { DesignerWorkspace: true, DesignerResizeHandle: true, DesignerBottomDock: DockStub } } })
    wrappers.push(w)
    await flushPromises()
    return w
  }

  it('a node key selects that card and asks the Inspector for its merge tab', async () => {
    const center = fakeCenter()
    await mountColumn(center)
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f1', nodeKey: 'gn_4' } }))
    expect(center.activateFlow).toHaveBeenCalledWith('f1')
    await flushPromises()
    await flushPromises()
    expect(center.selectNode).toHaveBeenCalledWith('f1', 'gn_4')
    expect(mergeWindows[0].target()).toMatchObject({ flowId: 'f1', nodeId: 'gn_4' })
    const first = mergeWindows[0].raise
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f1', nodeKey: 'gn_4' } }))
    await flushPromises()
    expect(mergeWindows).toHaveLength(1)
    expect(mergeWindows[0].raise).toBe(first + 1)
    // Git's merge id and the row/window event resolve to the same already-open card.
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f1', mergeId: 31 } }))
    await flushPromises()
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f1', nodeKey: 'gn_4' } }))
    await flushPromises()
    expect(mergeWindows).toHaveLength(1)
    expect(mergeWindows[0].raise).toBe(first + 3)
  })

  it('a Git merge id finds the card whose integration is that session; another Flow is opened first', async () => {
    const center = fakeCenter()
    await mountColumn(center)
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f1', mergeId: 31 } }))
    await flushPromises()
    expect(center.selectNode).toHaveBeenCalledWith('f1', 'gn_4')
    window.dispatchEvent(new CustomEvent('fg:open_card_merge', { detail: { flowId: 'f9', nodeKey: 'gn_1', flowName: 'Nine' } }))
    expect(center.openFlow).toHaveBeenCalledWith({ projectId: 'flowgate', flowId: 'f9', flowName: 'Nine' })
    expect(center.selectNode).not.toHaveBeenCalledWith('f9', 'gn_1') // waits for the graph read
  })
})

describe('notification runs tab: merge items', () => {
  const items = [
    { flowId: 'f1', flowTitle: 'F1', flowName: '편의성', nodeKey: 'gn_4', title: '업로드 대기열 이전', state: 'needs_choice', blockedBy: null },
    { flowId: 'f1', flowTitle: 'F1', flowName: '편의성', nodeKey: 'gn_2', title: '승인 화면', state: 'waiting', blockedBy: { nodeKey: 'gn_4', title: '업로드 대기열 이전' } },
  ]

  it('lists them (red: a choice is needed, blue: waiting) and [열기] asks for that card', async () => {
    const w = mount(NotificationRunsTab, { props: { rows: [], projectName: 'Alpha', busy: [], mergeItems: items as any }, global: { plugins: [i18n] } })
    wrappers.push(w)
    expect(w.text()).not.toContain(t('gui.designer.top.notifications.empty_runs'))
    const rows = w.findAll('[data-test="notification-merge"]')
    expect(rows.map((r) => r.get('[data-test="notification-merge-text"]').text())).toEqual([
      '‘업로드 대기열 이전’ 카드 병합 충돌 — 선택 필요',
      '‘승인 화면’ 카드 병합 대기 (‘업로드 대기열 이전’ 뒤)',
    ])
    // mockup v7: the conflict row is framed red with the conflict mark; the waiting one carries the hourglass
    expect(rows[0].classes()).toContain('is-choice')
    expect(rows[1].classes()).not.toContain('is-choice')
    expect(rows[0].get('.notification-status').classes()).toContain('is-fail')
    expect(rows[1].get('.notification-status').classes()).toContain('is-run')
    expect(rows[0].get('.notification-status path').attributes('d')).toBe('M7 4l10 16 M17 4L7 20 M4 9h4 M16 15h4')
    expect(rows[1].get('.notification-status path').attributes('d')).toContain('M7 3h10 M7 21h10')
    expect(rows[0].get('[data-test="notification-merge-open"]').text()).toBe(t('gui.designer.top.notifications.merge.open'))
    await rows[1].get('[data-test="notification-merge-open"]').trigger('click')
    expect(w.emitted('open-merge')).toEqual([[items[1]]])
  })

  it('with neither runs nor merges the empty line stays', () => {
    const w = mount(NotificationRunsTab, { props: { rows: [], projectName: 'Alpha', busy: [] }, global: { plugins: [i18n] } })
    wrappers.push(w)
    expect(w.text()).toContain(t('gui.designer.top.notifications.empty_runs'))
  })
})

describe('Git panel: a card result merge points to the card', () => {
  it('shows "카드에서 처리합니다" and [카드로 이동] (the merge id), no resolve buttons', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: 'open' })] }), conflicts: { ...trConflicts(), mergeId: 7, graphMerge: true } })
    const w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(w)
    await flushPromises()
    const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
    expect(q('git-conflict-card-handled')?.querySelector('b')?.textContent?.trim()).toBe(t('gui.git.conflict.card_handled'))
    expect(q('git-conflict-card-handled')?.textContent).toContain(t('gui.git.conflict.card_handled_desc'))
    expect(q('git-conflict-card')?.classList.contains('danger')).toBe(false)
    expect(q('git-conflict-unsupported')).toBeNull()
    expect([...document.body.querySelectorAll('[data-test="git-conflict-card"] button')].map((b) => b.getAttribute('data-test'))).toEqual(['git-conflict-open-card'])
    const detail = await heard(() => q('git-conflict-open-card')!.click())
    expect(detail).toEqual([{ flowId: G, mergeId: 7 }])
  })

  it('a TR revert conflict keeps its one line', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row({ reviewState: null })] }), conflicts: trConflicts() })
    const w = mount(DesignerGitPanel, { props: { projectId: P, flow: { groupId: G, name: 'Flow' }, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(w)
    await flushPromises()
    expect(document.body.querySelector('[data-test="git-conflict-unsupported"]')).not.toBeNull()
    expect(document.body.querySelector('[data-test="git-conflict-card-handled"]')).toBeNull()
  })
})
