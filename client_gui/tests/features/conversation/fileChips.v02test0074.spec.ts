import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// v02test.0074 card "6-화면" (시안 v2 · 6): `[name](fg-attachment:<key>)` in a message is a file chip (icon · name · size ·
// ⬇). ⬇ downloads the original straight from the Flow's attachment API (the attachment window is not opened), the name
// asks the container to show the file's row in the attachment window, a deleted file is struck through with no button,
// a replaced one downloads the file there now and says "변경됨". Under an answer the linked files are listed once more
// with [미리보기][다운로드] and, from two files up, [모두 받기(ZIP)].
const downloadBlobRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, downloadBlobRequest, postRequest }
})

import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationPanel } from '@/features/conversation'
import { toTurn } from '@/features/conversation/api'
import {
  attachmentKeyOf,
  linkedFilesOf,
  plainTextWithChips,
  previewKindOf,
} from '@/features/conversation/attachmentLinks'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import { ATTACHMENT_LINK_CONTEXT, type ConversationTurn, type TurnAttachmentLink } from '@/features/conversation/types'
import { createFakeSource, turn as fakeTurn } from './fakeConversationSource'

registerAppMessages(i18n)

const FLOW = 'p.m.0074'
const KEY_MD = 'ZG9jMQpyZXBvcnQubWQ'
const KEY_PNG = 'ZG9jMQpzaG90LnBuZw'
const KEY_OLD = 'ZG9jMQpvbGRfZHJhZnQubWQ'
const KEY_NEW = 'ZG9jMgpzcGVjLm1k'
const KEY_GONE = 'ZG9jMQpzcGVjLm1k'
const flowFile = (key: string) => `/api/v1/flows/${FLOW}/attachments/${key}`

const available = (key: string, name: string, size: number): TurnAttachmentLink => ({ key, status: 'available', name, size, replacementKey: null })
const deleted = (key: string, name: string | null): TurnAttachmentLink => ({ key, status: 'deleted', name, size: null, replacementKey: null })
const replaced = (key: string, name: string, size: number, replacementKey: string): TurnAttachmentLink => ({ key, status: 'replaced', name, size, replacementKey })

const turn = (speaker: 'user' | 'ai', body: string, attachmentLinks?: TurnAttachmentLink[], seq = 2): ConversationTurn => ({
  seq, speaker, body, createdAt: '2026-11-22T09:00:00Z', displayName: speaker === 'ai' ? 'Claude' : null, ...(attachmentLinks ? { attachmentLinks } : {}),
})

const focus = vi.fn(async (_flowId: string, _key: string) => true)
const saved = vi.fn()
let wrappers: VueWrapper[] = []

function mountMessage(t: ConversationTurn, context: boolean = true) {
  const wrapper = mount(TimelineMessage, {
    props: { turn: t, pending: false, failed: false },
    attachTo: document.body,
    global: { plugins: [i18n], provide: context ? { [ATTACHMENT_LINK_CONTEXT]: { flowId: () => FLOW, focus } } : {} },
  })
  wrappers.push(wrapper)
  return wrapper
}
const chips = (wrapper: VueWrapper) => wrapper.findAll('[data-test="tl-file-chip"]')

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  downloadBlobRequest.mockReset()
  postRequest.mockReset()
  focus.mockReset()
  focus.mockResolvedValue(true)
  saved.mockReset()
  downloadBlobRequest.mockImplementation(async () => ({ data: new Blob(['# 설계\n<b>본문</b>'], { type: 'text/markdown' }) }))
  postRequest.mockImplementation(async () => ({ data: new Blob(['zip']) }))
  URL.createObjectURL = vi.fn(() => 'blob:linked')
  URL.revokeObjectURL = vi.fn()
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    saved(this.download)
  })
})

afterEach(() => {
  for (const wrapper of wrappers) wrapper.unmount()
  wrappers = []
  vi.restoreAllMocks()
})

