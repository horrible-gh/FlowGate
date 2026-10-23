import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import GitConnectWindow from '@/features/git/GitConnectWindow.vue'
import type { GitConfigSave, GitConnectionTest, GitProvisionResult } from '@/features/git/types'
import { G, P, createFakeGitSource, gitError, networkError, row, status } from './fakeGitSource'

// flowgate.v02.0065 T#2 — `Git 연결` on the existing v0.1 Git settings contracts: the status
// decision, test-connection → settings save with Git on → provision → refresh, every way it can
// stop, the saved-but-not-prepared state, the project switch and the token staying out of sight.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
  vi.restoreAllMocks()
})

const FLOW = { groupId: G, name: '인증 오류 조사' }
// A made-up value used only to check where a typed token can end up.
const TOKEN = 'tok-0065-never-shown'
const URL = 'https://github.com/org/repo.git'

const off = () => status({ enabled: false, baseBranch: null, pending: [] })

type Fake = ReturnType<typeof createFakeGitSource>

/** Git off; a save turns it on in the fake server, as the real one does. */
function offline(): Fake {
  const fake = createFakeGitSource({ status: off() })
  fake.source.saveGitConfig.mockImplementation(async (_pid: string, input: GitConfigSave) => {
    fake.db.status = status({ pending: [] })
    return {
      configured: true,
      repoUrl: input.repoUrl,
      provider: input.provider,
      username: input.username,
      baseBranch: input.baseBranch,
      enabled: true,
      hasSecret: true,
    }
  })
  return fake
}

