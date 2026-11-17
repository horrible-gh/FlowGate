// Upload work belongs to a Flow entry, never to its window.
import { uploadFile } from './api'

export type CollisionChoice = 'cancel' | 'keep_both' | 'overwrite'
export interface UploadCollision { id: string; file: File; key: string; content_sha256: string }
export interface UploadState {
  currentFile: File | null
  pendingCount: number
  collision: UploadCollision | null
  cancelledUploads: string[]
  dirty: boolean
  uploadRevision: number
}
interface UploadContext {
  flowId: string
  state: UploadState
  stale(): boolean
  readOnly(): boolean
  hasView(): boolean
  ready(): Promise<void>
  changed(): void
  completed(): void
  failed(error: unknown): void
}
let requestSequence = 0
export function createUploadWorker(ctx: UploadContext) {
  const queue: { file: File; epoch: number }[] = []
  let running = false, epoch = 0
  let answer: ((choice: CollisionChoice) => void) | null = null
  const state = ctx.state
  const blocked = (at: number) => ctx.stale() || ctx.readOnly() || at !== epoch
  function respond(id: string, choice: CollisionChoice) {
    if (state.collision?.id !== id || !answer) return
    const resolve = answer
    if (choice === 'cancel' && !blocked(epoch)) state.cancelledUploads.push(state.collision.file.name)
    answer = null
    state.collision = null
    resolve(choice)
  }
  function detach() {
    if (state.collision) respond(state.collision.id, 'cancel')
  }
  function cancel() {
    epoch++
    queue.length = 0
    state.pendingCount = 0
    if (state.collision && answer) {
      const resolve = answer
      answer = null
      state.collision = null
      resolve('cancel')
    }
    ctx.changed()
  }
  async function send(file: File, at: number) {
    try {
      await uploadFile(ctx.flowId, file)
    } catch (error) {
      if (ctx.stale()) return
      const conflict = (error as { response?: { data?: { error?: { code?: string; details?: { key: string; content_sha256: string } } } } }).response?.data?.error
      if (conflict?.code !== 'ATTACHMENT_NAME_CONFLICT' || !conflict.details) { ctx.failed(error); return }
      await ctx.ready()
      if (ctx.stale()) return
      if (blocked(at)) return
      if (!ctx.hasView()) { state.cancelledUploads.push(file.name); return }
      const existing = { key: conflict.details.key, content_sha256: conflict.details.content_sha256 }
      const choice = await new Promise<CollisionChoice>(resolve => {
        answer = resolve
        state.collision = { id: `upload-${++requestSequence}`, file, ...existing }
      })
      await ctx.ready()
      if (blocked(at) || choice === 'cancel') return
      try { await uploadFile(ctx.flowId, file, choice, choice === 'overwrite' ? existing : undefined) }
      catch (failed) { if (!ctx.stale()) ctx.failed(failed); return }
    }
    // An accepted request still counts after a read-only transition, but never after session disposal.
    if (!ctx.stale()) ctx.completed()
  }
  async function run() {
    if (running) return
    running = true
    ctx.changed()
    try {
      while (queue.length && !ctx.stale()) {
        const item = queue.shift()!
        state.pendingCount = queue.length
        state.currentFile = item.file
        await ctx.ready()
        if (!blocked(item.epoch)) await send(item.file, item.epoch)
        state.currentFile = null
      }
    } finally {
      running = false
      state.currentFile = null
      ctx.changed()
    }
  }
  return {
    enqueue(files: readonly File[]) {
      if (ctx.stale() || ctx.readOnly()) return
      queue.push(...files.map(file => ({ file, epoch })))
      state.pendingCount = queue.length
      void run()
    },
    respond, detach, cancel,
    dispose() { cancel(); state.currentFile = null; state.cancelledUploads = [] },
    busy: () => running || queue.length > 0,
  }
}
