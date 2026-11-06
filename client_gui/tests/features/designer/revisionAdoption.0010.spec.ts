import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { reactive } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import RevisionNotice from '@/features/designer/center/RevisionNotice.vue'
import { isNonLatestRevision } from '@/features/designer/center/graph/revisionView'
import { editableRevision } from '@/features/designer/center/edit/draftModel'
import { nodeTitleEdit } from '@/features/designer/center/graph/nodeTitle'
import { createFlowRunControl, createFlowRunState, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'

registerAppMessages(i18n)
const idle: FlowRunInfo = { runId: 'run', status: 'idle', revisionNo: 1, openNodeKeys: [] }
const refusal = () => Object.assign(new Error('Execution rules are undecided.'), {
  code: 'run_policy_undecided', status: 409,
  params: { revision_no: 2, undecided_policies: [{ policy: 'report_pair', rule_ref: 'Q-5' }] },
})
const node = (id: string) => ({
  id, kind: 'task', title: id, x: 0, y: 0, structureEditable: true,
  definition: { nodeType: 'document', docType: id === 'root' ? 'R' : 'T', paletteItemId: null, label: id, assignee: null },
})
const graph = (): FlowGraph => ({
  flowId: 'f', ownership: 'graph', viewMode: 'draft', latestRevisionNo: 1, viewRevisionNo: null,
  nodes: [node('root'), node('new-card')], edges: [{ id: 'edge', source: 'root', target: 'new-card', editable: true }],
  draft: { version: 1, editable: true, confirmable: true, baseRevisionNo: 1, blockedReason: null },
  run: { ...idle },
})
function sourceOf(): FlowGraphSource {
  return {
    isSample: false, loadGraph: vi.fn(async () => graph()),
    loadRun: vi.fn(async () => idle),
    startRun: vi.fn(async () => ({ ...idle, status: 'running', revisionNo: 2 })),
    stopRun: vi.fn(async () => idle), resumeRun: vi.fn(async () => idle),
    checkRevisionAdoption: vi.fn(async () => null),
    saveDraft: vi.fn(async (_p, _f, version) => ({ version: version + 1, keyMap: {} })),
    validateDraft: vi.fn(async () => ({ confirmable: true, issues: [] })),
    confirmDraft: vi.fn(async () => 2),
  }
}
function control(status: string | null = 'idle', draft = true) {
  const source = sourceOf()
  const runs = reactive<Record<string, FlowRunState>>({
    f: { ...createFlowRunState(), readState: 'read', run: status ? { ...idle, status } : null },
  })
  const prepare = vi.fn(async () => true)
  const save = vi.fn(async () => true)
  const ctl = createFlowRunControl({
    source, projectId: () => 'p', isOpen: () => true, prepare,
    hasDraftToConfirm: () => draft, saveDraftBeforeCheck: save, refresh: vi.fn(),
  }, runs)
  return { ctl, source, runs, prepare, save }
}

describe('pre-confirm adoption assessment', () => {
  it.each(['all', 'next'] as const)('refuses %s before confirmation or start', async mode => {
    const { ctl, source, runs, prepare } = control()
    vi.mocked(source.checkRevisionAdoption!).mockResolvedValue(refusal())
    expect(await ctl.run('f', mode)).toBe(false)
    expect(prepare).not.toHaveBeenCalled()
    expect(source.startRun).not.toHaveBeenCalled()
    expect(runs.f.error).toMatchObject({ kind: 'run', code: 'run_policy_undecided', status: 409, message: '', action: 'start', mode, stage: 'revision', outcome: 'rejected' })
    ctl.clear()
  })
  it('saves, assesses, confirms and then starts when allowed', async () => {
    const { ctl, source, prepare, save } = control()
    expect(await ctl.run('f', 'all')).toBe(true)
    expect(save.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(source.checkRevisionAdoption!).mock.invocationCallOrder[0])
    expect(vi.mocked(source.checkRevisionAdoption!).mock.invocationCallOrder[0]).toBeLessThan(prepare.mock.invocationCallOrder[0])
    expect(prepare.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(source.startRun!).mock.invocationCallOrder[0])
    ctl.clear()
  })
  it('retains the existing confirm/start flow when the assessment request fails', async () => {
    const { ctl, source, prepare } = control()
    vi.mocked(source.checkRevisionAdoption!).mockRejectedValue(new Error('offline'))
    expect(await ctl.run('f', 'all')).toBe(true)
    expect(prepare).toHaveBeenCalledOnce()
    expect(source.startRun).toHaveBeenCalledOnce()
    ctl.clear()
  })
  it.each(['running', 'stopping', 'paused', null])('does not assess a %s run', async status => {
    const { ctl, source, prepare } = control(status)
    expect(await ctl.run('f', 'all')).toBe(status === null)
    expect(source.checkRevisionAdoption).not.toHaveBeenCalled()
    expect(prepare).toHaveBeenCalledTimes(status === null ? 1 : 0)
    ctl.clear()
  })
  it('does not assess when no draft will be confirmed', async () => {
    const { ctl, source } = control('idle', false)
    expect(await ctl.run('f', 'next')).toBe(true)
    expect(source.checkRevisionAdoption).not.toHaveBeenCalled()
    ctl.clear()
  })
  it('never assesses or confirms an unsaved candidate', async () => {
    const { ctl, source, prepare, save } = control()
    save.mockResolvedValue(false)
    expect(await ctl.run('f', 'all')).toBe(false)
    expect(source.checkRevisionAdoption).not.toHaveBeenCalled()
    expect(prepare).not.toHaveBeenCalled()
    ctl.clear()
  })
})

const sessions: DesignerCenterSession[] = []
afterEach(() => { sessions.splice(0).forEach(center => center.dispose()); i18n.global.locale.value = 'ko' })

describe('Center draft preservation', () => {
  it.each(['all', 'next'] as const)('keeps cards, edges, editing and saved draft after refused %s and re-entry', async mode => {
    const source = sourceOf()
    vi.mocked(source.checkRevisionAdoption!).mockResolvedValue(refusal())
    const center = createDesignerCenterSession({ source })
    sessions.push(center)
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'Isolated' })
    await flushPromises()
    expect(await center.runFlow('f', mode)).toBe(false)
    await flushPromises()
    expect(source.confirmDraft).not.toHaveBeenCalled()
    expect(source.startRun).not.toHaveBeenCalled()
    expect(center.state.runs.f.error?.code).toBe('run_policy_undecided')
    const assertGraph = () => {
      const load = center.state.graphs.f
      expect(load.status).toBe('ready')
      if (load.status !== 'ready') throw new Error('not ready')
      expect(load.graph.nodes.map(n => n.id)).toEqual(['root', 'new-card'])
      expect(load.graph.edges.map(e => e.id)).toEqual(['edge'])
      expect(load.graph.draft?.editable).toBe(true)
      expect(center.activeRunMarks.value.lock).toBeNull()
    }
    assertGraph()
    expect(await center.runFlow('f', mode)).toBe(false)
    await flushPromises()
    assertGraph()
    center.closeFlow('f')
    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'Isolated' })
    await flushPromises()
    assertGraph()
    expect(source.confirmDraft).not.toHaveBeenCalled()
    expect(source.startRun).not.toHaveBeenCalled()
  })
  it('waits for a pending draft save before checking the server candidate', async () => {
    const source = sourceOf()
    let finish!: () => void
    vi.mocked(source.saveDraft!).mockImplementation((_p, _f, version) => new Promise(resolve => {
      finish = () => resolve({ version: version + 1, keyMap: {} })
    }))
    vi.mocked(source.checkRevisionAdoption!).mockResolvedValue(refusal())
    const center = createDesignerCenterSession({ source, draftSaveDelayMs: 10000 })
    sessions.push(center)
    center.setProject('p')
    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'Isolated' })
    await flushPromises()
    expect(center.removeEdge('f', 'edge')).toBe(true)
    const command = center.runFlow('f', 'all')
    await flushPromises()
    expect(source.saveDraft).toHaveBeenCalledOnce()
    expect(source.checkRevisionAdoption).not.toHaveBeenCalled()
    finish()
    expect(await command).toBe(false)
    expect(source.checkRevisionAdoption).toHaveBeenCalledOnce()
    expect(source.confirmDraft).not.toHaveBeenCalled()
  })
})

