import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import type { FlowWorkBase } from '@/features/project'
import { G, P, createFakeGitSource, gitError, networkError, status } from './fakeGitSource'
import { createFakeWorktreeSource, flowWorkBase, manualMergeView, worktreeFile, worktreeStatus } from './fakeWorktreeSource'

// 0107 T#2 — D §6.1's five rows, using different project and Flow bases. Both displays
// consume one session; window disposal and existing refresh entry points are tested here.
registerAppMessages(i18n)
const FLOW = { groupId: G, name: 'A' }
const FLOW_B = { groupId: 'p.m.0002', name: 'B' }
const BASE = 'feature/v0.2.0'
let wrapper: VueWrapper | null = null
beforeEach(() => { i18n.global.locale.value = 'ko' })
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

function setup(projectBase: string | null = 'main') {
  const wt = createFakeWorktreeSource({ workBase: { workBaseRef: BASE, effectiveWorkBaseRef: BASE } })
  const fake = createFakeGitSource({ status: status({ baseBranch: projectBase }) }, { worktree: wt.source })
  return { wt, fake }
}
type Fixture = ReturnType<typeof setup>
async function render(t: Fixture, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: t.fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}
const q = (name: string) => document.body.querySelector<HTMLElement>(`[data-test="${name}"]`)
const text = (name: string) => q(name)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
async function click(el: HTMLElement | null) {
  expect(el).not.toBeNull()
  el!.click()
  await flushPromises()
}
const entry = (tab: string) => document.body.querySelector<HTMLElement>(`[data-test="git-worktree-entry"][data-tab="${tab}"]`)
const tab = (name: string) => document.body.querySelector<HTMLElement>(`[data-test="git-window-tab"][data-tab="${name}"]`)
const shell = () => q('git-window')?.closest<HTMLElement>('.ui-modeless-panel') ?? null
const refresh = () => (wrapper!.vm as unknown as { refresh(): Promise<void> }).refresh()
const reads = (t: Fixture) => t.wt.source.loadWorkBase.mock.calls.length
function expectBase(value: string, label = '플로우 베이스') {
  expect(text('git-base-branch')).toBe(`${label} · ${value}`)
  expect(text('git-status-base')).toBe(value)
  expect(q('git-status-base')!.previousElementSibling?.textContent).toBe(label)
}
function expectDiscarded() {
  for (const name of ['git-worktree', 'git-window', 'git-status', 'git-status-base', 'git-status-worktree', 'git-status-target', 'git-status-state', 'git-status-changed', 'git-status-staged', 'git-status-ahead']) {
    expect(q(name), name).toBeNull()
  }
  expect(document.body.querySelector('.ui-modeless-panel')).toBeNull()
}
function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

describe('D §6.1 — active Flow with effective base', () => {
  it('header and status share the effective Flow base; other status fields and targets keep their sources', async () => {
    const t = setup()
    t.wt.db.status = worktreeStatus({ branch: 'worktree-A', files: [worktreeFile('a'), worktreeFile('b', { staged: 'M' })] })
    t.wt.db.merge = manualMergeView({ aheadOfTarget: 4 })
    await render(t)
    expect(text('git-work-branch')).toBe('flowgate_v02_0008')
    await click(entry('status'))
    expectBase(BASE)
    expect(['git-status-worktree', 'git-status-target', 'git-status-state'].map(text)).toEqual(['worktree-A', 'v0.2 (기본)', '4개 커밋 미반영'])
    expect(['git-status-changed', 'git-status-staged', 'git-status-ahead'].map(text)).toEqual(['2', '1', '4'])
    expect(t.fake.db.status?.baseBranch).toBe('main')
    expect(t.wt.db.merge.defaultTarget).toBe('v0.2')
    expect(reads(t)).toBe(1)
    await click(tab('files'))
    await click(tab('status'))
    await click(shell()!.querySelector('[data-modeless-action="close"]'))
    await click(entry('status'))
    expectBase(BASE)
    expect(reads(t)).toBe(1)
  })

  it.each([false, true])('Worktree unavailable=%s does not supply or hide the Flow base; legacy null uses the server effective ref', async (unavailable) => {
    const t = setup()
    t.wt.db.status = worktreeStatus({ worktreeAvailable: !unavailable, branch: null })
    t.wt.db.workBase = { workBaseRef: null, effectiveWorkBaseRef: BASE }
    await render(t)
    await click(entry('status'))
    expectBase(BASE)
    expect(text('git-status-worktree')).toBe('—')
    expect(t.wt.db.workBase.workBaseRef).toBeNull()
  })

  it('switches A → B → A with both displays agreeing, including a Flow base equal to the project base', async () => {
    const t = setup()
    const w = await render(t)
    await click(entry('status'))
    expectBase(BASE)
    t.wt.db.workBase = { effectiveWorkBaseRef: 'main' }
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    await click(entry('status'))
    expectBase('main')
    t.wt.db.workBase = { effectiveWorkBaseRef: BASE }
    await w.setProps({ flow: FLOW })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    await click(entry('status'))
    expectBase(BASE)
    expect(t.fake.db.status?.baseBranch).toBe('main')
  })
})