async function mountPanel(fake: Fake, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const q = <T extends HTMLElement = HTMLElement>(sel: string) => document.body.querySelector<T>(`[data-test="${sel}"]`)
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

async function click(sel: string) {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
  await flushPromises()
}

async function type(sel: string, value: string) {
  const el = q<HTMLInputElement>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function openAndFill(token = TOKEN) {
  await click('git-unlinked-connect')
  await type('git-connect-remote', URL)
  const provider = q<HTMLSelectElement>('git-connect-provider')!
  provider.value = 'github'
  provider.dispatchEvent(new Event('change'))
  await type('git-connect-username', 'dev')
  if (token) await type('git-connect-secret', token)
  await type('git-connect-branch', 'v0.2')
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

const ok: GitConnectionTest = { reachable: true, authenticated: true, failureCode: null, remoteDefaultBranch: 'main' }
const failedProvision: GitProvisionResult = { status: 'failed', mode: 'clone', busy: false, provision: null }

// The 0033 view (Git on, nothing to do is quiet): no 미연동 / read-failure card, the header shown.
const connected = () =>
  q('git-unlinked') === null && q('git-status-error') === null && q<HTMLElement>('git-head')?.style.display === ''

const order = (...fns: Array<{ mock: { invocationCallOrder: number[] } }>) => fns.map((fn) => fn.mock.invocationCallOrder[0])

describe('status decision (0065-R §3.1)', () => {
  it.each([
    ['a failed read (500)', () => Promise.reject(gitError(500, 'internal')), 'git-status-error'],
    ['a refused read (403)', () => Promise.reject(gitError(403, 'forbidden')), 'git-status-error'],
    ['a missing project (404)', () => Promise.reject(gitError(404, 'not_found')), 'git-status-error'],
    ['no answer (network)', () => Promise.reject(networkError()), 'git-status-error'],
    ['enabled: false', () => Promise.resolve(off()), 'git-unlinked'],
    ['enabled: true (a Flow with work pending)', () => Promise.resolve(status({ pending: [row()] })), 'git-exceptions'],
  ])('%s → %s only', async (_label, answer, shown) => {
    const fake = createFakeGitSource()
    fake.source.loadStatus.mockImplementation(answer)
    await mountPanel(fake)
    for (const sel of ['git-status-error', 'git-unlinked', 'git-exceptions']) {
      expect(q(sel) !== null, sel).toBe(sel === shown)
    }
  })
})

describe('a re-read that fails over 미연동 (0065-R §3.1)', () => {
  const failedRead = () =>
    q('git-status-error') !== null &&
    text('git-status-error-reason') === '서버와 통신하지 못했습니다.' &&
    q('git-unlinked') === null &&
    q<HTMLElement>('git-head')?.style.display === ''

  it('enabled: false, then no answer on coming back to the tab → the read-failure card, retry reads again', async () => {
    const fake = createFakeGitSource({ status: off() })
    const w = await mountPanel(fake, { active: true })
    expect(q('git-unlinked')).not.toBeNull()

    fake.source.loadStatus.mockRejectedValueOnce(networkError())
    await w.setProps({ active: false })
    await w.setProps({ active: true })
    await flushPromises()
    expect(failedRead()).toBe(true)

    // Retry that fails again stays on the card; one that answers shows the answer.
    fake.source.loadStatus.mockRejectedValueOnce(networkError())
    await click('git-status-retry')
    expect(failedRead()).toBe(true)
    await click('git-status-retry')
    expect(q('git-status-error')).toBeNull()
    expect(q('git-unlinked')).not.toBeNull()
  })

  it('a connect that saved and prepared, then no answer to the status read → not 미연동; retry shows the 0033 view', async () => {
    const fake = offline()
    await mountPanel(fake)
    await openAndFill()
    fake.source.loadStatus.mockRejectedValueOnce(networkError())
    await click('git-connect-submit')
    expect(fake.source.saveGitConfig).toHaveBeenCalledTimes(1)
    expect(fake.source.provisionGit).toHaveBeenCalledTimes(1)
    expect(q('git-connect-window')).toBeNull()
    expect(failedRead()).toBe(true)

    await click('git-status-retry')
    expect(connected()).toBe(true)
  })

  it('a project already connected keeps its last answer on a network failure (0033 §4.5)', async () => {
    const fake = createFakeGitSource({ status: status({ pending: [row()] }) })
    const w = await mountPanel(fake, { active: true })
    expect(q('git-exceptions')).not.toBeNull()
    fake.source.loadStatus.mockRejectedValueOnce(networkError())
    await w.setProps({ active: false })
    await w.setProps({ active: true })
    await flushPromises()
    expect(q('git-status-error')).toBeNull()
    expect(q('git-unlinked')).toBeNull()
    expect(q('git-exceptions')).not.toBeNull()
  })
})

describe('연결 — the whole order', () => {
  it('test → save with Git on → provision ok → refresh: the window closes and the 0033 view is back', async () => {
    const fake = offline()
    const w = await mountPanel(fake)
    expect(fake.source.fetchGitConfig).not.toHaveBeenCalled()
    await openAndFill()
    expect(fake.source.fetchGitConfig).toHaveBeenCalledWith(P)
    const reads = fake.source.loadStatus.mock.calls.length
    await click('git-connect-submit')

    const sent: GitConfigSave = { repoUrl: URL, provider: 'github', username: 'dev', secret: TOKEN, baseBranch: 'v0.2' }
    expect(fake.source.testGitConnection).toHaveBeenCalledWith(P, sent)
    expect(fake.source.saveGitConfig).toHaveBeenCalledWith(P, sent)
    expect(fake.source.provisionGit).toHaveBeenCalledWith(P)
    const [tested, saved, provisioned] = order(fake.source.testGitConnection, fake.source.saveGitConfig, fake.source.provisionGit)
    expect(tested).toBeLessThan(saved)
    expect(saved).toBeLessThan(provisioned)
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(fake.source.loadStatus.mock.invocationCallOrder.at(-1)).toBeGreaterThan(provisioned)

    expect(q('git-connect-window')).toBeNull()
    expect(connected()).toBe(true)
    expect(fake.source.fetchGitProvision).not.toHaveBeenCalled()
    // The window is the panel's own: the 0033 window list never carried it.
    expect(((w.emitted('windows-change') ?? []) as unknown[][][]).every(([list]) => list.length === 0)).toBe(true)
  })

  it('mode none (the Base is already there) is done as well', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValue({ status: 'ok', mode: 'none', busy: false, provision: null })
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(q('git-connect-window')).toBeNull()
    expect(connected()).toBe(true)
  })

  it('runs one request at a time', async () => {
    const fake = offline()
    const pending = deferred<GitConnectionTest>()
    fake.source.testGitConnection.mockReturnValue(pending.promise)
    const w = await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(text('git-connect-submit')).toBe('연결 중…')
    expect(q<HTMLButtonElement>('git-connect-submit')?.disabled).toBe(true)
    expect(q<HTMLButtonElement>('git-connect-test')?.disabled).toBe(true)
    const win = w.findComponent(GitConnectWindow)
    const form = { repoUrl: URL, provider: 'generic', username: '', secret: '', baseBranch: 'main' }
    win.vm.$emit('connect', form)
    win.vm.$emit('test', form)
    await flushPromises()
    expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)
    pending.resolve(ok)
    await flushPromises()
    expect(fake.source.saveGitConfig).toHaveBeenCalledTimes(1)
  })
})

describe('연결 테스트 / a failed test stops before anything is saved', () => {
  it('연결 테스트 runs the test only and shows the outcome beside the button; typing clears it', async () => {
    const fake = offline()
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-test')
    expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)
    expect(text('git-connect-test-status')).toBe('연결 가능')
    expect(fake.source.saveGitConfig).not.toHaveBeenCalled()
    expect(fake.source.provisionGit).not.toHaveBeenCalled()
    await type('git-connect-branch', 'main')
    expect(text('git-connect-test-status')).toBe('')
  })

  it.each([
    ['unreachable', { reachable: false, authenticated: null, failureCode: 'unreachable' }, '원격 저장소에 접근하지 못했습니다. 저장소 URL을 확인하세요.'],
    ['auth failed', { reachable: true, authenticated: false, failureCode: 'auth_failed' }, '인증에 실패했습니다. 사용자명과 인증 토큰을 확인하세요.'],
    ['other git failure', { reachable: true, authenticated: null, failureCode: 'git_error' }, '연결 테스트에 실패했습니다.'],
  ])('%s: no save, the cause in the window', async (_label, answer, words) => {
    const fake = offline()
    fake.source.testGitConnection.mockResolvedValue({ remoteDefaultBranch: null, ...answer } as GitConnectionTest)
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-test')
    expect(text('git-connect-test-status')).toBe(words)
    await click('git-connect-submit')
    expect(fake.source.saveGitConfig).not.toHaveBeenCalled()
    expect(fake.source.provisionGit).not.toHaveBeenCalled()
    expect(text('git-connect-message')).toBe(words)
    expect(q('git-connect-window')).not.toBeNull()
    expect(q('git-unlinked')).not.toBeNull()
    expect(q<HTMLButtonElement>('git-connect-submit')?.disabled).toBe(false)
  })

  it.each([
    ['403', () => gitError(403, 'forbidden'), '이 Project의 Git 설정을 바꿀 권한이 없습니다.'],
    ['422', () => gitError(422, 'invalid_request'), '입력값을 서버가 받아들이지 않았습니다. 입력값을 확인하세요.'],
    ['network', () => networkError(), '서버와 통신하지 못했습니다.'],
    ['500', () => gitError(500, 'git_unavailable'), '연결 테스트에 실패했습니다.'],
  ])('a refused test request (%s) has its own words and saves nothing', async (_label, error, words) => {
    const fake = offline()
    fake.source.testGitConnection.mockRejectedValue(error())
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(text('git-connect-message')).toBe(words)
    expect(fake.source.saveGitConfig).not.toHaveBeenCalled()
  })
})

