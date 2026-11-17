import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import { ConversationRequestError } from '@/features/conversation/api'
import type { ConversationTurn, TurnImage } from '@/features/conversation/types'

const net = vi.hoisted(() => ({ blob: vi.fn() }))
vi.mock('@/features/conversation/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/api')>()),
  fetchMessageImageBlob: net.blob,
}))

registerAppMessages(i18n)

// flowgate.v02test.0029 T0005 — a message shows its images as a thumbnail row; one opens the original large (modal,
// Esc closes). The bytes come through the signed-in session and are shown from blob addresses.
const image = (n: number): TurnImage => ({
  imageId: `img_${String(n).padStart(32, '0')}`, mediaType: 'image/png', size: 100, width: 40, height: 30,
  url: `/flowgate/api/v1/flows/p.m.0001/message-images/img_${String(n).padStart(32, '0')}/content`,
})
const turn = (speaker: 'user' | 'ai', images?: TurnImage[]): ConversationTurn => ({
  seq: 3, speaker, body: '이 화면 봐줘', createdAt: '2026-11-17T09:00:00Z', ...(images ? { images } : {}),
})

let wrappers: VueWrapper[] = []
function mountMessage(t: ConversationTurn) {
  const wrapper = mount(TimelineMessage, { props: { turn: t, pending: false, failed: false }, attachTo: document.body, global: { plugins: [i18n] } })
  wrappers.push(wrapper)
  return wrapper
}

let urlCount = 0
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  urlCount = 0
  net.blob.mockReset().mockImplementation(async () => new Blob(['x'], { type: 'image/png' }))
  URL.createObjectURL = vi.fn(() => `blob:img-${(urlCount += 1)}`)
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  document.body.innerHTML = ''
})

const thumbs = (w: VueWrapper) => w.findAll('[data-test="tl-image"]')
const viewer = () => document.body.querySelector('[data-test="tl-image-viewer"]')

describe('message thumbnails', () => {
  it('shows one thumbnail per image under the text, fetched with the session and shown from blob addresses', async () => {
    const w = mountMessage(turn('user', [image(1), image(2)]))
    await flushPromises()
    expect(thumbs(w)).toHaveLength(2)
    expect(net.blob.mock.calls.map((c) => c[0])).toEqual([image(1).url, image(2).url])
    const srcs = w.findAll('img.tl-img-pic').map((img) => img.attributes('src'))
    expect(srcs).toEqual(['blob:img-1', 'blob:img-2'])
    // The content address (and any token) is never the picture's own address.
    for (const src of srcs) expect(src).not.toContain('/api/v1')
    expect(w.find('.tl-text').text()).toBe('이 화면 봐줘')
  })

  it('shows images on an AI message too, and nothing on a text message', async () => {
    expect(thumbs(mountMessage(turn('ai', [image(1)])))).toHaveLength(1)
    expect(mountMessage(turn('user')).find('[data-test="tl-images"]').exists()).toBe(false)
    expect(mountMessage(turn('user', [])).find('[data-test="tl-images"]').exists()).toBe(false)
  })

  it('a thumbnail that could not be loaded says so and is tried again when pressed', async () => {
    net.blob.mockRejectedValueOnce(new ConversationRequestError('', 500, null))
    const w = mountMessage(turn('user', [image(1)]))
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-state')).toBe('failed')
    expect(w.get('[data-test="tl-image-failed"]').text()).toBe('불러오지 못했습니다')
    await thumbs(w)[0].trigger('click')
    await flushPromises()
    expect(net.blob).toHaveBeenCalledTimes(2)
    expect(thumbs(w)[0].attributes('data-state')).toBe('ready')
    expect(viewer()).toBeNull()
  })

  it('gives the blob addresses back when the message leaves', async () => {
    const w = mountMessage(turn('user', [image(1)]))
    await flushPromises()
    w.unmount()
    wrappers = []
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:img-1')
  })
})

describe('original view', () => {
  it('opens the original large on press and closes with Esc', async () => {
    const w = mountMessage(turn('user', [image(1)]))
    await flushPromises()
    expect(viewer()).toBeNull()
    await thumbs(w)[0].trigger('click')
    await flushPromises()
    const dialog = viewer()
    expect(dialog).not.toBeNull()
    expect(dialog?.getAttribute('role')).toBe('dialog')
    expect(dialog?.getAttribute('aria-modal')).toBe('true')
    expect(dialog?.querySelector('[data-test="tl-image-full"]')?.getAttribute('src')).toBe('blob:img-1')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(viewer()).toBeNull()
  })

  it('closes with the ✕ and with a press on the backdrop, not on the picture', async () => {
    const w = mountMessage(turn('user', [image(1)]))
    await flushPromises()
    await thumbs(w)[0].trigger('click')
    await flushPromises()
    ;(document.body.querySelector('[data-test="tl-image-close"]') as HTMLElement).click()
    await flushPromises()
    expect(viewer()).toBeNull()
    await thumbs(w)[0].trigger('click')
    await flushPromises()
    ;(document.body.querySelector('[data-test="tl-image-full"]') as HTMLElement).click()
    await flushPromises()
    expect(viewer()).not.toBeNull()
    ;(viewer() as HTMLElement).click()
    await flushPromises()
    expect(viewer()).toBeNull()
  })

  it('steps between the images of the message', async () => {
    const w = mountMessage(turn('user', [image(1), image(2)]))
    await flushPromises()
    await thumbs(w)[1].trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="tl-image-full"]')?.getAttribute('src')).toBe('blob:img-2')
    ;(document.body.querySelector('[data-test="tl-image-next"]') as HTMLElement).click()
    await flushPromises()
    expect(document.body.querySelector('[data-test="tl-image-full"]')?.getAttribute('src')).toBe('blob:img-1')
  })

  it('does not offer stepping for a single image', async () => {
    const w = mountMessage(turn('user', [image(1)]))
    await flushPromises()
    await thumbs(w)[0].trigger('click')
    await flushPromises()
    expect(document.body.querySelector('[data-test="tl-image-next"]')).toBeNull()
  })
})