describe('the server state of a turn\'s links (attachment_links)', () => {
  it('reads available / replaced / deleted entries and leaves out what it cannot use', () => {
    const parsed = toTurn({
      seq: 3, speaker: 'ai', body: 'x',
      attachment_links: [
        { key: KEY_MD, status: 'available', name: 'report.md', size: 8601, replacement_key: null },
        { key: KEY_GONE, status: 'replaced', name: 'spec.md', size: 12, replacement_key: KEY_NEW },
        { key: KEY_OLD, status: 'deleted', name: 'old_draft.md', size: null, replacement_key: null },
        { key: 'x1', status: 'replaced', name: 'n.md', size: 1, replacement_key: null },
        { key: '', status: 'available' },
        { key: 'x2', status: 'archived' },
        null,
      ],
    } as never)
    expect(parsed.attachmentLinks).toEqual([
      available(KEY_MD, 'report.md', 8601),
      replaced(KEY_GONE, 'spec.md', 12, KEY_NEW),
      deleted(KEY_OLD, 'old_draft.md'),
      // replaced without the new key cannot be downloaded: shown as deleted, never guessed.
      { key: 'x1', status: 'deleted', name: 'n.md', size: 1, replacementKey: null },
    ])
  })

  it('keeps "the server said nothing" apart from "no links", and drops the list of a deleted message', () => {
    expect(toTurn({ seq: 1, speaker: 'user', body: 'a' } as never).attachmentLinks).toBeUndefined()
    expect(toTurn({ seq: 1, speaker: 'user', body: 'a', attachment_links: [] } as never).attachmentLinks).toEqual([])
    expect(toTurn({ seq: 1, speaker: 'user', body: '', deleted_at: '2026-11-22T00:00:00Z', attachment_links: [available(KEY_MD, 'a', 1)] } as never).attachmentLinks).toBeUndefined()
  })

  it('takes only fg-attachment addresses with a key', () => {
    expect(attachmentKeyOf(`fg-attachment:${KEY_MD}`)).toBe(KEY_MD)
    expect(attachmentKeyOf(` <fg-attachment:${KEY_MD}> `)).toBe(KEY_MD)
    expect(attachmentKeyOf('https://example.com/a')).toBeNull()
    expect(attachmentKeyOf('fg-attachment:')).toBeNull()
    expect(attachmentKeyOf('fg-attachment:a b')).toBeNull()
    expect(attachmentKeyOf('javascript:fg-attachment:abc')).toBeNull()
  })
})

