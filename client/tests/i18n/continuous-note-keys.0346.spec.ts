import { flushPromises, mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import i18n from '../../shared/i18n'
import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'

// flowgate.default.0346 T0005 §2-1 항목 8 / 완료 기준 "i18n 3개 언어(ko/en/ja) 키 추가 확인".
//
// Why a dedicated spec instead of leaning on tests/i18n/locales.spec.ts: that suite is
// RED on this branch's base for an unrelated reason (main.git_finalize.archive.* keys are
// referenced by a component but missing from every locale — reproduced on a clean stash of
// this worktree), so it cannot serve as the gate for THIS group's keys. These assertions are
// scoped to the four keys T0005 introduced, so they stay meaningful once that base failure
// is fixed elsewhere.
//
// The failure this guards against is the quiet one: a key added to ko.ts only. Vue-i18n
// falls back to the key path, so a ja/en user sees the literal string
// "main.continuous_work.tab_message" on the tab — and no test would notice.
//
// 0394 T0016 (NR0003 §6.2-라): the third case used to `readFileSync` ContinuousWorkDialog.vue
// and check that the string `main.continuous_work.<key>` appeared somewhere in it. That is
// text placement: the key could sit in a dead branch, in a comment, or behind a tab nobody
// can open, and the case would still pass — while the user still sees a blank tab, which is
// the exact failure it was written for. It now opens the tab and compares what is on screen
// against the locale file, in all three languages.

const { getRequest, putRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest,
}))

import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

const LOCALES = { ko, en, ja } as const

const NEW_KEYS = [
  'tab_message',
  'message_default_label',
  'message_default_placeholder',
  'message_step_placeholder',
] as const

function continuousWork(locale: (typeof LOCALES)[keyof typeof LOCALES]): Record<string, unknown> {
  return (locale as any).main.continuous_work as Record<string, unknown>
}

// Same shape the canonical query-form handler serves: N/NR done, T is the head, TR/TS/TSR pending.
function seqResponse() {
  return {
    data: {
      doc_id: 'flowgate.default.0346.0001-R',
      doc_class: 'R',
      decided: true,
      items: [
        { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
        { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
        { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
        { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 5, item_seq: 5, type: 'TS', label: '테스트시나리오', status: 'pending' },
        { id: 6, item_seq: 6, type: 'TSR', label: '테스트레포트', status: 'pending' },
      ],
      head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

/** Open the dialog and switch to the third tab — the one T0005 added. */
async function openMessageTab() {
  const wrapper = mount(ContinuousWorkDialog, {
    props: { visible: true, docRef: 'flowgate.default.0346.0001-R' },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  const tabs = document.querySelectorAll('.cwd-tab')
  expect(tabs.length, 'the dialog must show three option tabs').toBe(3)
  ;(tabs[2] as HTMLButtonElement).click()
  await nextTick()
  return wrapper
}

const text = (selector: string) =>
  (document.querySelector(selector)?.textContent ?? '').trim()

const placeholder = (selector: string) =>
  (document.querySelector(selector) as HTMLInputElement | null)?.placeholder ?? ''

beforeEach(() => {
  getRequest.mockReset().mockResolvedValue(seqResponse())
  putRequest.mockReset().mockResolvedValue({ data: { ok: true } })
})

afterEach(() => {
  document.body.innerHTML = ''
  i18n.global.locale.value = 'ko'
})

describe('[전달멘트] i18n keys (0346 T0005)', () => {
  it.each(Object.keys(LOCALES))('%s defines every new key with a non-empty string', (name) => {
    const block = continuousWork(LOCALES[name as keyof typeof LOCALES])
    for (const key of NEW_KEYS) {
      expect(block, `${name}.main.continuous_work.${key} is missing`).toHaveProperty(key)
      const value = block[key]
      expect(typeof value, `${name}.${key} must be a string`).toBe('string')
      expect((value as string).trim(), `${name}.${key} must not be blank`).not.toBe('')
    }
  })

  it('places the new tab label beside the existing two, in all three locales', () => {
    // The tab bar renders tab_basic / tab_provider / tab_message; three distinct labels are
    // what makes the third tab identifiable at all.
    for (const [name, locale] of Object.entries(LOCALES)) {
      const block = continuousWork(locale)
      const labels = [block.tab_basic, block.tab_provider, block.tab_message]
      expect(new Set(labels).size, `${name}: tab labels must be distinct`).toBe(3)
    }
  })

  it.each(Object.keys(LOCALES))('renders the tab in %s with that locale\'s own strings', async (name) => {
    // A key trio that exists in the locale files but is never rendered would pass the
    // checks above while the tab shows nothing — and a key rendered but missing from a
    // locale shows the raw key path, which is what a ja/en user actually saw.
    i18n.global.locale.value = name as 'ko' | 'en' | 'ja'
    const block = continuousWork(LOCALES[name as keyof typeof LOCALES])
    const wrapper = await openMessageTab()

    expect(text('.cwd-tabbar .cwd-tab:nth-child(3)')).toBe(block.tab_message)
    expect(text('.cwd-tab-panel .cwd-provider-label')).toBe(block.message_default_label)
    expect(placeholder('.cwd-message-default-input')).toBe(block.message_default_placeholder)
    expect(placeholder('.cwd-override-message-input')).toBe(block.message_step_placeholder)
    // The per-step rows are the reason message_step_placeholder exists at all.
    expect(document.querySelectorAll('.cwd-override-message-input').length).toBeGreaterThan(0)

    wrapper.unmount()
  })
})