describe('D §6.1 — active Flow with loading, failed or missing base', () => {
  it('loading clears both displays; header refresh and external refresh share the running request', async () => {
    const t = setup()
    await render(t)
    await click(entry('status'))
    const pending = deferred<FlowWorkBase>()
    t.wt.source.loadWorkBase.mockImplementationOnce(() => pending.promise)
    await click(q('git-refresh'))
    expectBase('—')
    const finished = refresh()
    await flushPromises()
    expectBase('—')
    expect(reads(t)).toBe(2)
    pending.resolve(flowWorkBase(G, { effectiveWorkBaseRef: 'release/new' }))
    await finished
    await flushPromises()
    expectBase('release/new')
    expect(t.fake.db.status?.baseBranch).toBe('main')
  })

  it.each([
    ['403', () => gitError(403, 'forbidden')],
    ['404', () => gitError(404, 'not_found')],
    ['network', () => networkError()],
  ])('%s failure shows — in both existing rows and short failure feedback; refresh recovers', async (_name, error) => {
    const t = setup()
    await render(t)
    await click(entry('status'))
    t.wt.source.loadWorkBase.mockRejectedValueOnce(error())
    await refresh()
    await flushPromises()
    expectBase('—')
    expect(q('git-status-failed')).not.toBeNull()
    expect(text('git-header-line')).not.toBe('')
    expect(reads(t)).toBe(2)
    await refresh()
    await flushPromises()
    expectBase(BASE)
    expect(q('git-status-failed')).toBeNull()
  })

  it.each([null, '', '  '])('missing effective value %s does not synthesize main or project base', async (value) => {
    const t = setup()
    t.wt.db.workBase = { workBaseRef: null, effectiveWorkBaseRef: value }
    await render(t)
    await click(entry('status'))
    expectBase('—')
    expect(q('git-status-failed')).toBeNull()
    expect(t.fake.db.status?.baseBranch).toBe('main')
  })

  it('late A success after switching to B cannot replace either display', async () => {
    const t = setup()
    const slow = deferred<FlowWorkBase>()
    t.wt.source.loadWorkBase.mockImplementationOnce(() => slow.promise)
    const w = await render(t)
    t.wt.db.workBase = { effectiveWorkBaseRef: 'release/B' }
    await w.setProps({ flow: FLOW_B })
    await flushPromises()
    await click(entry('status'))
    expectBase('release/B')
    slow.resolve(flowWorkBase(G, { effectiveWorkBaseRef: 'stale/A' }))
    await flushPromises()
    expectBase('release/B')
  })
})

