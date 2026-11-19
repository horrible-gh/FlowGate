// flowgate.v02test.0029 T0005 — the images attached to the composer's message, per draft owner (a chat, or a new-chat
// draft): picked → shrunk when too large → uploaded at once as temp images of the Flow (by flow id, so a message
// before its chat exists can carry them) → sent as `images: [image_id…]` with the text.
//
// Shown to the user as thumbnails above the input: uploading (progress), checking (restored from the draft, being
// confirmed), failed (error + retry), ready, or gone (the server already cleaned up a draft image after 24 hours).
// Only `ready` images go out; anything else holds the send.
import { computed, ref } from 'vue'
import { draftKey, loadDraftImages, saveDraftImages, type DraftOwner } from './drafts'
import { deleteMessageImage, fetchMessageImageBlob, messageImageUrl, uploadMessageImage } from './api'
import { prepareImage } from './imageResize'
import { ACCEPTED_IMAGE_TYPES, MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE, imageErrorKey, type ImageErrorKey } from './messageImages'

/**
 * `checking`: an image id read back from the draft, not yet confirmed to still exist on the server (it is cleaned up
 * after 24 hours). It holds the send like `uploading` and becomes `ready` only once its content was fetched.
 */
export type ComposerImageStatus = 'uploading' | 'checking' | 'ready' | 'failed' | 'missing'
export type ImageNoticeKey = ImageErrorKey | 'limit' | 'not_image'

export interface ComposerImage {
  localId: string
  name: string
  /** A blob address for the thumbnail (null until known / when the browser cannot make one). */
  previewUrl: string | null
  status: ComposerImageStatus
  /** The server's image id once uploaded (or restored from the draft). */
  imageId: string | null
  /** `check_failed`: a restored image that could not be confirmed (no answer from the server). */
  error: ImageErrorKey | 'check_failed' | null
}

interface Source { file: File; prepared: { blob: Blob; filename: string } | null }

let counter = 0
const newLocalId = () => `img-${Date.now().toString(36)}-${(counter += 1)}`

function blobUrl(blob: Blob): string | null {
  return typeof URL !== 'undefined' && typeof URL.createObjectURL === 'function' ? URL.createObjectURL(blob) : null
}

function revoke(url: string | null): void {
  if (url && typeof URL !== 'undefined' && typeof URL.revokeObjectURL === 'function') URL.revokeObjectURL(url)
}

export function isImageFile(file: { type: string }): boolean {
  return file.type.startsWith('image/')
}