describe('an AI answer draws fg-attachment links as file chips', () => {
  const BODY = `설계는 [attachment_renewal_45_R.md](fg-attachment:${KEY_MD})에 있고 화면은 [shot.png](fg-attachment:${KEY_PNG}) 입니다.`

  it('shows icon, the current name, the size and ⬇ for a file that is there', () => {
    const wrapper = mountMessage(turn('ai', BODY, [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 41 * 1024)]))
    const [md, png] = chips(wrapper)
    expect(chips(wrapper)).toHaveLength(2)
    expect(md.attributes('data-status')).toBe('available')
    expect(md.attributes('data-attachment-key')).toBe(KEY_MD)
    // the server's current name, not the text the AI wrote.
    expect(md.get('[data-test="tl-file-chip-name"]').text()).toBe('report.md')
    expect(md.get('[data-test="tl-file-chip-size"]').text()).toBe('8.4 KB')
    expect(md.get('[data-test="tl-file-chip-download"]').attributes('aria-label')).toBe('report.md 다운로드')
    expect(md.find('[data-test="tl-file-chip-tag"]').exists()).toBe(false)
    expect(md.get('.tl-fchip-ico').text()).toBe('📄')
    expect(png.get('.tl-fchip-ico').text()).toBe('🖼')
    expect(png.get('[data-test="tl-file-chip-size"]').text()).toBe('41.0 KB')
    // the sentence around the chips is still there, in order.
    expect(wrapper.get('[data-test="tl-md"]').text()).toMatch(/^설계는.*report\.md.*에 있고 화면은.*shot\.png.*입니다\.$/s)
  })

  it('is never an ordinary link and never leaves the address in the page', () => {
    const wrapper = mountMessage(turn('ai', `${BODY}\n\n[문서](https://example.com/doc)`, [available(KEY_MD, 'report.md', 1), available(KEY_PNG, 'shot.png', 1)]))
    const anchors = wrapper.findAll('[data-test="tl-md"] a')
    expect(anchors.map((a) => a.attributes('href'))).toEqual(['https://example.com/doc'])
    expect(wrapper.get('[data-test="tl-md"]').html()).not.toContain('fg-attachment:')
    expect(wrapper.get('[data-test="tl-md"]').text()).not.toContain('](')
  })

  it('escapes a file name that looks like HTML', () => {
    const wrapper = mountMessage(turn('ai', `[x](fg-attachment:${KEY_MD})`, [available(KEY_MD, '<img src=x onerror=1>.md', 5)]))
    expect(wrapper.find('[data-test="tl-md"] img').exists()).toBe(false)
    expect(chips(wrapper)[0].get('[data-test="tl-file-chip-name"]').text()).toBe('<img src=x onerror=1>.md')
    expect(chips(wrapper)[0].attributes('data-file-name')).toBe('<img src=x onerror=1>.md')
  })

  it('leaves a link written inside code as code', () => {
    const wrapper = mountMessage(turn('ai', ['형식:', '', '```', `[이름](fg-attachment:${KEY_MD})`, '```', '', `그리고 \`[a](fg-attachment:${KEY_MD})\``].join('\n'), []))
    expect(chips(wrapper)).toHaveLength(0)
    expect(wrapper.get('[data-test="tl-md"] pre code').text()).toContain(`[이름](fg-attachment:${KEY_MD})`)
    expect(wrapper.find('[data-test="tl-linked-files"]').exists()).toBe(false)
  })

  it('⬇ downloads the original at once from the Flow attachment address and does not open the window', async () => {
    const wrapper = mountMessage(turn('ai', BODY, [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 1)]))
    await chips(wrapper)[0].get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_MD)])
    expect(saved.mock.calls).toEqual([['report.md']])
    expect(focus).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="tl-file-error"]').exists()).toBe(false)
  })

  it('says so when the download fails', async () => {
    downloadBlobRequest.mockRejectedValue(Object.assign(new Error('gone'), { response: { status: 404 } }))
    const wrapper = mountMessage(turn('ai', BODY, [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 1)]))
    await chips(wrapper)[1].get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(saved).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="tl-file-error"]').text()).toBe('파일을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.')
  })

  it('the name asks the container to show that file in the attachment window, without downloading', async () => {
    const wrapper = mountMessage(turn('ai', BODY, [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 1)]))
    await chips(wrapper)[1].get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(focus.mock.calls).toEqual([[FLOW, KEY_PNG]])
    expect(downloadBlobRequest).not.toHaveBeenCalled()
    expect(wrapper.find('[data-test="tl-file-error"]').exists()).toBe(false)
  })

  it('says so when the attachment window cannot show the file', async () => {
    focus.mockResolvedValue(false)
    const wrapper = mountMessage(turn('ai', BODY, [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 1)]))
    await chips(wrapper)[0].get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-file-error"]').text()).toBe('첨부파일 창에서 이 파일을 찾지 못했습니다.')
  })

  it('a deleted file: struck-through chip with "삭제됨" and no button', async () => {
    const wrapper = mountMessage(turn('ai', `예전 초안 [old_draft_R.md](fg-attachment:${KEY_OLD})은 없습니다.`, [deleted(KEY_OLD, null)]))
    const chip = chips(wrapper)[0]
    expect(chip.attributes('data-status')).toBe('deleted')
    expect(chip.classes()).toContain('tl-fchip--deleted')
    // the server could not tell the name: the text the AI wrote is kept.
    expect(chip.get('[data-test="tl-file-chip-name"]').text()).toBe('old_draft_R.md')
    expect(chip.get('[data-test="tl-file-chip-tag"]').text()).toBe('삭제됨')
    expect(chip.findAll('button')).toHaveLength(0)
    expect(chip.find('[data-test="tl-file-chip-size"]').exists()).toBe(false)
    await chip.get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(focus).not.toHaveBeenCalled()
    expect(downloadBlobRequest).not.toHaveBeenCalled()
    // nothing to list either.
    expect(wrapper.find('[data-test="tl-linked-files"]').exists()).toBe(false)
  })

  it('a replaced file: "변경됨", and both ⬇ and the name use the file that is there now', async () => {
    const wrapper = mountMessage(turn('ai', `[spec.md](fg-attachment:${KEY_GONE})`, [replaced(KEY_GONE, 'spec.md', 2048, KEY_NEW)]))
    const chip = chips(wrapper)[0]
    expect(chip.attributes('data-status')).toBe('replaced')
    expect(chip.get('[data-test="tl-file-chip-tag"]').text()).toBe('변경됨')
    expect(chip.get('[data-test="tl-file-chip-size"]').text()).toBe('2.0 KB')
    await chip.get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_NEW)])
    expect(saved.mock.calls).toEqual([['spec.md']])
    await chip.get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(focus.mock.calls).toEqual([[FLOW, KEY_NEW]])
  })

  it('follows a later state of the same message (the file was deleted afterwards)', async () => {
    const wrapper = mountMessage(turn('ai', `[report.md](fg-attachment:${KEY_MD})`, [available(KEY_MD, 'report.md', 10)]))
    expect(chips(wrapper)[0].attributes('data-status')).toBe('available')
    await wrapper.setProps({ turn: turn('ai', `[report.md](fg-attachment:${KEY_MD})`, [deleted(KEY_MD, 'report.md')]) })
    expect(chips(wrapper)[0].attributes('data-status')).toBe('deleted')
    expect(chips(wrapper)[0].findAll('button')).toHaveLength(0)
    expect(wrapper.find('[data-test="tl-linked-files"]').exists()).toBe(false)
  })

  it('still draws a chip when the server said nothing about the key, and its buttons still work', async () => {
    const wrapper = mountMessage(turn('ai', `[report.md](fg-attachment:${KEY_MD})`))
    const chip = chips(wrapper)[0]
    expect(chip.attributes('data-status')).toBe('unknown')
    expect(chip.get('[data-test="tl-file-chip-name"]').text()).toBe('report.md')
    expect(chip.find('[data-test="tl-file-chip-size"]').exists()).toBe(false)
    await chip.get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_MD)])
  })

  it('without a panel around it the chip draws and its buttons report instead of throwing', async () => {
    const wrapper = mountMessage(turn('ai', `[report.md](fg-attachment:${KEY_MD})`, [available(KEY_MD, 'report.md', 10)]), false)
    await chips(wrapper)[0].get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest).not.toHaveBeenCalled()
    expect(wrapper.get('[data-test="tl-file-error"]').exists()).toBe(true)
  })

  it('keeps the code block copy button working next to chips', async () => {
    const wrapper = mountMessage(turn('ai', [`[report.md](fg-attachment:${KEY_MD})`, '', '```ts', 'const a = 1', '```'].join('\n'), [available(KEY_MD, 'report.md', 10)]))
    expect(wrapper.find('.code-copy-btn').exists()).toBe(true)
    expect(chips(wrapper)).toHaveLength(1)
  })
})