describe('D §6.1 — no Flow, with configured or unset project base', () => {
  it.each([
    ['main', false], ['main', true], [null, false], [null, true],
  ] as const)('project base=%s, minimized=%s: discard window and status; no reads or opening without Flow; no automatic reopen', async (projectBase, minimized) => {
    const t = setup(projectBase)
    const w = await render(t)
    await click(entry('status'))
    if (minimized) await click(shell()!.querySelector('[data-modeless-action="minimize"]'))
    // A queued click on an old entry must not reopen the closed gate.
    const oldEntry = entry('status')!
    await w.setProps({ flow: null })
    await flushPromises()
    expectDiscarded()
    expect(text('git-base-branch')).toBe(`프로젝트 베이스 · ${projectBase ?? '—'}`)
    const count = reads(t)
    oldEntry.click()
    await refresh()
    await w.setProps({ active: false })
    await w.setProps({ active: true })
    await flushPromises()
    expectDiscarded()
    expect(reads(t)).toBe(count)
    const pending = deferred<FlowWorkBase>()
    t.wt.source.loadWorkBase.mockImplementationOnce(() => pending.promise)
    await w.setProps({ flow: FLOW })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    expect(text('git-base-branch')).toBe('플로우 베이스 · —')
    await click(entry('status'))
    expectBase('—')
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
    pending.resolve(flowWorkBase(G, { effectiveWorkBaseRef: BASE }))
    await flushPromises()
    expectBase(BASE)
  })
})

describe('D §6.1 — Git unlinked or permission/status failure', () => {
  it.each(['off', '403', 'read error'] as const)('%s discards the open window and clears the Flow base; reopening the gate never reopens the window', async (reason) => {
    const t = setup()
    await render(t)
    await click(entry('status'))
    await click(shell()!.querySelector('[data-modeless-action="minimize"]'))
    if (reason === 'off') t.fake.db.status = status({ enabled: false, baseBranch: 'main' })
    else t.fake.source.loadStatus.mockRejectedValueOnce(gitError(reason === '403' ? 403 : 500, 'git_error'))
    await refresh()
    await flushPromises()
    expectDiscarded()
    expect(text('git-base-branch')).toBe('플로우 베이스 · —')
    if (reason === 'off') {
      expect(q('git-unlinked')).not.toBeNull()
      expect(q('git-head')!.style.display).toBe('none')
    } else expect(q('git-status-error')).not.toBeNull()
    const count = reads(t)
    t.fake.db.status = status({ baseBranch: 'main' })
    await refresh()
    await flushPromises()
    expect(q('git-window')).toBeNull()
    expect(reads(t)).toBe(count + 1)
    await click(entry('status'))
    expectBase(BASE)
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
  })

  it('project change discards an open minimized window and its values', async () => {
    const t = setup()
    const w = await render(t)
    await click(entry('status'))
    await click(shell()!.querySelector('[data-modeless-action="minimize"]'))
    t.wt.db.workBase = { projectId: 'p2', effectiveWorkBaseRef: 'release/p2' }
    await w.setProps({ projectId: 'p2', flow: { groupId: 'p2.m.0001', name: 'P2' } })
    await flushPromises()
    expect(q('git-window')).toBeNull()
    expect(q('git-status')).toBeNull()
    await click(entry('status'))
    expectBase('release/p2')
    expect(shell()!.classList.contains('is-minimized')).toBe(false)
  })
})

describe('existing refresh and locale contracts', () => {
  it('reactivation reads once, repeated renders and opening/switching tabs read nothing; remount reads fresh', async () => {
    const t = setup()
    const w = await render(t)
    await w.setProps({ active: false })
    t.wt.db.workBase = { effectiveWorkBaseRef: 'saved/0104' }
    await w.setProps({ active: true })
    await flushPromises()
    expect(reads(t)).toBe(2)
    await click(entry('status'))
    expectBase('saved/0104')
    await w.setProps({ readOnly: true })
    await click(tab('files'))
    await click(tab('status'))
    expect(reads(t)).toBe(2)
    w.unmount()
    wrapper = null
    await render(t)
    expect(reads(t)).toBe(3)
    expect(q('git-window')).toBeNull()
    await click(entry('status'))
    expectBase('saved/0104')
  })

  it.each([
    ['ko', '플로우 베이스', '프로젝트 베이스'],
    ['en', 'Flow base', 'Project base'],
    ['ja', 'フローベース', 'プロジェクトベース'],
  ] as const)('%s labels distinguish Flow and project scope', async (locale, flowLabel, projectLabel) => {
    i18n.global.locale.value = locale
    const t = setup()
    const w = await render(t)
    await click(entry('status'))
    expectBase(BASE, flowLabel)
    await w.setProps({ flow: null })
    await flushPromises()
    expectDiscarded()
    expect(text('git-base-branch')).toBe(`${projectLabel} · main`)
  })
})