export function createComposerImages(visibleOwner: () => DraftOwner | null) {
  const lists = ref<Record<string, ComposerImage[]>>({})
  const owners = new Map<string, DraftOwner>()
  const sources = new Map<string, Source>()
  const removed = new Set<string>()
  const notice = ref<ImageNoticeKey | null>(null)
  let disposed = false

  const keyOf = (owner: DraftOwner) => draftKey(owner)
  const listOf = (key: string): ComposerImage[] => lists.value[key] ?? []

  function persist(key: string): void {
    const owner = owners.get(key)
    // Every image the server knows by id stays in the draft — also one that is still being checked or whose check
    // failed for a passing reason (a sibling's update must not drop it). It leaves when found gone or taken out.
    if (owner) saveDraftImages(owner, listOf(key).flatMap((image) => image.status !== 'missing' && image.imageId ? [image.imageId] : []))
  }

  function setList(key: string, list: ComposerImage[]): void {
    lists.value = { ...lists.value, [key]: list }
  }

  function patch(key: string, localId: string, change: Partial<ComposerImage>): void {
    if (!listOf(key).some((image) => image.localId === localId)) return
    setList(key, listOf(key).map((image) => image.localId === localId ? { ...image, ...change } : image))
    persist(key)
  }

  /** The images of the owner on screen. */
  const images = computed<ComposerImage[]>(() => {
    const owner = visibleOwner()
    return owner ? listOf(keyOf(owner)) : []
  })
  const uploading = computed(() => images.value.some((image) => image.status === 'uploading'))
  const checking = computed(() => images.value.some((image) => image.status === 'checking'))
  /** Anything that is not a ready image holds the send (it would be left out silently otherwise). */
  const blocked = computed(() => images.value.some((image) => image.status !== 'ready'))
  const readyIds = computed(() => images.value.flatMap((image) => image.status === 'ready' && image.imageId ? [image.imageId] : []))

  /** Reads the owner's image ids from its draft the first time that owner is shown. */
  function restore(owner: DraftOwner | null): void {
    if (!owner || disposed) return
    const key = keyOf(owner)
    owners.set(key, owner)
    if (lists.value[key]) return
    const ids = loadDraftImages(owner)
    if (!ids.length) return
    const entries: ComposerImage[] = ids.map((imageId) => ({ localId: newLocalId(), name: imageId, previewUrl: null, status: 'checking', imageId, error: null }))
    setList(key, entries)
    for (const entry of entries) void loadPreview(key, owner.groupId, entry)
  }

  async function loadPreview(key: string, flowId: string, entry: ComposerImage): Promise<void> {
    if (!flowId || !entry.imageId) return
    try {
      const url = blobUrl(await fetchMessageImageBlob(messageImageUrl(flowId, entry.imageId)))
      if (disposed || !listOf(key).some((image) => image.localId === entry.localId)) revoke(url)
      // The content came back: the image still exists, so it may go out now.
      else patch(key, entry.localId, { previewUrl: url, status: 'ready' })
    } catch (error) {
      if (disposed) return
      const status = (error as { status?: number | null } | null)?.status
      // Cleaned up by the server (24 hours) or no longer this user's temp image: shown as gone, never sent.
      if (status === 404 || status === 403) patch(key, entry.localId, { status: 'missing', error: null })
      // Not confirmed either way (network, 5xx): it stays held and the user can take it out, or retry the check.
      else patch(key, entry.localId, { status: 'failed', error: 'check_failed' })
    }
  }

  /** Picks files from a paste / drop / file input; returns how many were taken. */
  function add(files: readonly File[]): number {
    const owner = visibleOwner()
    if (!owner || disposed) return 0
    const key = keyOf(owner)
    owners.set(key, owner)
    notice.value = null
    let taken = 0
    for (const file of files) {
      if (!isImageFile(file) || !ACCEPTED_IMAGE_TYPES.includes(file.type)) {
        notice.value = isImageFile(file) ? 'unsupported' : 'not_image'
        continue
      }
      if (listOf(key).length >= MAX_IMAGES_PER_MESSAGE) {
        notice.value = 'limit'
        break
      }
      const entry: ComposerImage = { localId: newLocalId(), name: file.name || 'image', previewUrl: blobUrl(file), status: 'uploading', imageId: null, error: null }
      sources.set(entry.localId, { file, prepared: null })
      setList(key, [...listOf(key), entry])
      taken += 1
      void upload(key, owner.groupId, entry.localId)
    }
    return taken
  }

  async function upload(key: string, flowId: string, localId: string): Promise<void> {
    const source = sources.get(localId)
    if (!source) return
    try {
      if (!flowId) throw new Error('no_flow')
      if (!source.prepared) {
        let prepared: Awaited<ReturnType<typeof prepareImage>>
        try {
          prepared = await prepareImage(source.file)
        } catch {
          // The browser could not decode (or scale) the file.
          if (!removed.has(localId) && !disposed) patch(key, localId, { status: 'failed', error: 'unreadable' })
          return
        }
        source.prepared = { blob: prepared.blob, filename: prepared.filename }
        if (prepared.resized) {
          // The thumbnail shows what is uploaded.
          const old = listOf(key).find((image) => image.localId === localId)?.previewUrl ?? null
          patch(key, localId, { previewUrl: blobUrl(prepared.blob) })
          revoke(old)
        }
      }
      if (source.prepared.blob.size > MAX_IMAGE_BYTES) {
        patch(key, localId, { status: 'failed', error: 'too_large' })
        return
      }
      const image = await uploadMessageImage(flowId, source.prepared.blob, source.prepared.filename)
      if (removed.has(localId) || disposed) {
        // Removed while uploading: the temp image is not wanted.
        removed.delete(localId)
        sources.delete(localId)
        void deleteMessageImage(flowId, image.imageId).catch(() => undefined)
        return
      }
      patch(key, localId, { status: 'ready', imageId: image.imageId, error: null })
      sources.delete(localId)
    } catch (error) {
      if (removed.has(localId) || disposed) {
        removed.delete(localId)
        sources.delete(localId)
        return
      }
      patch(key, localId, { status: 'failed', error: imageErrorKey(error) })
    }
  }

  function retry(localId: string): void {
    const owner = visibleOwner()
    if (!owner) return
    const key = keyOf(owner)
    const current = listOf(key).find((image) => image.localId === localId)
    if (!current || current.status !== 'failed') return
    notice.value = null
    if (!sources.has(localId)) {
      // A restored image whose check did not get an answer: ask again.
      if (current.imageId) {
        patch(key, localId, { status: 'checking', error: null })
        void loadPreview(key, owner.groupId, current)
      }
      return
    }
    patch(key, localId, { status: 'uploading', error: null })
    void upload(key, owner.groupId, localId)
  }

  function remove(localId: string): void {
    const owner = visibleOwner()
    if (!owner) return
    const key = keyOf(owner)
    const current = listOf(key).find((image) => image.localId === localId)
    if (!current) return
    notice.value = null
    setList(key, listOf(key).filter((image) => image.localId !== localId))
    revoke(current.previewUrl)
    if (current.status === 'uploading') removed.add(localId)
    else sources.delete(localId)
    // An uploaded temp image is deleted on the server (also one restored from the draft that is still being checked);
    // a failed delete only leaves it to the 24 hour clean-up.
    if (current.status !== 'missing' && current.imageId && owner.groupId) void deleteMessageImage(owner.groupId, current.imageId).catch(() => undefined)
    persist(key)
  }

  /** A first message's draft becomes the chat it created: the same images, shown under the chat's key. */
  function transfer(from: DraftOwner, to: DraftOwner): void {
    const fromKey = keyOf(from)
    const toKey = keyOf(to)
    owners.set(toKey, to)
    if (fromKey === toKey || !lists.value[fromKey] || lists.value[toKey]) return
    setList(toKey, [...listOf(fromKey)])
    persist(toKey)
  }

  /** The server accepted a message with these images: they are linked now, so they leave the composer (not deleted). */
  function sent(targets: readonly DraftOwner[], ids: readonly string[]): void {
    for (const owner of targets) {
      const key = keyOf(owner)
      if (!lists.value[key]) continue
      const keep: ComposerImage[] = []
      for (const image of listOf(key)) {
        if (image.imageId && ids.includes(image.imageId)) revoke(image.previewUrl)
        else keep.push(image)
      }
      setList(key, keep)
      owners.set(key, owner)
      persist(key)
    }
  }

  function dismissNotice(): void {
    notice.value = null
  }

  function dispose(): void {
    disposed = true
    for (const list of Object.values(lists.value)) for (const image of list) revoke(image.previewUrl)
  }

  return { images, uploading, checking, blocked, readyIds, notice, restore, add, retry, remove, transfer, sent, dismissNotice, dispose }
}

export type ComposerImages = ReturnType<typeof createComposerImages>
