import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { getRequest, postRequest } from '@shared/api'
import LoginApp from '@/login/LoginApp.vue'
import { authRedirect } from '@/login/stores/auth'

// the v0.2 sign-in page (login.html) in the mockup's
// style, with every v0.1 sign-in step in the same card and status/errors at the card bottom.
vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
  getRequest: vi.fn(),
}))

const mockedPost = vi.mocked(postRequest)
const mockedGet = vi.mocked(getRequest)
const toDashboard = vi.fn()

function mountPage() {
  return mount(LoginApp, { attachTo: document.body, global: { plugins: [i18n] } })
}

async function signIn(wrapper: ReturnType<typeof mountPage>, username = 'alice', password = 'secret') {
  await wrapper.get('#fg-login-username').setValue(username)
  await wrapper.get('#fg-login-password').setValue(password)
  await wrapper.get('form').trigger('submit')
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  mockedPost.mockReset()
  mockedGet.mockReset()
  mockedGet.mockResolvedValue({ data: { is_admin: false, roles: [] } } as never)
  toDashboard.mockReset()
  localStorage.clear()
  sessionStorage.clear()
  window.history.replaceState(null, '', '/login')
  vi.spyOn(authRedirect, 'toDashboard').mockImplementation(toDashboard)
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('v0.2 sign-in page layout (0024)', () => {
  it('shows the mockup card: brand, title, fields, OS sign-in, help and the v0.2 footer', () => {
    const wrapper = mountPage()

    expect(wrapper.get('.fg-login-logo').text()).toBe('FG')
    expect(wrapper.get('.fg-login-wordmark').text()).toBe('FlowGate')
    expect(wrapper.get('h1').text()).toBe('FlowGate 로그인')
    expect(wrapper.get('label[for="fg-login-username"]').text()).toContain('사용자명 또는 이메일')
    expect(wrapper.get('#fg-login-password').attributes('type')).toBe('password')
    expect(wrapper.get('[data-testid="login-submit"]').text()).toBe('로그인')
    expect(wrapper.get('[data-testid="os-login"]').text()).toBe('OS 로그인')
    expect(wrapper.get('.fg-login-help').text()).toBe('비밀번호를 잊으셨나요? 관리자에게 문의하세요.')
    expect(wrapper.get('.fg-login-footer').text()).toBe('FlowGate v0.2')
    expect(wrapper.find('[data-testid="login-status"]').exists()).toBe(false)

    wrapper.unmount()
  })

  it('has no workspace selector (R0001 §2: not in this change)', () => {
    const wrapper = mountPage()
    expect(wrapper.text()).not.toContain('워크스페이스')
    expect(wrapper.find('select').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows and hides the password with the eye toggle', async () => {
    const wrapper = mountPage()
    const toggle = wrapper.get('[data-testid="password-toggle"]')
    expect(toggle.attributes('aria-label')).toBe('비밀번호 표시')

    await toggle.trigger('click')
    expect(wrapper.get('#fg-login-password').attributes('type')).toBe('text')
    expect(toggle.attributes('aria-pressed')).toBe('true')
    expect(toggle.attributes('aria-label')).toBe('비밀번호 숨기기')

    await toggle.trigger('click')
    expect(wrapper.get('#fg-login-password').attributes('type')).toBe('password')
    wrapper.unmount()
  })

  it('switches KO/EN/JA and remembers the choice for /app', async () => {
    const wrapper = mountPage()
    const buttons = wrapper.findAll('.fg-login-lang')
    expect(buttons.map((b) => b.text())).toEqual(['KO', 'EN', 'JA'])
    expect(buttons[0].attributes('aria-pressed')).toBe('true')

    await buttons[1].trigger('click')
    expect(wrapper.get('h1').text()).toBe('FlowGate Sign In')
    expect(localStorage.getItem('preferred_locale')).toBe('en')
    expect(wrapper.findAll('.fg-login-lang')[1].attributes('aria-pressed')).toBe('true')

    await buttons[2].trigger('click')
    expect(wrapper.get('h1').text()).toBe('FlowGate ログイン')
    expect(localStorage.getItem('preferred_locale')).toBe('ja')
    wrapper.unmount()
  })

  it('does nothing when OS sign-in is pressed (implemented later)', async () => {
    const wrapper = mountPage()
    await wrapper.get('[data-testid="os-login"]').trigger('click')
    await flushPromises()

    expect(mockedPost).not.toHaveBeenCalled()
    expect(mockedGet).not.toHaveBeenCalled()
    expect(toDashboard).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="login-status"]').exists()).toBe(false)
    expect(wrapper.get('h1').text()).toBe('FlowGate 로그인')
    wrapper.unmount()
  })
})

describe('v0.2 sign-in page steps (0024)', () => {
  it('asks for both fields at the card bottom before sending anything', async () => {
    const wrapper = mountPage()
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(mockedPost).not.toHaveBeenCalled()
    const status = wrapper.get('[data-testid="login-status"]')
    expect(status.text()).toBe('사용자명과 비밀번호를 입력해 주세요.')
    expect(status.attributes('data-tone')).toBe('error')
    // The status block sits after the form and before the footer (mockup: bottom of the card).
    const card = wrapper.get('.fg-login-card').element
    const order = [...card.querySelectorAll('form, [data-testid="login-status"], .fg-login-footer')]
    expect(order.map((el) => el.tagName.toLowerCase())).toEqual(['form', 'div', 'footer'])
    wrapper.unmount()
  })

  it('signs in and leaves for /app', async () => {
    mockedPost.mockResolvedValue({
      data: {
        access_token: 'a', refresh_token: 'r', token_type: 'bearer',
        user: { user_id: 'u', username: 'alice', email: 'a@x', first_login_required: false },
      },
    } as never)
    const wrapper = mountPage()
    await signIn(wrapper)

    expect(mockedPost).toHaveBeenCalledWith('/auth/login', { username: 'alice', password: 'secret', remember_me: false })
    expect(toDashboard).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('shows the error, the attempt count and the last-attempt warning at the bottom', async () => {
    mockedPost.mockRejectedValue({ isAxiosError: true, response: { status: 401, data: { detail: 'invalid_credentials' } } })
    const wrapper = mountPage()

    await signIn(wrapper)
    let status = wrapper.get('[data-testid="login-status"]')
    expect(status.text()).toContain('사용자명 또는 비밀번호가 올바르지 않습니다.')
    expect(status.text()).toContain('로그인 시도 1/5회')
    expect(status.text()).not.toContain('15분')

    for (let i = 0; i < 3; i += 1) await signIn(wrapper)
    status = wrapper.get('[data-testid="login-status"]')
    expect(status.text()).toContain('로그인 시도 4/5회')
    expect(status.text()).toContain('1회 더 실패하면 15분간 잠깁니다.')
    wrapper.unmount()
  })

  it('shows the locked step with a countdown on 423', async () => {
    mockedPost.mockRejectedValue({
      isAxiosError: true,
      response: { status: 423, data: { locked_until: new Date(Date.now() + 90_000).toISOString() } },
    })
    const wrapper = mountPage()
    await signIn(wrapper)

    expect(wrapper.get('h1').text()).toBe('계정이 잠금되었습니다')
    expect(wrapper.get('[data-testid="locked-countdown"]').text()).toMatch(/잠금 해제까지: 0[01]:\d\d/)
    wrapper.unmount()
  })

  it('goes to the TOTP step, and from there to the backup-code step', async () => {
    mockedPost.mockResolvedValueOnce({ data: { totp_required: true, temp_token: 't-1' } } as never)
    const wrapper = mountPage()
    await signIn(wrapper)

    expect(wrapper.get('h1').text()).toBe('2단계 인증 (OTP)')
    expect(wrapper.findAll('.fg-login-totp-digit')).toHaveLength(6)

    await wrapper.get('[data-testid="totp-use-backup"]').trigger('click')
    expect(wrapper.get('h1').text()).toBe('백업 코드 입력')

    mockedPost.mockResolvedValueOnce({
      data: {
        access_token: 'a', refresh_token: 'r', token_type: 'bearer',
        user: { user_id: 'u', username: 'alice', email: 'a@x', first_login_required: false },
      },
    } as never)
    await wrapper.get('#fg-login-backup-code').setValue('ABCD-EFGH')
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp/backup', { temp_token: 't-1', backup_code: 'ABCD-EFGH' })
    expect(toDashboard).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('submits a TOTP code once all six digits are typed', async () => {
    mockedPost.mockResolvedValueOnce({ data: { totp_required: true, temp_token: 't-2' } } as never)
    const wrapper = mountPage()
    await signIn(wrapper)

    mockedPost.mockRejectedValueOnce({ isAxiosError: true, response: { status: 401, data: { detail: 'invalid_code' } } })
    const digits = wrapper.findAll('.fg-login-totp-digit')
    for (const [i, d] of digits.entries()) await d.setValue(String(i + 1))
    await flushPromises()

    expect(mockedPost).toHaveBeenLastCalledWith('/auth/totp', { temp_token: 't-2', code: '123456' })
    expect(wrapper.get('[data-testid="login-status"]').text()).toBe('인증 코드가 올바르지 않습니다.')
    wrapper.unmount()
  })

  it('shows the TOTP enrolment step the policy requires', async () => {
    mockedPost.mockResolvedValueOnce({ data: { totp_setup_required: true, temp_token: 'tmp-1' } } as never)
    const wrapper = mountPage()
    await signIn(wrapper)
    expect(wrapper.get('h1').text()).toBe('2단계 인증 등록')

    mockedPost.mockResolvedValueOnce({
      data: { secret: 'ABCDEFGHIJKL', qr_uri: 'otpauth://totp/x', backup_codes: ['A-1', 'B-2'], temp_token: 'tmp-enrol' },
    } as never)
    await wrapper.get('[data-testid="totp-setup-start"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="totp-setup-secret"]').text()).toBe('ABCD EFGH IJKL')
    expect(wrapper.get('[data-testid="totp-setup-backup"]').text()).toContain('A-1')
    wrapper.unmount()
  })

  it('asks for a new password when the server requires a change', async () => {
    mockedPost.mockResolvedValueOnce({
      data: {
        access_token: 'a', refresh_token: 'r', token_type: 'bearer', pw_change_required: true,
        user: { user_id: 'u', username: 'alice', email: 'a@x', first_login_required: false },
      },
    } as never)
    const wrapper = mountPage()
    await signIn(wrapper)

    expect(wrapper.get('h1').text()).toBe('비밀번호 변경 필수')
    expect(wrapper.find('#fg-login-current-password').exists()).toBe(true)
    expect(toDashboard).not.toHaveBeenCalled()

    await wrapper.get('#fg-login-current-password').setValue('old')
    await wrapper.get('#fg-login-new-password').setValue('Str0ng-Passw0rd!')
    await wrapper.get('#fg-login-confirm-password').setValue('Str0ng-Passw0rd!')
    mockedPost.mockResolvedValueOnce({ data: { message: 'ok', first_login_required: false } } as never)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mockedPost).toHaveBeenLastCalledWith('/auth/password/change', {
      current_password: 'old',
      new_password: 'Str0ng-Passw0rd!',
    })
    expect(toDashboard).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('opens the invitation step from /login?invite= and returns to sign-in with a notice', async () => {
    window.history.replaceState(null, '', '/login?invite=tok-1')
    mockedGet.mockResolvedValueOnce({ data: { username: 'Han Seojun', email: 'han@x.io' } } as never)
    const wrapper = mountPage()
    await flushPromises()

    expect(mockedGet).toHaveBeenCalledWith('/auth/invite', { token: 'tok-1' })
    expect(wrapper.get('h1').text()).toBe('초대 수락')
    expect(wrapper.text()).toContain('Han Seojun (han@x.io)')

    await wrapper.get('#fg-login-invite-password').setValue('Str0ng-Passw0rd!')
    await wrapper.get('#fg-login-invite-confirm').setValue('different')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.get('[data-testid="login-status"]').text()).toBe('새 비밀번호가 일치하지 않습니다.')
    expect(mockedPost).not.toHaveBeenCalled()

    await wrapper.get('#fg-login-invite-confirm').setValue('Str0ng-Passw0rd!')
    mockedPost.mockResolvedValueOnce({ data: {} } as never)
    await wrapper.get('form').trigger('submit')
    await flushPromises()
    expect(mockedPost).toHaveBeenCalledWith('/auth/invite/accept', { token: 'tok-1', password: 'Str0ng-Passw0rd!' })
    expect(wrapper.get('h1').text()).toBe('FlowGate 로그인')
    const status = wrapper.get('[data-testid="login-status"]')
    expect(status.text()).toBe('비밀번호가 설정되었습니다. 로그인하세요.')
    expect(status.attributes('data-tone')).toBe('success')
    wrapper.unmount()
  })
})
