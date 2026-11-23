import { mount, type VueWrapper } from '@vue/test-utils'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { LINE_ICON_PATHS } from '@/components/ui/LineIcon.vue'
import { toAttachmentActivity, toAttachmentGroups } from '@/features/conversation/api'
import TimelineAttachments from '@/features/conversation/TimelineAttachments.vue'

// v02test.0074 0011-T: one shared paperclip line icon (LineIcon `paperclip`) for the chat input's attachments button and
// the chat's AI attachment request block — no 📎 emoji left in either, and no second copy of the SVG path.
registerAppMessages(i18n)

const SRC = resolve(__dirname, '../../../src')
const read = (path: string) => readFileSync(resolve(SRC, path), 'utf8')

let wrapper: VueWrapper | null = null
beforeEach(() => {
  i18n.global.locale.value = 'ko'
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

const RAW = { request_id: 'aur_0123456789abcdef01234567', status: 'pending', filename: 'report.md', size: 1, ai_run_id: 'run-1' }

describe('shared paperclip icon', () => {
  it('is part of the shared line icon set', () => {
    expect(LINE_ICON_PATHS.paperclip).toMatch(/^<path d="[^"]+"\/>$/)
  })

  it('heads the AI attachment request block instead of the 📎 emoji', () => {
    const block = toAttachmentActivity(toAttachmentGroups([{ id: 'run-1', run_id: 'run-1', pending: 1, requests: [RAW] }])[0])
    wrapper = mount(TimelineAttachments, { props: { activity: block }, global: { plugins: [i18n] } })
    const head = wrapper.get('.tl-ar-head')
    expect(head.text()).not.toContain('📎')
    const icon = head.find('svg')
    expect(icon.exists()).toBe(true)
    expect(icon.attributes('data-icon')).toBe('paperclip')
    expect(icon.attributes('aria-hidden')).toBe('true')
    expect(head.text()).toContain('승인 대기 1건')
  })

  it('is drawn through LineIcon in both places, with no emoji and no copied path in their markup', () => {
    const d = LINE_ICON_PATHS.paperclip.match(/d="([^"]+)"/)![1]
    for (const file of ['features/conversation/ConversationComposer.vue', 'features/conversation/TimelineAttachments.vue']) {
      const src = read(file)
      const template = src.slice(0, src.indexOf('<script'))
      expect(template, file).toContain('<LineIcon name="paperclip"')
      expect(template, file).not.toContain('📎')
      expect(src, file).not.toContain(d)
    }
  })
})