describe('a person\'s message draws the same chip and stays plain text otherwise', () => {
  it('draws the chip, escapes the rest and keeps the line breaks', async () => {
    const body = `<b>굵게</b> 아님\n이 파일 [report.md](fg-attachment:${KEY_MD}) 봐줘 **별표**`
    const wrapper = mountMessage(turn('user', body, [available(KEY_MD, 'report.md', 8601)], 1))
    const box = wrapper.get('[data-test="tl-user-links"]')
    expect(box.find('b').exists()).toBe(false)
    expect(box.find('strong').exists()).toBe(false)
    expect(box.text()).toContain('<b>굵게</b> 아님')
    expect(box.text()).toContain('**별표**')
    expect(box.element.innerHTML).toContain('아님\n이 파일')
    const chip = chips(wrapper)[0]
    expect(chip.attributes('data-status')).toBe('available')
    expect(chip.get('[data-test="tl-file-chip-size"]').text()).toBe('8.4 KB')
    await chip.get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_MD)])
    await chip.get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(focus.mock.calls).toEqual([[FLOW, KEY_MD]])
    // the list under an answer is for answers only.
    expect(wrapper.find('[data-test="tl-linked-files"]').exists()).toBe(false)
  })

  it('shows a message without such a link exactly as before', () => {
    const wrapper = mountMessage(turn('user', '그냥 [링크](https://example.com) 글', [], 1))
    expect(wrapper.find('[data-test="tl-user-links"]').exists()).toBe(false)
    expect(wrapper.get('.tl-text').text()).toBe('그냥 [링크](https://example.com) 글')
    expect(wrapper.get('.tl-text').element.children).toHaveLength(0)
  })

  it('chips only the keys the server listed, every link while the server has not answered yet, and never the image form', () => {
    const labels = { deleted: 'D', replaced: 'R', open: (n: string) => n, download: (n: string) => n }
    const text = `\`[a](fg-attachment:${KEY_OLD})\` 와 [b](fg-attachment:${KEY_MD}) 와 ![c](fg-attachment:${KEY_PNG})`
    const listed = plainTextWithChips(text, [available(KEY_MD, 'b.md', 1)], labels)!
    expect(listed.match(/data-test="tl-file-chip"/g)).toHaveLength(1)
    expect(listed).toContain(`[a](fg-attachment:${KEY_OLD})`)
    expect(listed).toContain(`![c](fg-attachment:${KEY_PNG})`)
    const pending = plainTextWithChips(text, undefined, labels)!
    expect(pending.match(/data-test="tl-file-chip"/g)).toHaveLength(2)
    expect(pending).toContain(`![c](fg-attachment:${KEY_PNG})`)
    expect(plainTextWithChips('fg-attachment: 라는 글자만', undefined, labels)).toBeNull()
    expect(plainTextWithChips('링크 없음', undefined, labels)).toBeNull()
  })
})

