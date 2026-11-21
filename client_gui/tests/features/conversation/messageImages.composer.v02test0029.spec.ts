import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { ConversationRequestError, DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import ConversationComposer from '@/features/conversation/ConversationComposer.vue'
import { draftKey, draftUserId, loadDraftImages, saveDraftImages, type DraftOwner } from '@/features/conversation/drafts'
import { imageDeps } from '@/features/conversation/imageResize'

const net = vi.hoisted(() => ({ upload: vi.fn(), remove: vi.fn(), blob: vi.fn() }))
vi.mock('@/features/conversation/api', async (original) => ({
  ...(await original<typeof import('@/features/conversation/api')>()),
  uploadMessageImage: net.upload,
  deleteMessageImage: net.remove,
  fetchMessageImageBlob: net.blob,
}))

registerAppMessages(i18n)

// the composer side of message images: paste / drop → thumbnail → upload at once (by flow
// id, also before the chat exists) → send holds until every image is ready → `images: [image_id…]` goes with the text.
const FLOW = 'p.m.0001'
const CHAT = 'p.m.0001.0002-CH'
const original = { measure: imageDeps.measure, resize: imageDeps.resize }

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: unknown) => void
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const file = (name = 'shot.png', type = 'image/png') => new File([new Uint8Array([1, 2, 3])], name, { type })
const meta = (n: number) => ({ imageId: `img_${String(n).padStart(32, '0')}`, mediaType: 'image/png', size: 3, width: 10, height: 10, url: `/api/v1/flows/${FLOW}/message-images/x/content` })

let wrappers: VueWrapper[] = []
function mountComposer(props: Record<string, unknown> = {}) {
  const wrapper = mount(ConversationComposer, {
    props: {
      chatId: CHAT,
      projectId: 'p',
      groupId: FLOW,
      disabled: false,
      settings: DEFAULT_CHAT_SETTINGS,
      savingSettings: false,
      pendingSourceMode: null,
      providers: [],
      selectedProviderId: '',
      providersStatus: 'ready',
      hasProviders: false,
      sending: false,
      running: false,
      runStarting: false,
      cancelling: false,
      copyingMention: false,
      manualCopyText: null,
      onSend: vi.fn(async () => true),
      ...props,
    },
    attachTo: document.body,
    global: { plugins: [i18n], stubs: { ComposerModelPicker: true, ComposerSourceModes: true, ComposerSettings: true, ComposerToolsMenu: true } },
  })
  wrappers.push(wrapper)
  return wrapper
}

function paste(wrapper: VueWrapper, files: File[], text = '') {
  const event = new Event('paste', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'clipboardData', { value: { files, items: [], getData: (type: string) => (type === 'text/plain' ? text : '') } })
  wrapper.get('[data-test="conv-input"]').element.dispatchEvent(event)
  return event
}

function drop(wrapper: VueWrapper, files: File[]) {
  const event = new Event('drop', { bubbles: true, cancelable: true })
  Object.defineProperty(event, 'dataTransfer', { value: { files, types: ['Files'] } })
  wrapper.get('[data-test="conv-composer"]').element.dispatchEvent(event)
  return event
}

const send = (w: VueWrapper) => w.get('[data-test="conv-send"]')
const thumbs = (w: VueWrapper) => w.findAll('[data-test="conv-image"]')
const type = async (w: VueWrapper, text: string) => { await w.get('[data-test="conv-input"]').setValue(text) }

let counter = 0
beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  counter = 0
  net.upload.mockReset().mockImplementation(async () => meta((counter += 1)))
  net.remove.mockReset().mockResolvedValue(undefined)
  net.blob.mockReset().mockResolvedValue(new Blob(['x'], { type: 'image/png' }))
  imageDeps.measure = vi.fn().mockResolvedValue({ width: 100, height: 80 })
  imageDeps.resize = vi.fn()
  URL.createObjectURL = vi.fn(() => 'blob:thumb')
  URL.revokeObjectURL = vi.fn()
})

