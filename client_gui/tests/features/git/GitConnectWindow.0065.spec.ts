import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import GitConnectWindow from '@/features/git/GitConnectWindow.vue'
import type { GitConnectForm } from '@/features/git/types'

// flowgate.v02.0065 — the Git 연결 modeless on its own: v12 fields / words / footer, the
// window's own input check, the test / connect payloads, minimize / restore and the token
// never outliving the window.
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

// A made-up value used only to check where a typed token can end up.
const TYPED = 'typed-value-0065'

async function mountWindow(props: Record<string, unknown> = {}) {
  wrapper = mount(GitConnectWindow, {
    props: { open: true, ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

const q = <T extends HTMLElement = HTMLElement>(sel: string) => document.body.querySelector<T>(`[data-test="${sel}"]`)
const text = (sel: string) => q(sel)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

async function type(sel: string, value: string) {
  const el = q<HTMLInputElement>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('input'))
  await flushPromises()
}

async function choose(sel: string, value: string) {
  const el = q<HTMLSelectElement>(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.value = value
  el.dispatchEvent(new Event('change'))
  await flushPromises()
}

async function click(sel: string) {
  const el = q(sel)
  if (!el) throw new Error(`missing ${sel}`)
  el.click()
  await flushPromises()
}

async function fill() {
  await type('git-connect-remote', '  https://github.com/org/repo.git ')
  await choose('git-connect-provider', 'github')
  await type('git-connect-username', 'dev')
  await type('git-connect-secret', TYPED)
  await type('git-connect-branch', ' v0.2 ')
}

describe('GitConnectWindow — v12 layout', () => {
  it('shows the five fields in order with the mockup words', async () => {
    await mountWindow()
    const labels = [...document.body.querySelectorAll('.gxc-row label')].map((el) => el.textContent?.trim())
    expect(labels).toEqual(['저장소 URL', '제공자', '사용자명', '인증 토큰', 'Base branch'])
    expect(q<HTMLInputElement>('git-connect-remote')?.placeholder).toBe('https://github.com/org/repo.git')
    const options = [...(q<HTMLSelectElement>('git-connect-provider')?.options ?? [])].map((o) => [o.value, o.textContent?.trim()])
    expect(options).toEqual([
      ['generic', 'Generic Git'],
      ['github', 'GitHub'],
      ['gitlab', 'GitLab'],
      ['gitea', 'Gitea'],
      ['gitbucket', 'GitBucket'],
    ])
    expect(q<HTMLSelectElement>('git-connect-provider')?.value).toBe('generic')
    expect(q<HTMLInputElement>('git-connect-branch')?.value).toBe('main')
    expect(text('git-connect-title')).toBe('⑂ Git 연결')
  })

  it('labels point at their inputs', async () => {
    await mountWindow()
    for (const label of document.body.querySelectorAll<HTMLLabelElement>('.gxc-row label')) {
      expect(document.getElementById(label.htmlFor), label.textContent ?? '').not.toBeNull()
    }
  })

  it('the token field is a password field the browser does not fill', async () => {
    await mountWindow()
    const secret = q<HTMLInputElement>('git-connect-secret')
    expect(secret?.type).toBe('password')
    expect(secret?.getAttribute('autocomplete')).toBe('new-password')
    expect(secret?.placeholder).toBe('')
  })

  it('a stored token is only mentioned in the placeholder', async () => {
    await mountWindow({ hasStoredSecret: true })
    const secret = q<HTMLInputElement>('git-connect-secret')
    expect(secret?.placeholder).toBe('저장된 토큰 사용 · 바꿀 때만 입력')
    expect(secret?.value).toBe('')
  })

  it('footer: 연결 테스트 on the left with its status, then 취소 / 연결', async () => {
    await mountWindow()
    const buttons = [...document.body.querySelectorAll('.gxc-footer button')].map((el) => el.textContent?.trim())
    expect(buttons).toEqual(['연결 테스트', '취소', '연결'])
    expect(q('git-connect-test')?.parentElement?.classList.contains('gxc-footer-test')).toBe(true)
    expect(q('git-connect-submit')?.classList.contains('primary')).toBe(true)
    expect(q('git-connect-message')?.getAttribute('aria-live')).toBe('polite')
    expect(text('git-connect-message')).toBe('')
    expect(q('git-connect-message')?.classList.contains('is-empty')).toBe(true)
  })

  it('opens with the values it is given, never with a token', async () => {
    await mountWindow({ initial: { repoUrl: 'git@host:org/r.git', provider: 'gitea', username: 'me', baseBranch: 'dev' } })
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('git@host:org/r.git')
    expect(q<HTMLSelectElement>('git-connect-provider')?.value).toBe('gitea')
    expect(q<HTMLInputElement>('git-connect-username')?.value).toBe('me')
    expect(q<HTMLInputElement>('git-connect-branch')?.value).toBe('dev')
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
  })

  it('renders nothing while closed', async () => {
    await mountWindow({ open: false })
    expect(q('git-connect-window')).toBeNull()
  })
})

describe('GitConnectWindow — input check', () => {
  it('empty URL: the mockup line, no test / connect', async () => {
    const w = await mountWindow()
    await click('git-connect-test')
    expect(text('git-connect-message')).toBe('저장소 URL과 Base branch를 입력하세요.')
    await click('git-connect-submit')
    expect(w.emitted('test')).toBeUndefined()
    expect(w.emitted('connect')).toBeUndefined()
  })

  it('empty Base branch: the same line', async () => {
    const w = await mountWindow()
    await type('git-connect-remote', 'https://github.com/org/repo.git')
    await type('git-connect-branch', '  ')
    await click('git-connect-submit')
    expect(text('git-connect-message')).toBe('저장소 URL과 Base branch를 입력하세요.')
    expect(w.emitted('connect')).toBeUndefined()
  })

  it('a URL that is not http(s) / ssh / user@host: reads "형식을 확인하세요"', async () => {
    const w = await mountWindow()
    await type('git-connect-remote', 'github.com/org/repo')
    await click('git-connect-test')
    expect(text('git-connect-message')).toBe('저장소 URL 형식을 확인하세요.')
    expect(w.emitted('test')).toBeUndefined()
  })

  it('accepts ssh:// and the scp-like form', async () => {
    const w = await mountWindow()
    await type('git-connect-remote', 'ssh://git@host/org/repo.git')
    await click('git-connect-test')
    await type('git-connect-remote', 'git@github.com:org/repo.git')
    await click('git-connect-test')
    expect(w.emitted('test')).toHaveLength(2)
    expect(text('git-connect-message')).toBe('')
  })

  it('typing clears the line and reports input-change', async () => {
    const w = await mountWindow()
    await click('git-connect-test')
    expect(text('git-connect-message')).not.toBe('')
    await type('git-connect-remote', 'h')
    expect(text('git-connect-message')).toBe('')
    expect(w.emitted('input-change')).toBeTruthy()
  })
})

describe('GitConnectWindow — test / connect', () => {
  it('a valid form goes up trimmed with test and with connect', async () => {
    const w = await mountWindow()
    await fill()
    await click('git-connect-test')
    await click('git-connect-submit')
    const expected: GitConnectForm = {
      repoUrl: 'https://github.com/org/repo.git',
      provider: 'github',
      username: 'dev',
      secret: TYPED,
      baseBranch: 'v0.2',
    }
    expect(w.emitted('test')).toEqual([[expected]])
    expect(w.emitted('connect')).toEqual([[expected]])
  })

  it('shows the outcome it is given beside 연결 테스트 and in the message area', async () => {
    const w = await mountWindow()
    await w.setProps({ testState: { ok: true, messageKey: 'gui.git.connect.test' } })
    expect(text('git-connect-test-status')).toBe('연결 테스트')
    expect(q('git-connect-test-status')?.classList.contains('is-success')).toBe(true)
    await w.setProps({ testState: { ok: false, messageKey: 'gui.git.connect.invalid_url' } })
    expect(q('git-connect-test-status')?.classList.contains('is-error')).toBe(true)
    await w.setProps({ message: { tone: 'info', messageKey: 'gui.git.connect.required' } })
    expect(text('git-connect-message')).toBe('저장소 URL과 Base branch를 입력하세요.')
    expect(q('git-connect-message')?.classList.contains('is-info')).toBe(true)
  })

  it('says nothing on its own after a valid test: no success without an answer', async () => {
    await mountWindow()
    await fill()
    await click('git-connect-test')
    expect(text('git-connect-test-status')).toBe('')
    expect(text('git-connect-message')).toBe('')
  })

  it('busy: fields and buttons are off, the running button says so', async () => {
    const w = await mountWindow({ busy: 'testing' })
    expect(text('git-connect-test')).toBe('테스트 중…')
    for (const id of ['git-connect-remote', 'git-connect-provider', 'git-connect-username', 'git-connect-secret', 'git-connect-branch']) {
      expect((q(id) as HTMLInputElement).disabled, id).toBe(true)
    }
    for (const id of ['git-connect-test', 'git-connect-cancel', 'git-connect-submit']) {
      expect((q(id) as HTMLButtonElement).disabled, id).toBe(true)
    }
    await w.setProps({ busy: 'connecting' })
    expect(text('git-connect-submit')).toBe('연결 중…')
    expect(text('git-connect-test')).toBe('연결 테스트')
  })

  it('readOnly: test and connect are off, cancel stays', async () => {
    await mountWindow({ readOnly: true })
    expect((q('git-connect-test') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-connect-submit') as HTMLButtonElement).disabled).toBe(true)
    expect((q('git-connect-cancel') as HTMLButtonElement).disabled).toBe(false)
  })
})

describe('GitConnectWindow — window behaviour and the token', () => {
  it('minimize hides the body and shows "Git 연결 창 복원"; restore brings it back', async () => {
    const w = await mountWindow()
    const minimize = document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[aria-label="Minimize"]')
    minimize?.click()
    await flushPromises()
    expect(w.emitted('update:minimized')).toEqual([[true]])
    expect(text('git-connect-restore')).toBe('⑂ Git 연결 창 복원')
    expect((q('git-connect-window')?.parentElement as HTMLElement).style.display).toBe('none')
    await click('git-connect-restore')
    expect(w.emitted('update:minimized')).toEqual([[true], [false]])
    expect(q('git-connect-restore')).toBeNull()
    expect((q('git-connect-window')?.parentElement as HTMLElement).style.display).toBe('')
  })

  it('follows a minimized prop from outside', async () => {
    const w = await mountWindow({ minimized: true })
    expect(q('git-connect-restore')).not.toBeNull()
    await w.setProps({ minimized: false })
    expect(q('git-connect-restore')).toBeNull()
  })

  it('취소 empties the token and everything typed', async () => {
    const w = await mountWindow()
    await fill()
    await click('git-connect-cancel')
    expect(w.emitted('cancel')).toHaveLength(1)
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('')
  })

  it('× and Esc close and empty the token', async () => {
    const w = await mountWindow()
    await type('git-connect-secret', TYPED)
    document.body.querySelector<HTMLButtonElement>('.ui-modeless-titlebar-actions button[aria-label="Close"]')?.click()
    await flushPromises()
    expect(w.emitted('close')).toHaveLength(1)
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
    await type('git-connect-secret', TYPED)
    document.body.querySelector('.ui-modeless-panel')?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(w.emitted('close')).toHaveLength(2)
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
  })

  it('closing and opening again starts without the token', async () => {
    const w = await mountWindow()
    await type('git-connect-secret', TYPED)
    await w.setProps({ open: false })
    await w.setProps({ open: true })
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe('')
  })

  it('the typed token is not written into the page markup', async () => {
    await mountWindow()
    await fill()
    await click('git-connect-test')
    expect(document.body.innerHTML).not.toContain(TYPED)
  })
})

describe('GitConnectWindow — fits the viewport', () => {
  const VIEWPORT = { width: window.innerWidth, height: window.innerHeight }

  function setViewport(width: number, height: number) {
    Object.defineProperty(window, 'innerWidth', { configurable: true, writable: true, value: width })
    Object.defineProperty(window, 'innerHeight', { configurable: true, writable: true, value: height })
  }

  afterEach(() => setViewport(VIEWPORT.width, VIEWPORT.height))

  const panel = () => document.body.querySelector<HTMLElement>('.ui-modeless-panel') as HTMLElement
  const titleButton = (label: string) =>
    document.body.querySelector<HTMLButtonElement>(`.ui-modeless-titlebar-actions button[aria-label="${label}"]`)
  const CONTROLS = [
    'git-connect-remote',
    'git-connect-provider',
    'git-connect-username',
    'git-connect-secret',
    'git-connect-branch',
    'git-connect-test',
    'git-connect-cancel',
    'git-connect-submit',
  ]

  it('a wide viewport keeps the v12 place: 500px wide, 30px off the right edge', async () => {
    setViewport(1280, 800)
    await mountWindow()
    expect(panel().style.width).toBe('500px')
    expect(panel().style.left).toBe('750px')
    expect(panel().style.top).toBe('180px')
    expect(panel().style.height).toBe('360px')
  })

  it('a 375px viewport: 355px wide, 10px off each side; minimize / restore keep it and every control', async () => {
    setViewport(375, 667)
    const w = await mountWindow({ open: false })
    await w.setProps({ open: true })
    expect(panel().style.width).toBe('355px')
    expect(panel().style.left).toBe('10px')
    expect(panel().style.top).toBe('15px')
    titleButton('Minimize')?.click()
    await flushPromises()
    expect(panel().style.width).toBe('355px')
    expect(panel().style.left).toBe('10px')
    await click('git-connect-restore')
    expect(panel().style.width).toBe('355px')
    expect(panel().style.left).toBe('10px')
    expect(titleButton('Minimize')).not.toBeNull()
    expect(titleButton('Close')).not.toBeNull()
    for (const sel of CONTROLS) expect(q<HTMLInputElement>(sel)?.disabled, sel).toBe(false)
  })

  it('the viewport narrowing under the window fits it again, keeping what was typed and the focused field', async () => {
    setViewport(1280, 800)
    await mountWindow()
    await type('git-connect-remote', 'https://github.com/org/repo.git')
    await type('git-connect-secret', TYPED)
    q('git-connect-remote')?.focus()
    const before = panel()
    // jsdom lays nothing out: the window's box is given as the 1280px place it was opened at.
    before.getBoundingClientRect = () => ({ left: 750, right: 1250, top: 180, bottom: 540, width: 500, height: 360, x: 750, y: 180, toJSON: () => ({}) }) as DOMRect
    setViewport(375, 667)
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(panel()).not.toBe(before)
    expect(panel().style.width).toBe('355px')
    expect(panel().style.left).toBe('10px')
    expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('https://github.com/org/repo.git')
    expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe(TYPED)
    expect((document.activeElement as HTMLElement | null)?.dataset.test).toBe('git-connect-remote')
  })

  it('stays minimized when it is fitted again', async () => {
    setViewport(1280, 800)
    const w = await mountWindow()
    titleButton('Minimize')?.click()
    await flushPromises()
    panel().getBoundingClientRect = () => ({ left: 750, right: 1250, top: 180, bottom: 220, width: 500, height: 40, x: 750, y: 180, toJSON: () => ({}) }) as DOMRect
    setViewport(375, 667)
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(panel().style.width).toBe('355px')
    expect(q('git-connect-restore')).not.toBeNull()
    await click('git-connect-restore')
    expect(w.emitted('update:minimized')).toEqual([[true], [false]])
    for (const sel of CONTROLS) expect(q(sel), sel).not.toBeNull()
  })

  // jsdom lays nothing out: every element's box is read from its own style, a minimized panel
  // (the shell sets its height to auto) being its 40px titlebar.
  function layOutFromStyle() {
    return vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const left = parseFloat(this.style.left) || 0
      const top = parseFloat(this.style.top) || 0
      const width = parseFloat(this.style.width) || 0
      const height = this.style.height === 'auto' ? 40 : parseFloat(this.style.height) || 0
      return { left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) } as DOMRect
    })
  }

  it('minimized while only the height shrinks: restoring fits the full window on screen again', async () => {
    const layout = layOutFromStyle()
    try {
      setViewport(375, 667)
      const w = await mountWindow({ open: false })
      await w.setProps({ open: true })
      await type('git-connect-remote', 'https://github.com/org/repo.git')
      await type('git-connect-secret', TYPED)
      titleButton('Minimize')?.click()
      await flushPromises()
      const minimized = panel()
      setViewport(375, 300)
      window.dispatchEvent(new Event('resize'))
      await flushPromises()
      // Only the titlebar is on screen while minimized; nothing to fit yet.
      expect(panel()).toBe(minimized)
      await click('git-connect-restore')
      expect(w.emitted('update:minimized')).toEqual([[true], [false]])
      expect(q('git-connect-restore')).toBeNull()
      const box = panel().getBoundingClientRect()
      expect([box.left, box.top, box.width, box.height]).toEqual([10, 15, 355, 270])
      expect(box.right).toBeLessThanOrEqual(375)
      expect(box.bottom).toBeLessThanOrEqual(300)
      // The footer sits in the shell body, which scrolls inside that on-screen box.
      const body = panel().querySelector('.ui-modeless-body')
      for (const sel of CONTROLS) {
        expect(body?.contains(q(sel)), sel).toBe(true)
        expect(q<HTMLInputElement>(sel)?.disabled, sel).toBe(false)
      }
      expect(q<HTMLInputElement>('git-connect-remote')?.value).toBe('https://github.com/org/repo.git')
      expect(q<HTMLInputElement>('git-connect-secret')?.value).toBe(TYPED)
    } finally {
      layout.mockRestore()
    }
  })

  it('restoring a window that still fits leaves it where it is', async () => {
    const layout = layOutFromStyle()
    try {
      setViewport(375, 667)
      await mountWindow()
      titleButton('Minimize')?.click()
      await flushPromises()
      const before = panel()
      setViewport(375, 600)
      window.dispatchEvent(new Event('resize'))
      await click('git-connect-restore')
      expect(panel()).toBe(before)
      expect(panel().style.height).toBe('360px')
    } finally {
      layout.mockRestore()
    }
  })

  it('a window that still fits is left where it is', async () => {
    setViewport(1280, 800)
    await mountWindow()
    const before = panel()
    before.getBoundingClientRect = () => ({ left: 750, right: 1250, top: 180, bottom: 540, width: 500, height: 360, x: 750, y: 180, toJSON: () => ({}) }) as DOMRect
    setViewport(1270, 790)
    window.dispatchEvent(new Event('resize'))
    await flushPromises()
    expect(panel()).toBe(before)
    expect(panel().style.width).toBe('500px')
  })
})
