import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DesignerGitPanel } from '@/features/git'
import { G, P, createFakeGitSource, gitError, networkError, row, status } from './fakeGitSource'

// flowgate.v02.0065 — the Git tab with Git off (미연동), with a failed status read and with Git
// on: exactly one of them shows; `Git 연결` opens the connect modeless next to the 0033 parts.
registerAppMessages(i18n)

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

const FLOW = { groupId: G, name: '인증 오류 조사' }

async function mountPanel(fake: ReturnType<typeof createFakeGitSource>, props: Record<string, unknown> = {}) {
  wrapper = mount(DesignerGitPanel, {
    props: { projectId: P, flow: FLOW, source: fake.source, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const q = (sel: string) => document.body.querySelector<HTMLElement>(`[data-test="${sel}"]`)
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

async function click(sel: string) {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
  await flushPromises()
}

async function confirmDialog() {
  const button = [...document.body.querySelectorAll<HTMLElement>('.ui-confirm-actions button')].at(-1)
  if (!button) throw new Error('no confirm dialog')
  button.click()
  await flushPromises()
}

const off = () =>
  status({
    enabled: false,
    baseBranch: null,
    pending: [row()],
    baseDirty: { files: ['src/a.ts'], mergeInProgress: false, aiRun: false },
  })

describe('DesignerGitPanel — 미연동 / 조회 실패 / 연결됨 are exclusive', () => {
  it('Git off: the 미연동 card only, no exception card', async () => {
    await mountPanel(createFakeGitSource({ status: off() }))
    expect(q('git-unlinked')).not.toBeNull()
    expect(text('git-unlinked-heading')).toBe('Git 미연동')
    expect(text('git-unlinked-connect')).toBe('Git 연결')
    expect(q('git-unlinked')?.querySelector('svg')).not.toBeNull()
    expect(q('git-exceptions')).toBeNull()
    expect(q('git-status-error')).toBeNull()
  })

  it('a failed read is its own card, never 미연동', async () => {
    const fake = createFakeGitSource()
    fake.source.loadStatus.mockRejectedValue(gitError(500, 'internal'))
    await mountPanel(fake)
    expect(q('git-status-error')).not.toBeNull()
    expect(text('git-status-error-reason')).toBe('Git 상태를 읽을 수 없습니다.')
    expect(q('git-unlinked')).toBeNull()
    expect(q('git-exceptions')).toBeNull()
  })

  it('a network failure keeps its own words', async () => {
    const fake = createFakeGitSource()
    fake.source.loadStatus.mockRejectedValue(networkError())
    await mountPanel(fake)
    expect(text('git-status-error-reason')).toBe('서버와 통신하지 못했습니다.')
    expect(q('git-unlinked')).toBeNull()
  })

  it('Git on: neither card, the 0033 view as it was', async () => {
    await mountPanel(createFakeGitSource({ status: status({ pending: [row()] }) }))
    expect(q('git-unlinked')).toBeNull()
    expect(q('git-status-error')).toBeNull()
    expect(q('git-exceptions')).not.toBeNull()
  })

  it('retry reads again and shows what the server now says', async () => {
    const fake = createFakeGitSource({ status: off() })
    fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'internal'))
    await mountPanel(fake)
    expect(q('git-status-error')).not.toBeNull()
    await click('git-status-retry')
    expect(q('git-status-error')).toBeNull()
    expect(q('git-unlinked')).not.toBeNull()
  })

  it('switches when a refresh finds Git on', async () => {
    const fake = createFakeGitSource({ status: off() })
    await mountPanel(fake)
    expect(q('git-unlinked')).not.toBeNull()
    fake.db.status = status()
    await click('git-refresh')
    expect(q('git-unlinked')).toBeNull()
  })

  it('a failed re-read after a remote apply failure shows the read-failure card only; Git on again brings the failure card and 다시 반영 back', async () => {
    const fake = createFakeGitSource({
      status: status({ pending: [row({ status: 'waiting', mergeId: null, plan: { action: 'merge', targetBranch: 'main' } })] }),
    })
    fake.source.finalize.mockRejectedValueOnce(gitError(500, 'push_rejected'))
    await mountPanel(fake)
    await click('git-hold-apply')
    await confirmDialog()
    expect(q('git-push-card')).not.toBeNull()

    fake.source.loadStatus.mockRejectedValueOnce(gitError(500, 'internal'))
    await click('git-refresh')
    expect(q('git-status-error')).not.toBeNull()
    expect(q('git-exceptions')).toBeNull()
    expect(q('git-push-card')).toBeNull()
    expect(q('git-push-retry')).toBeNull()
    expect(q('git-unlinked')).toBeNull()

    await click('git-status-retry')
    expect(q('git-status-error')).toBeNull()
    expect(q('git-push-card')).not.toBeNull()
    await click('git-push-retry')
    expect(fake.source.finalize).toHaveBeenCalledTimes(2)
    expect(fake.source.finalize).toHaveBeenLastCalledWith(G, 'merge', 'main')
  })

  it('Git off hides the header (still in the page); Git on shows it again', async () => {
    const fake = createFakeGitSource({ status: off() })
    await mountPanel(fake)
    expect(q('git-head')?.style.display).toBe('none')
    expect(text('git-base-branch')).toBe('Base branch · 미설정')
    fake.db.status = status()
    await (wrapper?.vm as unknown as { refresh: () => Promise<void> }).refresh()
    await flushPromises()
    expect(q('git-unlinked')).toBeNull()
    expect(q('git-head')?.style.display).toBe('')
  })

  it('a failed read keeps the header and its refresh on screen', async () => {
    const fake = createFakeGitSource()
    fake.source.loadStatus.mockRejectedValue(gitError(500, 'internal'))
    await mountPanel(fake)
    expect(q('git-head')?.style.display).toBe('')
    expect(q('git-refresh')).not.toBeNull()
  })
})

describe('DesignerGitPanel — Git 연결 modeless', () => {
  it('Git 연결 opens the window; × closes it', async () => {
    const w = await mountPanel(createFakeGitSource({ status: off() }))
    expect(q('git-connect-window')).toBeNull()
    await click('git-unlinked-connect')
    expect(q('git-connect-window')).not.toBeNull()
    expect(text('git-connect-title')).toBe('⑂ Git 연결')
    document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[aria-label="Close"]')?.click()
    await flushPromises()
    expect(q('git-connect-window')).toBeNull()
    // The window is the panel's own: the 0033 window list never carries it.
    const reported = (w.emitted('windows-change') ?? []) as unknown[][][]
    expect(reported.every(([list]) => list.length === 0)).toBe(true)
  })

  it('minimize / restore keep the window open', async () => {
    await mountPanel(createFakeGitSource({ status: off() }))
    await click('git-unlinked-connect')
    document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[aria-label="Minimize"]')?.click()
    await flushPromises()
    expect(text('git-connect-restore')).toBe('⑂ Git 연결 창 복원')
    await click('git-connect-restore')
    expect(q('git-connect-restore')).toBeNull()
    expect(q('git-connect-window')).not.toBeNull()
  })

  it('취소 closes the window', async () => {
    await mountPanel(createFakeGitSource({ status: off() }))
    await click('git-unlinked-connect')
    await click('git-connect-cancel')
    expect(q('git-connect-window')).toBeNull()
  })

  it('another project closes the window', async () => {
    const w = await mountPanel(createFakeGitSource({ status: off() }))
    await click('git-unlinked-connect')
    await w.setProps({ projectId: 'p2' })
    await flushPromises()
    expect(q('git-connect-window')).toBeNull()
  })

  // T#2 wired the requests (GitConnectFlow.0065.spec.ts covers them): test / connect go to the
  // Git settings contracts only and show nothing before the server answers.
  it('test / connect use the Git settings contracts only, never a 0033 action', async () => {
    const fake = createFakeGitSource({ status: off() })
    fake.source.testGitConnection.mockImplementation(() => new Promise(() => undefined))
    await mountPanel(fake)
    await click('git-unlinked-connect')
    const remote = q('git-connect-remote') as HTMLInputElement
    remote.value = 'https://github.com/org/repo.git'
    remote.dispatchEvent(new Event('input'))
    await flushPromises()
    await click('git-connect-test')
    expect(text('git-connect-test-status')).toBe('')
    expect(text('git-connect-message')).toBe('')
    expect(q('git-connect-window')).not.toBeNull()
    expect(q('git-unlinked')).not.toBeNull()
    expect(fake.source.testGitConnection).toHaveBeenCalledTimes(1)
    expect(fake.source.saveGitConfig).not.toHaveBeenCalled()
    for (const call of ['finalize', 'push', 'baseCommit', 'baseRevert', 'baseRemove'] as const) {
      expect(fake.source[call], call).not.toHaveBeenCalled()
    }
  })

  it('readOnly: Git 연결 is off', async () => {
    await mountPanel(createFakeGitSource({ status: off() }), { readOnly: true })
    const button = q('git-unlinked-connect') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    button.click()
    await flushPromises()
    expect(q('git-connect-window')).toBeNull()
  })
})