describe('the linked files under an answer', () => {
  const BODY = `[a](fg-attachment:${KEY_MD}) [b](fg-attachment:${KEY_PNG}) [a 다시](fg-attachment:${KEY_MD}) [c](fg-attachment:${KEY_OLD}) [d](fg-attachment:${KEY_GONE})`
  const LINKS = [available(KEY_MD, 'report.md', 8601), available(KEY_PNG, 'shot.png', 41 * 1024), deleted(KEY_OLD, 'old_draft.md'), replaced(KEY_GONE, 'spec.md', 2048, KEY_NEW)]
  const files = (wrapper: VueWrapper) => wrapper.findAll('[data-test="tl-linked-file"]')

  it('lists each existing file once (a replaced link as the file there now), not the deleted one', () => {
    expect(linkedFilesOf(LINKS)).toEqual([
      { key: KEY_MD, name: 'report.md', size: 8601, replaced: false },
      { key: KEY_PNG, name: 'shot.png', size: 41 * 1024, replaced: false },
      { key: KEY_NEW, name: 'spec.md', size: 2048, replaced: true },
    ])
    expect(linkedFilesOf(undefined)).toEqual([])
    const wrapper = mountMessage(turn('ai', BODY, LINKS))
    expect(wrapper.get('[data-test="tl-linked-count"]').text()).toBe('이 답변에 링크된 파일 3개')
    expect(files(wrapper).map((file) => file.attributes('data-attachment-key'))).toEqual([KEY_MD, KEY_PNG, KEY_NEW])
    expect(files(wrapper).map((file) => file.get('[data-test="tl-linked-name"]').text())).toEqual(['report.md', 'shot.png', 'spec.md'])
    expect(files(wrapper)[0].get('[data-test="tl-linked-size"]').text()).toBe('8.4 KB')
    expect(files(wrapper)[2].get('[data-test="tl-linked-replaced"]').text()).toBe('변경됨')
    for (const [i, file] of files(wrapper).entries()) {
      expect(file.get('[data-test="tl-linked-preview"]').text()).toBe('미리보기')
      // Icon only: no visible word, the name stays in the accessible label and the tooltip.
      const download = file.get('[data-test="tl-linked-download"]')
      expect(download.text()).toBe('⬇')
      const label = `${['report.md', 'shot.png', 'spec.md'][i]} 다운로드`
      expect(download.attributes('aria-label')).toBe(label)
      expect(download.attributes('title')).toBe(label)
    }
  })

  it('offers [모두 받기(ZIP)] from two files up and sends the listed keys', async () => {
    const one = mountMessage(turn('ai', `[a](fg-attachment:${KEY_MD})`, [available(KEY_MD, 'report.md', 1)]))
    expect(one.get('[data-test="tl-linked-count"]').text()).toBe('이 답변에 링크된 파일 1개')
    expect(one.find('[data-test="tl-linked-zip"]').exists()).toBe(false)

    const wrapper = mountMessage(turn('ai', BODY, LINKS))
    expect(wrapper.get('[data-test="tl-linked-zip"]').text()).toBe('모두 받기(ZIP)')
    await wrapper.get('[data-test="tl-linked-zip"]').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(1)
    expect(postRequest.mock.calls[0][0]).toBe(`/api/v1/flows/${FLOW}/attachments-zip`)
    expect(postRequest.mock.calls[0][1]).toEqual({ keys: [KEY_MD, KEY_PNG, KEY_NEW] })
    expect(postRequest.mock.calls[0][2]).toEqual({ responseType: 'blob' })
    expect(saved.mock.calls).toEqual([[`${FLOW}_attachments.zip`]])
  })

  it('[다운로드] downloads that file and reports a failure', async () => {
    const wrapper = mountMessage(turn('ai', BODY, LINKS))
    await files(wrapper)[2].get('[data-test="tl-linked-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_NEW)])
    expect(saved.mock.calls).toEqual([['spec.md']])
    downloadBlobRequest.mockRejectedValue(new Error('offline'))
    await files(wrapper)[0].get('[data-test="tl-linked-download"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-linked-error"]').text()).toBe('파일을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.')
    postRequest.mockRejectedValue(new Error('offline'))
    await wrapper.get('[data-test="tl-linked-zip"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-linked-error"]').exists()).toBe(true)
    expect(saved).toHaveBeenCalledTimes(1)
  })

  it('[미리보기] shows a text file as text (never as HTML), an image as an image, and closes again', async () => {
    const wrapper = mountMessage(turn('ai', BODY, LINKS))
    await files(wrapper)[0].get('[data-test="tl-linked-preview"]').trigger('click')
    await flushPromises()
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_MD)])
    const text = wrapper.get('[data-test="tl-linked-preview-text"]')
    expect(text.text()).toBe('# 설계\n<b>본문</b>')
    expect(text.find('b').exists()).toBe(false)
    expect(saved).not.toHaveBeenCalled()
    expect(files(wrapper)[0].get('[data-test="tl-linked-preview"]').text()).toBe('미리보기 닫기')

    // another file: the first preview closes (one at a time).
    await files(wrapper)[1].get('[data-test="tl-linked-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="tl-linked-preview-text"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="tl-linked-preview-image"]').attributes('src')).toBe('blob:linked')

    await files(wrapper)[1].get('[data-test="tl-linked-preview"]').trigger('click')
    expect(wrapper.find('[data-test="tl-linked-preview-body"]').exists()).toBe(false)
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:linked')
  })

  it('says a file cannot be previewed without fetching it, and reports a failed read', async () => {
    const wrapper = mountMessage(turn('ai', `[z](fg-attachment:${KEY_MD}) [m](fg-attachment:${KEY_PNG})`, [available(KEY_MD, 'bundle.zip', 100), available(KEY_PNG, 'notes.md', 10)]))
    await files(wrapper)[0].get('[data-test="tl-linked-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-linked-preview-none"]').text()).toBe('미리 볼 수 없는 파일입니다. 다운로드해서 확인해 주세요.')
    expect(downloadBlobRequest).not.toHaveBeenCalled()
    downloadBlobRequest.mockRejectedValue(new Error('offline'))
    await files(wrapper)[1].get('[data-test="tl-linked-preview"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-linked-preview-body"]').text()).toBe('미리보기를 불러오지 못했습니다.')
  })

  it('decides the preview kind from the name and size only', () => {
    expect(previewKindOf('a.md', 10)).toBe('text')
    expect(previewKindOf('a.JSON', 1024 * 1024)).toBe('text')
    expect(previewKindOf('a.log', 1024 * 1024 + 1)).toBe('none')
    expect(previewKindOf('a.PNG', 10 * 1024 * 1024)).toBe('image')
    expect(previewKindOf('a.png', 10 * 1024 * 1024 + 1)).toBe('none')
    expect(previewKindOf('a.pdf', 10)).toBe('none')
    expect(previewKindOf('a.md', null)).toBe('none')
  })

  it('the name in the list goes to the attachment window too', async () => {
    const wrapper = mountMessage(turn('ai', BODY, LINKS))
    await files(wrapper)[2].get('[data-test="tl-linked-name"]').trigger('click')
    await flushPromises()
    expect(focus.mock.calls).toEqual([[FLOW, KEY_NEW]])
    focus.mockResolvedValue(false)
    await files(wrapper)[0].get('[data-test="tl-linked-name"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-linked-error"]').text()).toBe('첨부파일 창에서 이 파일을 찾지 못했습니다.')
  })
})