afterEach(() => {
  for (const w of wrappers) w.unmount()
  wrappers = []
  imageDeps.measure = original.measure
  imageDeps.resize = original.resize
})

describe('paste and drop', () => {
  it('pasting an image shows a thumbnail at once and uploads it to the Flow', async () => {
    const hold = deferred<ReturnType<typeof meta>>()
    net.upload.mockReturnValueOnce(hold.promise)
    const w = mountComposer()
    const picked = file()
    const event = paste(w, [picked])
    await flushPromises()
    expect(event.defaultPrevented).toBe(true)
    expect(thumbs(w)).toHaveLength(1)
    expect(thumbs(w)[0].attributes('data-status')).toBe('uploading')
    expect(w.find('[data-test="conv-image-uploading"]').text()).toBe('업로드 중')
    expect(net.upload).toHaveBeenCalledTimes(1)
    expect(net.upload).toHaveBeenCalledWith(FLOW, picked, 'shot.png')
    hold.resolve(meta(1))
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-status')).toBe('ready')
    expect(w.get('img.conv-img-pic').attributes('src')).toBe('blob:thumb')
  })

  it('leaves a paste that also carries text to the textarea', async () => {
    const w = mountComposer()
    const event = paste(w, [file()], '복사한 표')
    await flushPromises()
    expect(event.defaultPrevented).toBe(false)
    expect(net.upload).not.toHaveBeenCalled()
    expect(thumbs(w)).toHaveLength(0)
  })

  it('takes a dropped image file; a dropped non-image says so and uploads nothing', async () => {
    const w = mountComposer()
    expect(drop(w, [file('a.webp', 'image/webp')]).defaultPrevented).toBe(true)
    await flushPromises()
    expect(net.upload).toHaveBeenCalledTimes(1)
    drop(w, [file('notes.pdf', 'application/pdf')])
    await flushPromises()
    expect(net.upload).toHaveBeenCalledTimes(1)
    expect(w.get('[data-test="conv-image-notice"]').attributes('data-notice')).toBe('not_image')
    expect(w.get('[data-test="conv-image-notice"]').text()).toContain('이미지 파일만')
  })

  it('refuses the 6th image with the 5-per-message notice', async () => {
    const w = mountComposer()
    for (let i = 0; i < 5; i++) paste(w, [file(`s${i}.png`)])
    await flushPromises()
    expect(thumbs(w)).toHaveLength(5)
    paste(w, [file('sixth.png')])
    await flushPromises()
    expect(thumbs(w)).toHaveLength(5)
    expect(net.upload).toHaveBeenCalledTimes(5)
    expect(w.get('[data-test="conv-image-notice"]').attributes('data-notice')).toBe('limit')
    expect(w.get('[data-test="conv-image-notice"]').text()).toContain('메시지당 5장')
  })

  it('takes only what fits when several are pasted at once', async () => {
    const w = mountComposer()
    paste(w, Array.from({ length: 7 }, (_, i) => file(`m${i}.png`)))
    await flushPromises()
    expect(thumbs(w)).toHaveLength(5)
    expect(net.upload).toHaveBeenCalledTimes(5)
    expect(w.find('[data-test="conv-image-notice"]').attributes('data-notice')).toBe('limit')
  })

  it('does not attach while the composer cannot take input (sending)', async () => {
    const w = mountComposer({ sending: true })
    expect(paste(w, [file()]).defaultPrevented).toBe(false)
    expect(drop(w, [file()]).defaultPrevented).toBe(false)
    await flushPromises()
    expect(net.upload).not.toHaveBeenCalled()
  })
})

