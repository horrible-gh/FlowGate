import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import SecuritySessionsView from '../src/settings/views/SecuritySessionsView.vue'

// 0394 T0004 (NR0003 §4.3 C5): this file used to replace vue-i18n with a stub that
// returned `locale` and nothing else. That was enough while the view held its strings
// inline, but the view now renders through `t()`, and a stub missing `t` fails at
// render with "$setup.t is not a function". Install the real catalog instead of
// widening the stub — the two labels asserted below are then the shipped English
// strings, so a rename in shared/i18n shows up here rather than passing against a
// copy that only this test knows about.
vi.mock('@shared/api', () => ({
  getRequest: vi.fn(async () => ({
    data: {
      sessions: [
        {
          session_id: 's1',
          device_label: null,
          ip_display: null,
          created_at: '2026-01-01T00:00:00Z',
          last_used_at: '2026-01-01T00:00:00Z',
          is_current: true,
        },
      ],
    },
  })),
  deleteRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const originalLocale = i18n.global.locale.value

afterEach(() => {
  i18n.global.locale.value = originalLocale
})

describe('SecuritySessionsView', () => {
  it('renders fallback and hides current revoke button', async () => {
    i18n.global.locale.value = 'en'

    const w = mount(SecuritySessionsView, { global: { plugins: [i18n] } })
    await new Promise((r) => setTimeout(r, 0))

    expect(w.text()).toContain(i18n.global.t('settings.security_sessions.unknown_device'))
    expect(w.text()).toContain(i18n.global.t('settings.security_sessions.current'))
    expect(w.text()).toContain('Unknown device')
    expect(w.text()).toContain('Current session')
    expect(w.findAll('article button')).toHaveLength(0)
  })
})