describe('the panel hands the chips its Flow and the container\'s focusAttachment', () => {
  const CHAT = 'p.m.0074.0002-CH'

  it('a chip name in the open chat calls focusAttachment(flow, key); ⬇ downloads from that Flow', async () => {
    const focusAttachment = vi.fn(async (_flowId: string, _key: string) => true)
    const fake = createFakeSource({
      chats: [{ id: CHAT, title: '파일', updatedAt: new Date().toISOString() }],
      turns: {
        [CHAT]: [
          fakeTurn(1, 'user', '파일 줘', '2026-11-22T08:02:00Z'),
          fakeTurn(2, 'ai', `여기 [report.md](fg-attachment:${KEY_MD})`, '2026-11-22T08:03:00Z', { attachmentLinks: [available(KEY_MD, 'report.md', 8601)] }),
        ],
      },
    })
    const wrapper = mount(ConversationPanel, {
      props: { projectId: 'p', groupId: FLOW, source: fake.source, focusAttachment },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    wrappers.push(wrapper)
    await flushPromises()
    const chip = wrapper.get('[data-test="tl-file-chip"]')
    await chip.get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(focusAttachment.mock.calls).toEqual([[FLOW, KEY_MD]])
    await chip.get('[data-test="tl-file-chip-download"]').trigger('click')
    await flushPromises()
    expect(downloadBlobRequest.mock.calls.map((call) => call[0])).toEqual([flowFile(KEY_MD)])
    expect(saved.mock.calls).toEqual([['report.md']])
  })

  it('without a focusAttachment from the container the name reports that the file was not found', async () => {
    const fake = createFakeSource({
      chats: [{ id: CHAT, title: '파일', updatedAt: new Date().toISOString() }],
      turns: { [CHAT]: [fakeTurn(1, 'ai', `[report.md](fg-attachment:${KEY_MD})`, '2026-11-22T08:03:00Z', { attachmentLinks: [available(KEY_MD, 'report.md', 1)] })] },
    })
    const wrapper = mount(ConversationPanel, { props: { projectId: 'p', groupId: FLOW, source: fake.source }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await flushPromises()
    await wrapper.get('[data-test="tl-file-chip-name"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-test="tl-file-error"]').text()).toBe('첨부파일 창에서 이 파일을 찾지 못했습니다.')
  })
})
