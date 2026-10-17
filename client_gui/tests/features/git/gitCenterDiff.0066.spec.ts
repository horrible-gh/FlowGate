import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { FlowFileDiff } from '@/features/project'
import GitCenterDiffView from '@/features/git/GitCenterDiffView.vue'
import { createGitCenterDiff, type GitCenterDiffSession } from '@/features/git/gitCenterDiff'
import type { GitDiffIntent } from '@/features/git/types'
import { G, P, gitError, networkError } from './fakeGitSource'

// flowgate.v02.0066 T#6 — C29: the Center Diff session and screen (0066 D#2 §3-10, §6-6): which
// intents open, latest answer only, closing, and every state / error line. The server message
// is never shown.
// flowgate.v02.0067 T#4 — the screen is the Git modeless `Diff` tab detail: `‹ 목록으로` (raised as
// `back`), `파일 경로` with the full path once and `비교 기준`, then the same states; no ✕ / ⟳ of its
// own. The part is internal now (not on the public surface).
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null
const sessions: GitCenterDiffSession[] = []

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  sessions.splice(0).forEach((s) => s.dispose())
  document.body.innerHTML = ''
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

const side = (content: string | null, overrides: Partial<FlowFileDiff['old']> = {}) => ({
  exists: content !== null,
  binary: false,
  truncated: false,
  size: content?.length ?? 0,
  content,
  ...overrides,
})

function fileDiff(path: string, oldText: string | null, newText: string | null, overrides: Partial<FlowFileDiff> = {}): FlowFileDiff {
  return {
    groupId: G,
    branch: 'flowgate_v02_0008',
    commit: null,
    baseBranch: 'v0.2',
    mergeBase: 'f1',
    path,
    status: 'M',
    old: side(oldText),
    new: side(newText),
    ...overrides,
  }
}

const intent = (path: string, overrides: Partial<GitDiffIntent> = {}): GitDiffIntent => ({
  projectId: P,
  groupId: G,
  mergeId: null,
  path,
  status: 'M',
  origin: 'worktree',
  diffSource: 'group',
  compareBase: null,
  ...overrides,
})

/** An error the way a features/project loader throws it: a plain Error keeping the transport error as `cause`. */
const loaderError = (transport: unknown) => Object.assign(new Error('Failed to load the file diff.'), { cause: transport })

function setup(load: (path: string, base: string | null) => Promise<FlowFileDiff>) {
  const loadFileDiff = vi.fn((_pid: string, _gid: string, path: string, base: string | null) => load(path, base))
  const session = createGitCenterDiff({ loadFileDiff })
  sessions.push(session)
  session.setContext(P, G)
  return { session, loadFileDiff }
}