describe('shrinking', () => {
  it('uploads the original when the long side fits, without calling the shrink', async () => {
    const w = mountComposer()
    const picked = file('fits.png')
    paste(w, [picked])
    await flushPromises()
    expect(imageDeps.resize).not.toHaveBeenCalled()
    expect(net.upload.mock.calls[0][1]).toBe(picked)
    expect(thumbs(w)[0].attributes('data-status')).toBe('ready')
  })

  it('shrinks a larger image first and uploads the shrunk copy (JPEG becomes WebP)', async () => {
    imageDeps.measure = vi.fn().mockResolvedValue({ width: 4000, height: 2000 })
    const shrunk = new Blob(['small'], { type: 'image/webp' })
    imageDeps.resize = vi.fn().mockResolvedValue(shrunk)
    const w = mountComposer()
    paste(w, [file('big.jpg', 'image/jpeg')])
    await flushPromises()
    expect(imageDeps.resize).toHaveBeenCalledTimes(1)
    expect(vi.mocked(imageDeps.resize).mock.calls[0][1]).toEqual({ width: 1568, height: 784 })
    expect(net.upload).toHaveBeenCalledWith(FLOW, shrunk, 'big.webp')
  })

  it('does not upload an image that is still over 5 MiB after shrinking; it can be taken out', async () => {
    imageDeps.measure = vi.fn().mockResolvedValue({ width: 4000, height: 2000 })
    imageDeps.resize = vi.fn().mockResolvedValue({ size: 6 * 1024 * 1024, type: 'image/png' } as Blob)
    const w = mountComposer()
    paste(w, [file('huge.png')])
    await flushPromises()
    expect(net.upload).not.toHaveBeenCalled()
    expect(thumbs(w)[0].attributes('data-status')).toBe('failed')
    expect(w.get('[data-test="conv-image-error"]').text()).toContain('5MB를 넘습니다')
  })

  it('an image the browser cannot read fails on its thumbnail', async () => {
    imageDeps.measure = vi.fn().mockRejectedValue(new Error('decode_failed'))
    const w = mountComposer()
    paste(w, [file('broken.png')])
    await flushPromises()
    expect(net.upload).not.toHaveBeenCalled()
    expect(w.get('[data-test="conv-image-error"]').text()).toContain('읽을 수 없습니다')
  })
})

