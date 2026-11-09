import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, type Pinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
const mocks = vi.hoisted(() => ({ runCurrent: vi.fn() }))
vi.mock('@/features/document', async (original) => ({
  ...(await original<typeof import('@/features/document')>()),
  runCurrentRead: (...a: unknown[]) => mocks.runCurrent(...a),
}))
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { WorktreeStatus } from '@/features/git/types'
import { INSPECTOR_REFRESH_COALESCE_MS } from '@/features/designer/inspector/useInspectorResource'
import { installProjectEventStream, removeProjectEventStream, type ProjectEventStream } from '../designer/inspector/support/projectEventStream'
import { G, P, createFakeGitSource } from './fakeGitSource'
import { createFakeWorktreeSource, worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// flowgate.v02test.0013 — the worktree area reads again when an AI run of the shown Flow ends. A card
// worker or chat run changes working files with no Git action of the panel; before this the area kept
// the E1 answer read before the run ("변경 파일 0" over 16 uncommitted files) until a manual refresh or
// a tab switch. Frames of another Flow are ignored, a burst is one read, a reconnect reads again and
// a Flow switch drops both a pending signal and a late answer of the old Flow.
registerAppMessages(i18n)

const FLOW = { groupId: G, name: '인증 오류 조사' }
const FLOW_B = { groupId: 'p.m.0002', name: '다른 Flow' }

let wrapper: VueWrapper | null = null
let pinia: Pinia
let stream: ProjectEventStream

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  pinia = createPinia()
  stream = installProjectEventStream()
  mocks.runCurrent.mockReset()
  mocks.runCurrent.mockResolvedValue({ runId: 'r1', status: 'idle', nodes: [] })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  removeProjectEventStream()
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((res) => (resolve = res))
  return { promise, resolve }
}

const files = (n: number) => Array.from({ length: n }, (_, i) => worktreeFile(`src/f${i}.ts`))
const text = (sel: string) =>
  document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
const changed = () => text('git-worktree-summary-changed')
// Past the coalesce window, then let the reads answer.
const settle = async () => {
  await new Promise((resolve) => setTimeout(resolve, INSPECTOR_REFRESH_COALESCE_MS + 30))
  await flushPromises()
}

async function mountPanel(initial: WorktreeStatus) {
  const wt = createFakeWorktreeSource({ status: initial })
  const fake = createFakeGitSource({}, { worktree: wt.source })
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: fake.source },
    attachTo: document.body,
    global: { plugins: [i18n, pinia] },
  })
  await flushPromises()
  stream.open()
  await flushPromises()
  return { wt, fake, w: wrapper }
}

describe('worktree area follows AI runs of its Flow (flowgate.v02test.0013)', () => {
  it('ai_invoke_finished of the shown Flow reads E1 again: the count goes from 0 to the files the run left', async () => {
    const { wt } = await mountPanel(worktreeStatus({ files: [] }))
    expect(changed()).toBe('0')
    const before = wt.source.loadStatus.mock.calls.length
    // The run changed 16 files and committed nothing.
    wt.db.status = worktreeStatus({ files: files(16), worktreeToken: 'wt-2' })
    stream.emit('ai_invoke_finished', { project: P, groupId: G, docId: `${G}.0002-CH` })
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(before + 1)
    expect(wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G)
    expect(changed()).toBe('16')
  })

  it('chat_run_changes of the shown Flow reads again as well', async () => {
    const { wt } = await mountPanel(worktreeStatus({ files: [] }))
    wt.db.status = worktreeStatus({ files: files(3), worktreeToken: 'wt-2' })
    stream.emit('chat_run_changes', { project: P, groupId: G, payload: { group_id: G, files_changed: 3 } })
    await settle()
    expect(changed()).toBe('3')
  })

  it('a frame of another Flow, or one with no group, reads nothing', async () => {
    const { wt } = await mountPanel(worktreeStatus({ files: [] }))
    const before = wt.source.loadStatus.mock.calls.length
    wt.db.status = worktreeStatus({ files: files(2), worktreeToken: 'wt-2' })
    stream.emit('ai_invoke_finished', { project: P, groupId: FLOW_B.groupId })
    stream.emit('chat_run_changes', { project: P, groupId: null })
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(before)
    expect(changed()).toBe('0')
  })

  it('a burst of run-end events is one read (E1 / E8 / E10 once each)', async () => {
    const { wt } = await mountPanel(worktreeStatus({ files: [] }))
    const e1 = wt.source.loadStatus.mock.calls.length
    const e8 = wt.source.loadManualMerge.mock.calls.length
    const e10 = wt.source.loadMergeResults.mock.calls.length
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    stream.emit('chat_run_changes', { project: P, groupId: G })
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(e1 + 1)
    expect(wt.source.loadManualMerge.mock.calls.length).toBe(e8 + 1)
    expect(wt.source.loadMergeResults.mock.calls.length).toBe(e10 + 1)
  })

  it('a reconnect of the event stream (events may be missing) reads again', async () => {
    const { wt } = await mountPanel(worktreeStatus({ files: [] }))
    wt.db.status = worktreeStatus({ files: files(4), worktreeToken: 'wt-2' })
    stream.reconnect()
    await settle()
    expect(changed()).toBe('4')
  })

  it('reads while the tab is hidden too, so the count badge stays right', async () => {
    const { wt, w } = await mountPanel(worktreeStatus({ files: [] }))
    await w.setProps({ active: false })
    await flushPromises()
    const before = wt.source.loadStatus.mock.calls.length
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(before + 1)
  })

  it('Flow switch: an answer of the old Flow arriving late is dropped, and old-Flow frames no longer count', async () => {
    const { wt, w } = await mountPanel(worktreeStatus({ files: files(1) }))
    const late = deferred<WorktreeStatus>()
    wt.source.loadStatus.mockImplementationOnce(() => late.promise)
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    await settle()
    expect(wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(G)
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    expect(wt.source.loadStatus.mock.calls.at(-1)?.[0]).toBe(FLOW_B.groupId)
    expect(changed()).toBe('1')
    late.resolve(worktreeStatus({ files: files(9), worktreeToken: 'wt-old' }))
    await flushPromises()
    expect(changed()).toBe('1')
    const before = wt.source.loadStatus.mock.calls.length
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(before)
  })

  it('Flow switch inside the coalesce window drops the pending signal of the old Flow', async () => {
    const { wt, w } = await mountPanel(worktreeStatus({ files: files(1) }))
    stream.emit('ai_invoke_finished', { project: P, groupId: G })
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    const before = wt.source.loadStatus.mock.calls.length
    await settle()
    expect(wt.source.loadStatus.mock.calls.length).toBe(before)
  })
})