async function mountView(session: GitCenterDiffSession) {
  wrapper = mount(GitCenterDiffView, { props: { session }, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

describe('gitCenterDiff — what opens and what applies', () => {
  it('opens a worktree intent of the current context with base only when compareBase is set', async () => {
    const { session, loadFileDiff } = setup(async (path) => fileDiff(path, 'a\n', 'b\n'))
    session.open(intent('src/a.ts'))
    await flushPromises()
    expect(loadFileDiff).toHaveBeenCalledWith(P, G, 'src/a.ts', null)
    expect(session.state.load).toBe('ready')
    session.open(intent('src/b.ts', { compareBase: 'release' }))
    await flushPromises()
    expect(loadFileDiff).toHaveBeenLastCalledWith(P, G, 'src/b.ts', 'release')
    expect(session.state.open?.path).toBe('src/b.ts')
  })

  it('ignores 0033 intents and intents of another project or group', async () => {
    const { session, loadFileDiff } = setup(async (path) => fileDiff(path, 'a', 'b'))
    session.open(intent('x', { origin: 'conflict', diffSource: 'base', compareBase: undefined }))
    session.open(intent('x', { origin: 'review', diffSource: 'review', mergeId: 7, compareBase: undefined }))
    session.open(intent('x', { origin: 'dirty', groupId: null, diffSource: 'base', compareBase: undefined }))
    session.open(intent('x', { projectId: 'other' }))
    session.open(intent('x', { groupId: 'p.m.0002' }))
    await flushPromises()
    expect(loadFileDiff).not.toHaveBeenCalled()
    expect(session.state.open).toBeNull()
  })

  it('quick clicks: only the last answer applies; an answer after close is dropped', async () => {
    const answers: Record<string, { promise: Promise<FlowFileDiff>; resolve: (value: FlowFileDiff) => void }> = {
      a: deferred<FlowFileDiff>(),
      b: deferred<FlowFileDiff>(),
      c: deferred<FlowFileDiff>(),
    }
    const { session } = setup((path) => answers[path].promise)
    session.open(intent('a'))
    session.open(intent('b'))
    answers.b.resolve(fileDiff('b', 'x', 'y'))
    answers.a.resolve(fileDiff('a', 'x', 'z'))
    await flushPromises()
    expect(session.state.diff?.path).toBe('b')
    session.open(intent('c'))
    session.close()
    answers.c.resolve(fileDiff('c', '1', '2'))
    await flushPromises()
    expect(session.state.open).toBeNull()
    expect(session.state.diff).toBeNull()
  })

  it('closes on a context change; refresh reads the same group again, or closes a target comparison diff', async () => {
    const { session, loadFileDiff } = setup(async (path) => fileDiff(path, 'a', 'b'))
    session.open(intent('a'))
    await flushPromises()
    session.refresh({ groupId: 'p.m.0002', closeCompare: false })
    session.refresh({ groupId: G, closeCompare: true })
    await flushPromises()
    // A worktree diff (no compareBase) is not closed by the compare-close request; it reads again.
    expect(loadFileDiff).toHaveBeenCalledTimes(2)
    session.open(intent('b', { compareBase: 'release' }))
    await flushPromises()
    session.refresh({ groupId: G, closeCompare: true })
    expect(session.state.open).toBeNull()
    session.open(intent('a'))
    await flushPromises()
    session.setContext(P, 'p.m.0002')
    expect(session.state.open).toBeNull()
    session.setContext(P, G)
    session.open(intent('a'))
    session.setContext(null, null)
    expect(session.state.open).toBeNull()
  })
})

describe('GitCenterDiffView — states and error lines (C29)', () => {
  it('reading, then the line diff with old / new numbers and the compared bases', async () => {
    const answer = deferred<FlowFileDiff>()
    const { session } = setup(() => answer.promise)
    session.open(intent('src/a.ts'))
    await mountView(session)
    expect(text('git-center-diff-loading')).toBe('읽는 중…')
    expect(text('git-center-diff-path')).toBe('src/a.ts')
    answer.resolve(fileDiff('src/a.ts', 'one\ntwo\nthree\n', 'one\nTWO\nthree\nfour\n'))
    await flushPromises()
    const lines = [...document.body.querySelectorAll<HTMLElement>('.gw-diff-line')].map((l) => `${l.dataset.kind}:${l.textContent}`)
    expect(lines).toEqual(['context:11 one', 'del:2-two', 'add:2+TWO', 'context:33 three', 'add:4+four'])
    expect(text('git-center-diff-compare')).toBe('작업 기준(v0.2) ↔ 작업 파일')
    expect(text('git-center-diff-letter')).toBe('M')
    session.open(intent('src/a.ts', { compareBase: 'release' }))
    await flushPromises()
    expect(text('git-center-diff-compare')).toBe('release 기준 ↔ 작업 파일')
  })

  it('no difference, binary, too large, and a cap on drawn lines', async () => {
    const diffs: Record<string, FlowFileDiff> = {
      same: fileDiff('same', 'x\n', 'x\n'),
      bin: fileDiff('bin', null, null, { old: side('x', { binary: true }), new: side('y') }),
      big: fileDiff('big', null, null, { old: side('x'), new: side(null, { truncated: true }) }),
      long: fileDiff('long', '', Array.from({ length: 2500 }, (_, i) => `l${i}`).join('\n')),
    }
    const { session } = setup(async (path) => diffs[path])
    await mountView(session)
    session.open(intent('same'))
    await flushPromises()
    expect(text('git-center-diff-same')).toBe('차이 없음')
    session.open(intent('bin'))
    await flushPromises()
    expect(text('git-center-diff-unavailable')).toBe('바이너리 파일 — 내용 비교를 표시하지 않습니다.')
    session.open(intent('big'))
    await flushPromises()
    expect(text('git-center-diff-unavailable')).toBe('파일이 커서 표시할 수 없습니다.')
    session.open(intent('long'))
    await flushPromises()
    expect(document.body.querySelectorAll('.gw-diff-line')).toHaveLength(2000)
    expect(text('git-center-diff-more')).toBe('500줄 더 있음')
  })

  it('floor 409 → "작업 기준 확인 필요" without a body; 404 / 403 / compare / network / other; no server message', async () => {
    const errors: Record<string, unknown> = {
      floor: loaderError(gitError(409, 'group_work_base_unverified')),
      diverged: loaderError(gitError(409, 'group_work_base_diverged')),
      gone: loaderError(gitError(404, 'not_found')),
      denied: loaderError(gitError(403, 'forbidden')),
      compare: loaderError(gitError(422, 'invalid_base')),
      net: loaderError(networkError()),
      other: loaderError(gitError(500, 'git_error')),
    }
    let fail = true
    const { session, loadFileDiff } = setup(async (path) => {
      if (fail) throw errors[path]
      return fileDiff(path, 'a', 'b')
    })
    await mountView(session)
    const show = async (path: string) => {
      session.open(intent(path))
      await flushPromises()
      return q('git-center-diff-error')
    }
    let el = await show('floor')
    expect(el?.dataset.kind).toBe('floor')
    expect(el?.getAttribute('title')).toBe('group_work_base_unverified')
    expect(text('git-center-diff-error')).toContain('작업 기준 확인 필요 — 이 파일의 작업 기준 Diff를 표시할 수 없습니다.')
    expect(text('git-center-diff-error')).toContain('Stage·커밋은 계속할 수 있습니다.')
    expect(q('git-center-diff-lines')).toBeNull()
    el = await show('diverged')
    expect(el?.dataset.kind).toBe('floor')
    expect(text('git-center-diff-error')).toContain('작업 기준 확인 필요')
    await show('gone')
    expect(text('git-center-diff-error')).toBe('파일을 찾을 수 없습니다(이미 정리되었거나 표시되지 않는 파일).')
    await show('denied')
    expect(text('git-center-diff-error')).toBe('권한 없음')
    await show('compare')
    expect(text('git-center-diff-error')).toBe('이 대상과 비교할 수 없습니다. (invalid_base)')
    await show('other')
    expect(text('git-center-diff-error')).toBe('Diff를 읽지 못했습니다. (git_error)')
    await show('net')
    expect(text('git-center-diff-error')).toContain('읽지 못함')
    expect(document.body.textContent).not.toContain('server text never shown')
    fail = false
    q('git-center-diff-retry')!.click()
    await flushPromises()
    expect(loadFileDiff).toHaveBeenLastCalledWith(P, G, 'net', null)
    expect(q('git-center-diff-lines')).not.toBeNull()
  })

  it('0067 T#4: ‹ 목록으로 is raised as `back`; no ✕ / ⟳ of its own; closing leaves nothing drawn', async () => {
    const { session, loadFileDiff } = setup(async (path) => fileDiff(path, 'a', 'b'))
    session.open(intent('a'))
    await mountView(session)
    expect(text('git-center-diff-back')).toBe('‹ 목록으로')
    expect(q('git-center-diff-reload')).toBeNull()
    expect(q('git-center-diff-close')).toBeNull()
    q('git-center-diff-back')!.click()
    await flushPromises()
    expect(wrapper!.emitted('back')).toEqual([[]])
    // The view decides nothing: the window closes the session.
    expect(session.state.open?.path).toBe('a')
    session.close()
    await flushPromises()
    expect(q('git-center-diff')).toBeNull()
    expect(loadFileDiff).toHaveBeenCalledTimes(1)
  })

  it('0067 T#4: `파일 경로` shows the full path once, `비교 기준` its own label; the code keeps its columns', async () => {
    const path = 'client_gui/src/features/git/deep/nested/file-with-a-long-name.ts'
    const long = `const x = '${'y'.repeat(400)}'`
    const { session } = setup(async (p) => fileDiff(p, 'a\n', `${long}\n`))
    session.open(intent(path, { status: 'M' }))
    await mountView(session)
    const rows = [...document.body.querySelectorAll<HTMLElement>('.gw-diff-meta-row')]
    expect(rows.map((r) => r.querySelector('dt')?.textContent)).toEqual(['파일 경로', '비교 기준'])
    expect(text('git-center-diff-path')).toBe(path)
    expect(q('git-center-diff-path')?.getAttribute('title')).toBe(path)
    // The path once in the whole detail: no separate file name line or title.
    const all = q('git-center-diff')!.textContent ?? ''
    expect(all.split(path).length - 1).toBe(1)
    expect(all.split('file-with-a-long-name.ts').length - 1).toBe(1)
    expect(text('git-center-diff-letter')).toBe('M')
    expect(text('git-center-diff-compare')).toBe('작업 기준(v0.2) ↔ 작업 파일')
    // Each line: two number cells, the sign and the text, one row of the scrolling code area.
    const line = document.body.querySelector<HTMLElement>('.gw-diff-line.is-add')!
    expect([...line.children].map((c) => c.className)).toEqual(['gw-diff-no', 'gw-diff-no', 'gw-diff-sign', 'gw-diff-text'])
    expect(line.parentElement?.dataset.test).toBe('git-center-diff-lines')
    expect(line.querySelector('.gw-diff-text')?.textContent).toBe(long)
  })

  it('0067 T#4: no difference and a failed read are different lines', async () => {
    let fail = false
    const { session } = setup(async (path) => {
      if (fail) throw loaderError(gitError(500, 'git_error'))
      return fileDiff(path, 'x\n', 'x\n')
    })
    session.open(intent('same'))
    await mountView(session)
    expect(q('git-center-diff-same')?.className).toBe('gw-diff-note')
    expect(text('git-center-diff-same')).toBe('차이 없음')
    expect(q('git-center-diff-error')).toBeNull()
    fail = true
    session.reload()
    await flushPromises()
    expect(q('git-center-diff-same')).toBeNull()
    expect(q('git-center-diff-error')?.className).toContain('is-error')
    expect(text('git-center-diff-error')).toBe('Diff를 읽지 못했습니다. (git_error)')
  })
})