describe('upload state and the send', () => {
  it('holds the send while an image uploads, then sends with its id', async () => {
    const hold = deferred<ReturnType<typeof meta>>()
    net.upload.mockReturnValueOnce(hold.promise)
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, '이 화면 봐줘')
    paste(w, [file()])
    await flushPromises()
    expect(send(w).attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="conv-image-hint"]').attributes('data-hint')).toBe('uploading')
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
    hold.resolve(meta(1))
    await flushPromises()
    expect(send(w).attributes('disabled')).toBeUndefined()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).toHaveBeenCalledTimes(1)
    const [text, snapshot] = onSend.mock.calls[0] as unknown as [string, { images?: string[]; owner: DraftOwner }]
    expect(text).toBe('이 화면 봐줘')
    expect(snapshot.images).toEqual([meta(1).imageId])
    expect(snapshot.owner.chatId).toBe(CHAT)
  })

  it('sends the ready images in the order they were attached', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, 'two')
    paste(w, [file('a.png')])
    paste(w, [file('b.png')])
    await flushPromises()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect((onSend.mock.calls[0] as unknown as [string, { images?: string[] }])[1].images).toEqual([meta(1).imageId, meta(2).imageId])
  })

  it('a text-only message carries no images field', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, 'plain')
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend.mock.calls[0][1]).not.toHaveProperty('images')
  })

  it('the thumbnails leave once the server accepted the message, and nothing is deleted', async () => {
    const w = mountComposer()
    await type(w, 'go')
    paste(w, [file()])
    await flushPromises()
    expect(loadDraftImages({ userId: draftUserId(), projectId: 'p', groupId: FLOW, intentId: '', chatId: CHAT })).toEqual([meta(1).imageId])
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(thumbs(w)).toHaveLength(0)
    expect(net.remove).not.toHaveBeenCalled()
    expect(loadDraftImages({ userId: draftUserId(), projectId: 'p', groupId: FLOW, intentId: '', chatId: CHAT })).toEqual([])
  })

  it('keeps the thumbnails when the send fails', async () => {
    const w = mountComposer({ onSend: vi.fn(async () => false) })
    await type(w, 'go')
    paste(w, [file()])
    await flushPromises()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(thumbs(w)).toHaveLength(1)
  })

  it('images without text hold the send (the server refuses an empty message)', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    paste(w, [file()])
    await flushPromises()
    expect(send(w).attributes('disabled')).toBeDefined()
    expect(w.find('[data-test="conv-image-hint"]').exists()).toBe(false)
    await w.get('[data-test="conv-composer"]').trigger('submit')
    expect(onSend).not.toHaveBeenCalled()
  })

  it('a failed upload shows the reason and 다시 시도; the send waits for the retry or the ✕', async () => {
    net.upload.mockRejectedValueOnce(new ConversationRequestError('', 413, 'message_image_too_large'))
    const w = mountComposer()
    await type(w, 'x')
    paste(w, [file()])
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-status')).toBe('failed')
    expect(w.get('[data-test="conv-image-error"]').text()).toContain('5MB를 넘습니다')
    expect(send(w).attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="conv-image-hint"]').attributes('data-hint')).toBe('unusable')
    await w.get('[data-test="conv-image-retry"]').trigger('click')
    await flushPromises()
    expect(net.upload).toHaveBeenCalledTimes(2)
    expect(thumbs(w)[0].attributes('data-status')).toBe('ready')
    expect(send(w).attributes('disabled')).toBeUndefined()
  })

  it('names the other refusals too (type, size of side, permission)', async () => {
    net.upload.mockRejectedValueOnce(new ConversationRequestError('', 415, 'message_image_unsupported_type'))
    const w = mountComposer()
    paste(w, [file()])
    await flushPromises()
    expect(w.get('[data-test="conv-image-error"]').text()).toContain('PNG·JPEG·GIF·WebP')
  })
})

describe('taking an image out', () => {
  it('✕ on an uploaded temp image deletes it on the server', async () => {
    const w = mountComposer()
    paste(w, [file()])
    await flushPromises()
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    await flushPromises()
    expect(thumbs(w)).toHaveLength(0)
    expect(net.remove).toHaveBeenCalledWith(FLOW, meta(1).imageId)
  })

  it('✕ while it uploads removes the thumbnail and deletes the image once it is up', async () => {
    const hold = deferred<ReturnType<typeof meta>>()
    net.upload.mockReturnValueOnce(hold.promise)
    const w = mountComposer()
    paste(w, [file()])
    await flushPromises()
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(thumbs(w)).toHaveLength(0)
    expect(net.remove).not.toHaveBeenCalled()
    hold.resolve(meta(1))
    await flushPromises()
    expect(net.remove).toHaveBeenCalledWith(FLOW, meta(1).imageId)
    expect(thumbs(w)).toHaveLength(0)
  })

  it('✕ on a failed image removes it without a server call', async () => {
    net.upload.mockRejectedValueOnce(new ConversationRequestError('', 500, null))
    const w = mountComposer()
    paste(w, [file()])
    await flushPromises()
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(thumbs(w)).toHaveLength(0)
    expect(net.remove).not.toHaveBeenCalled()
  })
})