describe('non-latest revision notice and shared edit lock', () => {
  const past = (extra: Partial<FlowGraph> = {}): FlowGraph => ({
    ...graph(), viewMode: 'revision', viewRevisionNo: 1, latestRevisionNo: 2, draft: null, ...extra,
  })
  it.each(['ko', 'en', 'ja'] as const)('shows a pinned revision and the editing limit in %s', locale => {
    i18n.global.locale.value = locale
    const wrapper = mount(RevisionNotice, { props: { graph: past() }, global: { plugins: [i18n] } })
    const text = wrapper.get('[role="status"]').text()
    expect(text).toContain('1')
    expect(text).toContain('2')
    expect(text).not.toContain('gui.designer')
    expect(text).toMatch(/편집|Editing|編集/)
    wrapper.unmount()
  })
  it('uses neutral wording for a history view not pinned by the current run', () => {
    i18n.global.locale.value = 'en'
    const wrapper = mount(RevisionNotice, { props: { graph: past({ run: { ...idle, revisionNo: 2 } }) }, global: { plugins: [i18n] } })
    expect(wrapper.text()).toContain('Showing revision 1')
    expect(wrapper.text()).not.toContain('run-pinned')
    wrapper.unmount()
  })
  it.each([
    { viewRevisionNo: 2 }, { viewMode: 'draft' }, { viewRevisionNo: null }, { latestRevisionNo: null },
  ])('does not show a notice for %j', extra => {
    const current = past(extra)
    expect(isNonLatestRevision(current)).toBe(false)
    const wrapper = mount(RevisionNotice, { props: { graph: current }, global: { plugins: [i18n] } })
    expect(wrapper.find('[role="status"]').exists()).toBe(false)
    wrapper.unmount()
  })
  it('keeps structure and title editing locked for a past revision', () => {
    const current = past()
    expect(editableRevision(current)).toBeNull()
    const card = { ...current.nodes[0], titleState: { title: 'Title', version: 1 } } as typeof current.nodes[number]
    expect(nodeTitleEdit(current, card).editable).toBe(false)
    expect(editableRevision(past({ viewRevisionNo: 2 }))).not.toBeNull()
  })
})