describe('a failed save', () => {
  it.each([
    ['500', () => gitError(500, 'internal'), 'Git 설정을 저장하지 못했습니다.'],
    ['403', () => gitError(403, 'forbidden'), '이 Project의 Git 설정을 바꿀 권한이 없습니다.'],
    ['422', () => gitError(422, 'invalid_request'), '입력값을 서버가 받아들이지 않았습니다. 입력값을 확인하세요.'],
  ])('%s: no provision, the window stays with the save failure', async (_label, error, words) => {
    const fake = offline()
    fake.source.saveGitConfig.mockRejectedValue(error())
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(fake.source.provisionGit).not.toHaveBeenCalled()
    expect(text('git-connect-message')).toBe(words)
    expect(q('git-connect-window')).not.toBeNull()
    expect(q('git-connect-retry-provision')).toBeNull()
    expect(q('git-unlinked')).not.toBeNull()
  })
})

describe('saved but the Base not prepared (0065-R §3.3)', () => {
  it('result.status failed (HTTP 200): not done — state read again, 저장소 준비 다시 시도 runs provision again', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValueOnce(failedProvision)
    await mountPanel(fake)
    await openAndFill()
    const reads = fake.source.loadStatus.mock.calls.length
    await click('git-connect-submit')

    expect(q('git-connect-window')).not.toBeNull()
    expect(text('git-connect-message')).toBe('설정은 저장됐지만 저장소 준비에 실패했습니다. 저장소 준비를 다시 시도하세요.')
    expect(fake.source.fetchGitProvision).toHaveBeenCalledWith(P)
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
    // The tab follows the server: Git is on now, so the 0033 view, not 미연동.
    expect(connected()).toBe(true)
    expect(text('git-connect-retry-provision')).toBe('저장소 준비 다시 시도')

    await click('git-connect-retry-provision')
    expect(fake.source.provisionGit).toHaveBeenCalledTimes(2)
    // Preparing again does not test or save again.
    expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)
    expect(fake.source.saveGitConfig).toHaveBeenCalledTimes(1)
    expect(q('git-connect-window')).toBeNull()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 2)
  })

  it('git_busy has its own words and is not done either', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValue({ status: 'failed', mode: 'none', busy: true, provision: null })
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(q('git-connect-window')).not.toBeNull()
    expect(text('git-connect-message')).toBe(
      '설정은 저장됐지만 다른 Git 작업이 진행 중이라 저장소를 준비하지 못했습니다. 잠시 후 다시 시도하세요.',
    )
    expect(fake.source.fetchGitProvision).toHaveBeenCalled()
    await click('git-connect-retry-provision')
    expect(fake.source.provisionGit).toHaveBeenCalledTimes(2)
    expect(q('git-connect-window')).not.toBeNull()
  })

  it('a failed provision request (500 / 409 git_busy / 403) is not done', async () => {
    for (const [error, words] of [
      [gitError(500, 'git_error'), '설정은 저장됐지만 저장소 준비에 실패했습니다. 저장소 준비를 다시 시도하세요.'],
      [gitError(409, 'git_busy'), '설정은 저장됐지만 다른 Git 작업이 진행 중이라 저장소를 준비하지 못했습니다. 잠시 후 다시 시도하세요.'],
      [gitError(403, 'forbidden'), '설정은 저장됐지만 저장소를 준비할 권한이 없습니다.'],
    ] as const) {
      const fake = offline()
      fake.source.provisionGit.mockRejectedValue(error)
      await mountPanel(fake)
      await openAndFill()
      await click('git-connect-submit')
      expect(q('git-connect-window')).not.toBeNull()
      expect(text('git-connect-message')).toBe(words)
      wrapper?.unmount()
      wrapper = null
      document.body.innerHTML = ''
    }
  })

  it('a provision state that says Git is off offers no preparation (it would be refused)', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValue(failedProvision)
    fake.source.fetchGitProvision.mockResolvedValue({ configured: false, enabled: false, baseCheckoutExists: false, lastAttemptResult: null })
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(q('git-connect-window')).not.toBeNull()
    expect(q('git-connect-retry-provision')).toBeNull()
  })

  it('an unreadable provision state still offers preparing again', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValue(failedProvision)
    fake.source.fetchGitProvision.mockRejectedValue(gitError(500, 'internal'))
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(q('git-connect-retry-provision')).not.toBeNull()
  })
})

