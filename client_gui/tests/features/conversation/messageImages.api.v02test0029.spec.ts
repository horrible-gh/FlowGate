import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02test.0029 T0005 — the screen's image calls: upload by flow id, delete, authenticated content, the
// `images` field of a turn, `accepts_images` of a provider, and the shrink-before-upload rule.
const net = vi.hoisted(() => ({
  postFormRequest: vi.fn(),
  deleteRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  ...net,
  patchRequest: vi.fn(),
  extractApiErrorMessage: (_e: unknown, fallback: string) => fallback,
}))

import {
  ConversationRequestError,
  deleteMessageImage,
  fetchMessageImageBlob,
  fetchProviders,
  messageImageRequestPath,
  messageImageUrl,
  postTurn,
  toTurn,
  uploadMessageImage,
} from '@/features/conversation/api'
import { fittedSize, imageDeps, prepareImage } from '@/features/conversation/imageResize'
import { MAX_IMAGE_DIMENSION, imageErrorKey } from '@/features/conversation/messageImages'

const CHAT = 'p.m.0001.0002-CH'
const IMG = { image_id: `img_${'a'.repeat(32)}`, media_type: 'image/png', size: 12, width: 30, height: 20, url: `/flowgate/api/v1/flows/p.m.0001/message-images/img_${'a'.repeat(32)}/content` }

beforeEach(() => {
  for (const fn of Object.values(net)) fn.mockReset()
})

describe('message image requests', () => {
  it('uploads one multipart `file` to the flow (group id), not to a chat', async () => {
    net.postFormRequest.mockResolvedValue({ data: { ok: true, image: IMG } })
    const blob = new Blob([new Uint8Array([1, 2, 3])], { type: 'image/png' })
    const image = await uploadMessageImage('p.m.0001', blob, 'shot.png')
    expect(net.postFormRequest).toHaveBeenCalledTimes(1)
    const [path, form] = net.postFormRequest.mock.calls[0]
    expect(path).toBe('/api/v1/flows/p.m.0001/message-images')
    expect((form as FormData).get('file')).toBeInstanceOf(Blob)
    expect(((form as FormData).get('file') as File).name).toBe('shot.png')
    expect(image).toEqual({ imageId: IMG.image_id, mediaType: 'image/png', size: 12, width: 30, height: 20, url: IMG.url })
  })

  it('does not use the document attachment API', async () => {
    net.postFormRequest.mockResolvedValue({ data: { image: IMG } })
    await uploadMessageImage('p.m.0001', new Blob(['x']), 'a.png')
    expect(String(net.postFormRequest.mock.calls[0][0])).not.toContain('attachments')
  })

  it('maps a refusal to the code and status the composer reads', async () => {
    net.postFormRequest.mockRejectedValue({ response: { status: 413, data: { ok: false, error: { code: 'message_image_too_large' } } } })
    const failure = await uploadMessageImage('p.m.0001', new Blob(['x']), 'a.png').catch((e) => e)
    expect(failure).toBeInstanceOf(ConversationRequestError)
    expect(failure).toMatchObject({ status: 413, code: 'message_image_too_large' })
    expect(imageErrorKey(failure)).toBe('too_large')
  })

  it.each([
    [415, 'message_image_unsupported_type', 'unsupported'],
    [422, 'message_image_too_large_dimension', 'dimension'],
    [409, 'message_image_flow_disposed', 'disposed'],
    [403, 'message_image_forbidden', 'forbidden'],
    [null, null, 'failed'],
    [500, null, 'failed'],
  ])('reads %s %s as %s', (status, code, key) => {
    expect(imageErrorKey(new ConversationRequestError('', status as number | null, code as string | null))).toBe(key)
  })

  it('deletes a temp image by flow and image id', async () => {
    net.deleteRequest.mockResolvedValue({ data: { ok: true } })
    await deleteMessageImage('p.m.0001', IMG.image_id)
    expect(net.deleteRequest).toHaveBeenCalledWith(`/api/v1/flows/p.m.0001/message-images/${IMG.image_id}`)
  })

  it('reads the bytes through the session request, from the part of the url from /api/v1 on, with no token in it', async () => {
    const blob = new Blob(['x'], { type: 'image/png' })
    net.downloadBlobRequest.mockResolvedValue({ data: blob })
    expect(await fetchMessageImageBlob(IMG.url)).toBe(blob)
    const path = net.downloadBlobRequest.mock.calls[0][0] as string
    expect(path).toBe(`/api/v1/flows/p.m.0001/message-images/${IMG.image_id}/content`)
    expect(path).not.toMatch(/token|access|auth/i)
    expect(messageImageRequestPath(IMG.url)).toBe(path)
    expect(messageImageUrl('p.m.0001', IMG.image_id)).toBe(path)
  })
})