describe('the AI that cannot take images', () => {
  const providers = [
    { id: 'claude', name: 'Claude', acceptsImages: true },
    { id: 'codex', name: 'Codex', acceptsImages: false },
    { id: 'old', name: 'Old' },
  ]

  it('shows no unsupported-AI notice and allows sending when accepts_images=false', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ providers, selectedProviderId: 'codex', onSend })
    await type(w, 'look')
    paste(w, [file()])
    await flushPromises()
    expect(w.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
    expect(send(w).attributes('disabled')).toBeUndefined()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it('keeps the unsupported-AI notice absent across AI changes and image removal', async () => {
    const w = mountComposer({ providers, selectedProviderId: 'claude' })
    paste(w, [file()])
    await flushPromises()
    expect(w.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
    await w.setProps({ selectedProviderId: 'old' })
    expect(w.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
    await w.setProps({ selectedProviderId: 'codex' })
    expect(w.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(w.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
    const empty = mountComposer({ providers, selectedProviderId: 'codex' })
    expect(empty.find('[data-test="conv-image-unsupported-ai"]').exists()).toBe(false)
  })
})

describe('a new chat (chatId null)', () => {
  it('uploads by flow id and sends the ids with the first message', async () => {
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ chatId: null, draftIntentId: 'intent-1', onSend })
    await type(w, '첫 메시지')
    paste(w, [file()])
    await flushPromises()
    expect(net.upload).toHaveBeenCalledWith(FLOW, expect.anything(), 'shot.png')
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    const snapshot = onSend.mock.calls[0][1] as unknown as { images?: string[]; owner: DraftOwner }
    expect(snapshot.images).toEqual([meta(1).imageId])
    expect(snapshot.owner).toMatchObject({ chatId: null, groupId: FLOW, intentId: 'intent-1' })
  })

  it('keeps the images with that draft when another chat is shown and back', async () => {
    const w = mountComposer({ chatId: null, draftIntentId: 'intent-1' })
    paste(w, [file()])
    await flushPromises()
    await w.setProps({ chatId: CHAT })
    expect(thumbs(w)).toHaveLength(0)
    await w.setProps({ chatId: null })
    expect(thumbs(w)).toHaveLength(1)
  })
})

describe('draft images', () => {
  const owner = (): DraftOwner => ({ userId: draftUserId(), projectId: 'p', groupId: FLOW, intentId: '', chatId: CHAT })

  it('keeps only the image ids of the draft', async () => {
    const w = mountComposer()
    paste(w, [file()])
    await flushPromises()
    expect(loadDraftImages(owner())).toEqual([meta(1).imageId])
    const raw = localStorage.getItem(`${draftKey(owner())}.images`) ?? ''
    expect(raw).toBe(JSON.stringify([meta(1).imageId]))
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(loadDraftImages(owner())).toEqual([])
  })

  it('shows a draft image again from its id', async () => {
    saveDraftImages(owner(), [meta(7).imageId])
    const w = mountComposer()
    await flushPromises()
    expect(net.blob).toHaveBeenCalledWith(`/api/v1/flows/${FLOW}/message-images/${meta(7).imageId}/content`)
    expect(thumbs(w)).toHaveLength(1)
    expect(thumbs(w)[0].attributes('data-status')).toBe('ready')
  })

  it('holds the send (button and Enter) while a restored image is being checked, and sends it once the check passed', async () => {
    const hold = deferred<Blob>()
    net.blob.mockReturnValueOnce(hold.promise)
    saveDraftImages(owner(), [meta(7).imageId])
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, '복원 확인')
    await flushPromises()
    // the content answer is still pending: the image is not known to exist yet
    expect(thumbs(w)[0].attributes('data-status')).toBe('checking')
    expect(w.get('[data-test="conv-image-checking"]').text()).toBe('확인 중')
    expect(w.get('[data-test="conv-image-hint"]').attributes('data-hint')).toBe('checking')
    expect(send(w).attributes('disabled')).toBeDefined()
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: 'Enter' })
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).not.toHaveBeenCalled()
    // still in the draft while it is checked
    expect(loadDraftImages(owner())).toEqual([meta(7).imageId])
    hold.resolve(new Blob(['x'], { type: 'image/png' }))
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-status')).toBe('ready')
    expect(send(w).attributes('disabled')).toBeUndefined()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).toHaveBeenCalledTimes(1)
    expect((onSend.mock.calls[0][1] as unknown as { images?: string[] }).images).toEqual([meta(7).imageId])
  })

  it('never sends a restored image whose check ends in 404: the pending check blocked it, the 404 keeps it blocked', async () => {
    const hold = deferred<Blob>()
    net.blob.mockReturnValueOnce(hold.promise)
    saveDraftImages(owner(), [meta(7).imageId])
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, 'x')
    await flushPromises()
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: 'Enter' })
    expect(onSend).not.toHaveBeenCalled()
    hold.reject(new ConversationRequestError('', 404, 'message_image_not_found'))
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-status')).toBe('missing')
    expect(send(w).attributes('disabled')).toBeDefined()
    await w.get('[data-test="conv-input"]').trigger('keydown', { key: 'Enter' })
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect(onSend).not.toHaveBeenCalled()
    expect(loadDraftImages(owner())).toEqual([])
  })

  it('a 403 answer also marks it gone; an unanswered check stays held, keeps the id in the draft and can be retried', async () => {
    net.blob.mockRejectedValueOnce(new ConversationRequestError('', 403, 'forbidden'))
    saveDraftImages(owner(), [meta(7).imageId, meta(8).imageId])
    net.blob.mockRejectedValueOnce(new ConversationRequestError('', 503, null))
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, 'x')
    await flushPromises()
    expect(thumbs(w).map((t) => t.attributes('data-status'))).toEqual(['missing', 'failed'])
    expect(w.get('[data-test="conv-image-error"]').text()).toContain('확인하지 못했습니다')
    expect(send(w).attributes('disabled')).toBeDefined()
    // the id of the unconfirmed image is not lost from the draft
    expect(loadDraftImages(owner())).toEqual([meta(8).imageId])
    await w.get('[data-test="conv-image-retry"]').trigger('click')
    await flushPromises()
    expect(thumbs(w)[1].attributes('data-status')).toBe('ready')
    // the gone one still holds the send until it is taken out
    expect(send(w).attributes('disabled')).toBeDefined()
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(send(w).attributes('disabled')).toBeUndefined()
    await w.get('[data-test="conv-composer"]').trigger('submit')
    await flushPromises()
    expect((onSend.mock.calls[0][1] as unknown as { images?: string[] }).images).toEqual([meta(8).imageId])
  })

  it('✕ on an image that is still being checked takes it out and deletes the temp image', async () => {
    const hold = deferred<Blob>()
    net.blob.mockReturnValueOnce(hold.promise)
    saveDraftImages(owner(), [meta(7).imageId])
    const w = mountComposer()
    await flushPromises()
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(thumbs(w)).toHaveLength(0)
    expect(net.remove).toHaveBeenCalledWith(FLOW, meta(7).imageId)
    expect(loadDraftImages(owner())).toEqual([])
    hold.resolve(new Blob(['x'], { type: 'image/png' }))
    await flushPromises()
    expect(thumbs(w)).toHaveLength(0)
  })

  it('shows a draft image the server already cleaned up (24 hours) as gone and holds the send until it is removed', async () => {
    net.blob.mockRejectedValueOnce(new ConversationRequestError('', 404, 'message_image_not_found'))
    saveDraftImages(owner(), [meta(7).imageId])
    const onSend = vi.fn(async () => true)
    const w = mountComposer({ onSend })
    await type(w, 'x')
    await flushPromises()
    expect(thumbs(w)[0].attributes('data-status')).toBe('missing')
    expect(w.get('[data-test="conv-image-missing"]').text()).toContain('사라진 이미지')
    expect(send(w).attributes('disabled')).toBeDefined()
    expect(loadDraftImages(owner())).toEqual([])
    await w.get('[data-test="conv-image-remove"]').trigger('click')
    expect(send(w).attributes('disabled')).toBeUndefined()
    expect(net.remove).not.toHaveBeenCalled()
  })
})
