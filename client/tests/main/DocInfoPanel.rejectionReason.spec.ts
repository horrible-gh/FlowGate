import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

const LONG_REJECTION_REASON = [
  '최소 한글자 이상 넣을것',
  '',
  ...Array.from({ length: 12 }, () => '세로길이 테스트'),
].join('\n')

function mountPanel() {
  return mount(DocInfoPanel, {
    props: {
      docId: 'test.test.0015.0002-D',
      typeCode: 'D',
      reviewStatus: 'rejected',
      rejectReason: LONG_REJECTION_REASON,
      rejectionHistory: [
        {
          reason: '최소 한글자 이상 넣을것',
          rejected_at: '2026-06-12T20:43:00+09:00',
          rejected_by: null,
        },
        {
          reason: LONG_REJECTION_REASON,
          rejected_at: '2026-06-12T20:47:12+09:00',
          rejected_by: null,
        },
      ],
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('DocInfoPanel rejection reason height', () => {
  it('renders the latest multiline reason in a collapsed quote card', () => {
    const wrapper = mountPanel()
    const reason = wrapper.find('.dip-reject-reason')
    const toggle = wrapper.find('.dip-reject-quote-toggle')

    expect(reason.text()).toContain('세로길이 테스트')
    expect(reason.text()).toBe(LONG_REJECTION_REASON)
    expect(wrapper.find('.dip-reject-quote').exists()).toBe(true)
    expect(toggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('.dip-ai-history-link').exists()).toBe(true)
  })

  it('expands the quote card on demand', async () => {
    const wrapper = mountPanel()
    const toggle = wrapper.find('.dip-reject-quote-toggle')

    await toggle.trigger('click')

    expect(wrapper.find('.dip-reject-quote').classes()).toContain('open')
    expect(toggle.attributes('aria-expanded')).toBe('true')
  })

  it('uses a two-line preview and scrolls long content only when expanded', () => {
    const css = readFileSync(join(process.cwd(), 'shared/app.css'), 'utf-8')
    const block = css.match(/\.dip-reject-reason\s*\{([^}]*)\}/)?.[1] ?? ''
    const openBlock = css.match(/\.dip-reject-quote\.open \.dip-reject-reason\s*\{([^}]*)\}/)?.[1] ?? ''
    const webkitOpenBlock = css.match(
      /@supports selector\(::-webkit-scrollbar\)\s*\{\s*\.dip-reject-quote\.open \.dip-reject-reason\s*\{([^}]*)\}/,
    )?.[1] ?? ''
    const scrollbar = css.match(/\.dip-reject-quote\.open \.dip-reject-reason::\-webkit-scrollbar\s*\{([^}]*)\}/)?.[1] ?? ''
    const scrollbarTrack = css.match(/\.dip-reject-quote\.open \.dip-reject-reason::\-webkit-scrollbar-track\s*\{([^}]*)\}/)?.[1] ?? ''
    const scrollbarThumb = css.match(/\.dip-reject-quote\.open \.dip-reject-reason::\-webkit-scrollbar-thumb\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(block).toMatch(/overflow-wrap\s*:\s*anywhere/)
    expect(block).toMatch(/-webkit-line-clamp\s*:\s*2/)
    expect(openBlock).toMatch(/max-height\s*:\s*8rem/)
    expect(openBlock).toMatch(/overflow-y\s*:\s*auto/)
    expect(openBlock).toMatch(/scrollbar-color\s*:\s*#f87171 #fff1f2/)
    expect(openBlock).toMatch(/scrollbar-width\s*:\s*thin/)
    expect(webkitOpenBlock).toMatch(/scrollbar-color\s*:\s*auto/)
    expect(webkitOpenBlock).toMatch(/scrollbar-width\s*:\s*auto/)
    expect(scrollbar).toMatch(/width\s*:\s*14px/)
    expect(scrollbarTrack).toMatch(/background\s*:\s*#fff1f2/)
    expect(scrollbarThumb).toMatch(/background\s*:\s*#f87171/)
  })
})