describe('turn wire', () => {
  it('lists the images of a turn (metadata only) and leaves the field off a text turn', () => {
    expect(toTurn({ seq: 3, speaker: 'user', body: 'look', images: [IMG] }).images).toEqual([
      { imageId: IMG.image_id, mediaType: 'image/png', size: 12, width: 30, height: 20, url: IMG.url },
    ])
    expect(toTurn({ seq: 4, speaker: 'user', body: 'text', images: [] })).not.toHaveProperty('images')
    expect(toTurn({ seq: 5, speaker: 'user', body: 'old server' })).not.toHaveProperty('images')
    // An entry without an id or an address cannot be shown.
    expect(toTurn({ seq: 6, speaker: 'user', body: 'x', images: [{ media_type: 'image/png' }, IMG] }).images).toHaveLength(1)
  })

  it('posts `images: [image_id…]` with the turn, never bytes, and nothing when there are none', async () => {
    net.postRequest.mockResolvedValue({ data: { turn: { seq: 1, speaker: 'user', body: 'look' }, head_seq: 1 } })
    await postTurn(CHAT, { body: 'look', idempotencyKey: 'sess_aaaaaaaa', basedOnSeq: 0, images: [IMG.image_id] })
    expect(net.postRequest.mock.calls[0][0]).toBe(`/api/v1/documents/${CHAT}/conversation/turn`)
    expect(net.postRequest.mock.calls[0][1]).toEqual({ body: 'look', idempotency_key: 'sess_aaaaaaaa', based_on_seq: 0, images: [IMG.image_id] })
    await postTurn(CHAT, { body: 'plain', idempotencyKey: 'sess_bbbbbbbb', basedOnSeq: 1 })
    expect(net.postRequest.mock.calls[1][1]).not.toHaveProperty('images')
  })

  it('reads `accepts_images` of a provider; absent stays unknown', async () => {
    net.getRequest.mockResolvedValue({ data: { providers: [
      { id: 'a', name: 'Claude', accepts_images: true },
      { id: 'b', name: 'Codex', accepts_images: false },
      { id: 'c', name: 'Old' },
    ], default_provider_id: 'a' } })
    const list = await fetchProviders('p')
    expect(list.providers).toEqual([
      { id: 'a', name: 'Claude', acceptsImages: true },
      { id: 'b', name: 'Codex', acceptsImages: false },
      { id: 'c', name: 'Old' },
    ])
  })
})

describe('shrinking before upload (long side over 1568px)', () => {
  const original = { measure: imageDeps.measure, resize: imageDeps.resize }
  const png = (name = 'a.png') => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/png' })
  const jpg = (name = 'a.jpg') => new File([new Uint8Array([1, 2, 3])], name, { type: 'image/jpeg' })

  beforeEach(() => {
    imageDeps.measure = vi.fn()
    imageDeps.resize = vi.fn()
  })

  it('keeps the original bytes while the long side fits (the limit itself fits)', async () => {
    vi.mocked(imageDeps.measure).mockResolvedValue({ width: MAX_IMAGE_DIMENSION, height: 900 })
    const file = png()
    const prepared = await prepareImage(file)
    expect(imageDeps.resize).not.toHaveBeenCalled()
    expect(prepared).toEqual({ blob: file, filename: 'a.png', resized: false })
  })

  it('scales a larger image to fit and keeps PNG as PNG', async () => {
    vi.mocked(imageDeps.measure).mockResolvedValue({ width: 3136, height: 1000 })
    const out = new Blob(['p'], { type: 'image/png' })
    vi.mocked(imageDeps.resize).mockResolvedValue(out)
    const prepared = await prepareImage(png('shot.png'))
    expect(imageDeps.resize).toHaveBeenCalledTimes(1)
    expect(vi.mocked(imageDeps.resize).mock.calls[0][1]).toEqual({ width: 1568, height: 500 })
    expect(prepared).toEqual({ blob: out, filename: 'shot.png', resized: true })
  })

  it('exports every other format as WebP', async () => {
    vi.mocked(imageDeps.measure).mockResolvedValue({ width: 1000, height: 4000 })
    const out = new Blob(['w'], { type: 'image/webp' })
    vi.mocked(imageDeps.resize).mockResolvedValue(out)
    const prepared = await prepareImage(jpg('photo.jpeg'))
    expect(vi.mocked(imageDeps.resize).mock.calls[0][1]).toEqual({ width: 392, height: 1568 })
    expect(prepared.filename).toBe('photo.webp')
    expect(prepared.blob.type).toBe('image/webp')
  })

  it('fitted size never goes over the limit or below one pixel', () => {
    expect(fittedSize({ width: 1568, height: 1568 })).toEqual({ width: 1568, height: 1568 })
    expect(fittedSize({ width: 100000, height: 10 })).toEqual({ width: 1568, height: 1 })
  })

  it('an image the browser cannot decode is rejected, not uploaded as it is', async () => {
    vi.mocked(imageDeps.measure).mockRejectedValue(new Error('decode_failed'))
    await expect(prepareImage(png())).rejects.toThrow('decode_failed')
  })

  afterEach(() => {
    imageDeps.measure = original.measure
    imageDeps.resize = original.resize
  })
})