describe('existing settings with Git off', () => {
  it('fill the window (never the token); no token typed sends null so the stored one is used', async () => {
    const fake = offline()
    fake.source.fetchGitConfig.mockResolvedValue({
      configured: true,
      repoUrl: 'https://gitlab.example.com/team/app.git',
      provider: 'gitlab',
      username: 'ci',
      baseBranch: 'develop',
      enabled: false,
      hasSecret: true,
    })
    await mountPanel(fake)
    await click('git-unlinked-connect')
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('https://gitlab.example.com/team/app.git')
    expect(q<HTMLSelectElement>('git-connect-provider')?.value).toBe('gitlab')
    expect(q<HTMLInputElement>('git-connect-username')?.value).toBe('ci')
    expect(q<HTMLInputElement>('git-connect-branch')?.value).toBe('develop')
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-secret')?.placeholder).toBe('저장된 토큰 사용 · 바꿀 때만 입력')

    await click('git-connect-submit')
    const sent: GitConfigSave = {
      repoUrl: 'https://gitlab.example.com/team/app.git',
      provider: 'gitlab',
      username: 'ci',
      secret: null,
      baseBranch: 'develop',
    }
    expect(fake.source.testGitConnection).toHaveBeenCalledWith(P, sent)
    expect(fake.source.saveGitConfig).toHaveBeenCalledWith(P, sent)
    expect(q('git-connect-window')).toBeNull()
  })

  it('a provider the window does not list opens as Generic Git', async () => {
    const fake = offline()
    fake.source.fetchGitConfig.mockResolvedValue({
      configured: true,
      repoUrl: URL,
      provider: 'bitbucket',
      username: null,
      baseBranch: 'main',
      enabled: false,
      hasSecret: false,
    })
    await mountPanel(fake)
    await click('git-unlinked-connect')
    expect(q<HTMLSelectElement>('git-connect-provider')?.value).toBe('generic')
    expect(q<HTMLInputElement>('git-connect-username')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-secret')?.placeholder).toBe('')
  })

  it('an unreadable config leaves an empty form with a note; 미연동 stays', async () => {
    const fake = offline()
    fake.source.fetchGitConfig.mockRejectedValue(gitError(500, 'internal'))
    await mountPanel(fake)
    await click('git-unlinked-connect')
    expect(q('git-connect-window')).not.toBeNull()
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-branch')?.value).toBe('main')
    expect(text('git-connect-message')).toBe('기존 Git 설정을 불러오지 못했습니다. 값을 직접 입력하세요.')
    expect(q('git-unlinked')).not.toBeNull()
    expect(q('git-status-error')).toBeNull()
  })
})

describe('project switch / closing while a request runs', () => {
  it('another project closes the window, drops the late answer and reads the new project', async () => {
    const fake = offline()
    const pending = deferred<GitConnectionTest>()
    fake.source.testGitConnection.mockReturnValue(pending.promise)
    const w = await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)

    await w.setProps({ projectId: 'p2' })
    await flushPromises()
    expect(q('git-connect-window')).toBeNull()
    expect(fake.source.loadStatus).toHaveBeenLastCalledWith('p2')
    expect(q('git-unlinked')).not.toBeNull()

    pending.resolve(ok)
    await flushPromises()
    expect(fake.source.saveGitConfig).not.toHaveBeenCalled()
    expect(fake.source.provisionGit).not.toHaveBeenCalled()
    expect(q('git-connect-window')).toBeNull()

    // Opening again starts clean for the new project.
    await click('git-unlinked-connect')
    expect(fake.source.fetchGitConfig).toHaveBeenLastCalledWith('p2')
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
    expect(text('git-connect-message')).toBe('')
  })

  it('a late config answer of the old project does not fill the new window', async () => {
    const fake = offline()
    const pending = deferred<Awaited<ReturnType<typeof fake.source.fetchGitConfig>>>()
    fake.source.fetchGitConfig.mockReturnValueOnce(pending.promise)
    const w = await mountPanel(fake)
    await click('git-unlinked-connect')
    await w.setProps({ projectId: 'p2' })
    await flushPromises()
    await click('git-unlinked-connect')
    pending.resolve({
      configured: true,
      repoUrl: 'https://old.example.com/a.git',
      provider: 'gitea',
      username: 'old',
      baseBranch: 'old',
      enabled: false,
      hasSecret: true,
    })
    await flushPromises()
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-secret')?.placeholder).toBe('')
  })

  it('× while saving: the window is gone, the late answer changes nothing in it, the tab reads the server again', async () => {
    const fake = offline()
    const saving = deferred<void>()
    const original = fake.source.saveGitConfig.getMockImplementation()!
    fake.source.saveGitConfig.mockImplementation(async (pid: string, input: GitConfigSave) => {
      await saving.promise
      return original(pid, input)
    })
    await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[data-modeless-action="close"]')?.click()
    await flushPromises()
    expect(q('git-connect-window')).toBeNull()
    const reads = fake.source.loadStatus.mock.calls.length

    saving.resolve()
    await flushPromises()
    expect(fake.source.provisionGit).not.toHaveBeenCalled()
    expect(q('git-connect-window')).toBeNull()
    expect(fake.source.loadStatus.mock.calls.length).toBe(reads + 1)
    expect(q('git-unlinked')).toBeNull()
  })
})

describe('one change request per project — reopening never runs a second one beside it', () => {
  const URL_B = 'https://github.com/org/other.git'

  /** A fake server that records the saved repository, the prepared one and how many settings
   *  changes (save / provision) ran at once; `hold` keeps every request of that kind waiting. */
  function server(fake: Fake, hold: 'save' | 'provision') {
    const seen = { saved: null as string | null, prepared: null as string | null, running: 0, most: 0 }
    const gates: Array<() => void> = []
    async function change<T>(kind: 'save' | 'provision', apply: () => T): Promise<T> {
      seen.running += 1
      seen.most = Math.max(seen.most, seen.running)
      try {
        if (kind === hold) await new Promise<void>((resolve) => gates.push(resolve))
        return apply()
      } finally {
        seen.running -= 1
      }
    }
    fake.source.saveGitConfig.mockImplementation((_pid: string, input: GitConfigSave) =>
      change('save', () => {
        seen.saved = input.repoUrl
        fake.db.status = status({ pending: [] })
        return {
          configured: true,
          repoUrl: input.repoUrl,
          provider: input.provider,
          username: input.username,
          baseBranch: input.baseBranch,
          enabled: true,
          hasSecret: true,
        }
      }),
    )
    fake.source.provisionGit.mockImplementation(() =>
      change('provision', (): GitProvisionResult => {
        seen.prepared = seen.saved
        return { status: 'ok', mode: 'clone', busy: false, provision: null }
      }),
    )
    const release = async () => {
      while (gates.length) {
        gates.shift()!()
        await flushPromises()
      }
    }
    return { seen, release, held: () => gates.length }
  }

  const formB = { repoUrl: URL_B, provider: 'github', username: 'dev', secret: '', baseBranch: 'v0.2' }
  const closeButton = () =>
    document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[data-modeless-action="close"]')

  it.each(['save', 'provision'] as const)(
    'Git 연결 clicked again while the %s runs: the same window comes back as it was, nothing new is sent',
    async (hold) => {
      const fake = offline()
      const srv = server(fake, hold)
      const w = await mountPanel(fake)
      await openAndFill()
      await click('git-connect-submit')
      expect(srv.held()).toBe(1)
      const win = w.findComponent(GitConnectWindow)
      win.vm.$emit('update:minimized', true)
      await flushPromises()

      await click('git-unlinked-connect')
      expect(win.props('minimized')).toBe(false)
      expect(fake.source.fetchGitConfig).toHaveBeenCalledTimes(1)
      expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe(URL)
      expect(text('git-connect-submit')).toBe('연결 중…')
      expect(q<HTMLButtonElement>('git-connect-submit')?.disabled).toBe(true)
      win.vm.$emit('connect', formB)
      win.vm.$emit('test', formB)
      await flushPromises()
      expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)

      await srv.release()
      expect(fake.source.saveGitConfig).toHaveBeenCalledTimes(1)
      expect(fake.source.provisionGit).toHaveBeenCalledTimes(1)
      expect(srv.seen).toMatchObject({ saved: URL, prepared: URL, most: 1 })
      expect(q('git-connect-window')).toBeNull()
      expect(connected()).toBe(true)
    },
  )

  it.each(['save', 'provision'] as const)(
    '× and reopen while the %s runs: the new window waits for it, then its own connect runs alone',
    async (hold) => {
      const fake = offline()
      const srv = server(fake, hold)
      const w = await mountPanel(fake)
      await openAndFill()
      await click('git-connect-submit')
      expect(srv.held()).toBe(1)
      closeButton()?.click()
      await flushPromises()
      expect(q('git-connect-window')).toBeNull()

      // Reopened: the earlier request still runs, so the window shows it and sends nothing.
      await click('git-unlinked-connect')
      expect(q('git-connect-window')).not.toBeNull()
      expect(q<HTMLButtonElement>('git-connect-submit')?.disabled).toBe(true)
      expect(fake.source.fetchGitConfig).toHaveBeenCalledTimes(1)
      const win = w.findComponent(GitConnectWindow)
      win.vm.$emit('connect', formB)
      await flushPromises()
      expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)
      expect(fake.source.saveGitConfig.mock.calls.length + fake.source.provisionGit.mock.calls.length).toBe(
        hold === 'save' ? 1 : 2,
      )

      // The earlier one runs its course (the dropped window does not go on to provision).
      await srv.release()
      expect(fake.source.fetchGitConfig).toHaveBeenCalledTimes(2)
      expect(q('git-connect-window')).not.toBeNull()
      expect(q<HTMLButtonElement>('git-connect-submit')?.disabled).toBe(false)

      await type('git-connect-remote', URL_B)
      await type('git-connect-branch', 'v0.2')
      await click('git-connect-submit')
      await srv.release()
      expect(fake.source.saveGitConfig).toHaveBeenCalledTimes(2)
      expect(fake.source.saveGitConfig).toHaveBeenLastCalledWith(P, expect.objectContaining({ repoUrl: URL_B }))
      expect(srv.seen).toMatchObject({ saved: URL_B, prepared: URL_B, most: 1 })
      expect(fake.source.provisionGit.mock.invocationCallOrder.at(-1)).toBeGreaterThan(
        fake.source.saveGitConfig.mock.invocationCallOrder.at(-1)!,
      )
      expect(q('git-connect-window')).toBeNull()
      expect(connected()).toBe(true)
    },
  )
})

describe('the token stays out of sight (0065-R §3.3)', () => {
  function leakCheck(w: VueWrapper, logs: Array<{ mock: { calls: unknown[][] } }>) {
    expect(document.body.innerHTML).not.toContain(TOKEN)
    expect(JSON.stringify(w.emitted())).not.toContain(TOKEN)
    const win = w.findComponent(GitConnectWindow)
    if (win.exists()) expect(JSON.stringify(win.props())).not.toContain(TOKEN)
    for (const log of logs) expect(JSON.stringify(log.mock.calls)).not.toContain(TOKEN)
  }

  it('through a test, a failed connect (server text carrying the token) and a successful one', async () => {
    const logs = (['log', 'info', 'warn', 'error', 'debug'] as const).map((level) =>
      vi.spyOn(console, level).mockImplementation(() => undefined),
    )
    const fake = offline()
    // A server whose message repeats the URL with the token: it is never shown.
    const leaky = Object.assign(new Error(`fatal: https://dev:${TOKEN}@github.com/org/repo.git`), {
      isAxiosError: true,
      response: { status: 500, data: { ok: false, error: { code: 'git_error', message: `https://dev:${TOKEN}@github.com` } } },
    })
    fake.source.saveGitConfig.mockRejectedValueOnce(leaky)
    const w = await mountPanel(fake)
    await openAndFill()
    expect(q<HTMLInputElement>('git-connect-secret')?.getAttribute('value')).toBeNull()

    await click('git-connect-test')
    leakCheck(w, logs)
    await click('git-connect-submit')
    expect(text('git-connect-message')).toBe('Git 설정을 저장하지 못했습니다.')
    leakCheck(w, logs)

    await click('git-connect-submit')
    expect(q('git-connect-window')).toBeNull()
    leakCheck(w, logs)
    // The only place it went: the request bodies.
    expect(fake.source.saveGitConfig).toHaveBeenLastCalledWith(P, expect.objectContaining({ secret: TOKEN }))

    // Opening again starts without it.
    fake.db.status = off()
    await w.vm.$nextTick()
    await (w.vm as unknown as { refresh: () => Promise<void> }).refresh()
    await flushPromises()
    await click('git-unlinked-connect')
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
  })

  it('a provision failure keeps no token either', async () => {
    const fake = offline()
    fake.source.provisionGit.mockResolvedValue(failedProvision)
    const w = await mountPanel(fake)
    await openAndFill()
    await click('git-connect-submit')
    expect(q('git-connect-window')).not.toBeNull()
    leakCheck(w, [])
  })
})
