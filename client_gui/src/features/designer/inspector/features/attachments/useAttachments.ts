// flowgate.v02.0048 T#8 — the attachments tab's state and its tab number (D#1 §7.2 / §7.3, §10.3, §10.4).
// The attachments are those of the selected Node's document (attachmentsTargetDocId: the Node's own
// `docId`, D#1 §10.3). They are read through useInspectorResource. The server sends no event for an
// attachment change (D#1 F6, §7.4), so the list is read again after this tab's own add / delete, on a
// reconnect and on a manual retry; another user's change shows when the Node is selected again.
// Adding and deleting call the existing v0.1 API, then read the list and the guard again — success or
// refusal (D#1 §7.3, §8.1). Whether they are possible also reads the Flow's AI activity and the group's
// disposal (`group_disposed` of the document read), the facts the server refuses on; a refusal of the
// server's mutability guard (409 DOCUMENT_NOT_MUTABLE, 423) keeps adding and deleting disabled with its
// reason until a guard event (AI run started / finished, document refresh), a manual read again or
// another document. Opening downloads the file (a query, never blocked).
import { computed, reactive, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  ATTACHMENTS_MAX_COUNT_PER_DOC,
  ATTACHMENTS_MAX_FILES_PER_REQUEST,
  ATTACHMENTS_MAX_UPLOAD_BYTES,
  attachmentsDelete,
  attachmentsDownload,
  attachmentsErrorOf,
  attachmentsRead,
  attachmentsUpload,
  reviewActivityRead,
  reviewDocumentRead,
  type AttachmentsFile,
  type AttachmentsList,
} from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import {
  useInspectorEvents,
  useInspectorResource,
  type InspectorEventMatch,
  type InspectorEventScope,
  type InspectorResourceStatus,
} from '../../useInspectorResource'
import {
  attachmentsAvailability,
  attachmentsBlockedBy,
  attachmentsErrorKey,
  attachmentsFailureKey,
  attachmentsTabCount,
  attachmentsTargetDocId,
  attachmentsUploadRefusal,
  attachmentsView,
  type AttachmentsAvailability,
  type AttachmentsBlocked,
  type AttachmentsRowView,
} from './attachmentsModel'

/** Server events after which the list is read again: none — attachment changes send no event (D#1 F6). */
export const ATTACHMENTS_EVENTS: readonly string[] = []

/** Server events after which the change guard (AI activity, disposal) is read again. */
export const ATTACHMENTS_GUARD_EVENTS = ['ai_invoke_started', 'ai_invoke_finished', 'document_explorer_refresh'] as const

/** A guard event of any document of the target Flow, or of the target document itself. */
export const attachmentsGuardMatches: InspectorEventMatch = (frame, scope) =>
  frame.groupId === scope.flowId || (scope.docId !== null && frame.docId === scope.docId)

const LIMITS = {
  maxBytes: ATTACHMENTS_MAX_UPLOAD_BYTES,
  maxFiles: ATTACHMENTS_MAX_FILES_PER_REQUEST,
  maxCount: ATTACHMENTS_MAX_COUNT_PER_DOC,
}

// This folder's own change signal. The server sends nothing when attachments change, so after this
// tab adds or deletes (or finds a file gone), the tab number (read on its own, D#1 §14.3) is told here
// to read again.
const changes = reactive(new Map<string, number>())

function changed(docId: string) {
  changes.set(docId, (changes.get(docId) ?? 0) + 1)
}

function scopeOf(target: InspectorTarget | null): InspectorEventScope | null {
  return target ? { projectId: target.projectId, flowId: target.flowId, docId: attachmentsTargetDocId(target) } : null
}

interface AttachmentsGuard {
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
}

export interface AttachmentsFailure {
  readonly operation: 'add' | 'delete' | 'open'
  /** The attachment it was about (delete / open), or null (add). */
  readonly filename: string | null
  readonly key: string
  readonly params: Readonly<Record<string, string | number>>
}

export interface AttachmentsBusy {
  readonly operation: 'add' | 'delete'
  readonly filename: string | null
}

export interface AttachmentsState {
  readonly rows: ComputedRef<readonly AttachmentsRowView[] | null>
  readonly availability: ComputedRef<AttachmentsAvailability>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  readonly stale: Readonly<Ref<boolean>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  /** The add / delete waiting for the server or for the read after it, or null. */
  readonly busy: Readonly<Ref<AttachmentsBusy | null>>
  /** The attachment being downloaded, or null. */
  readonly opening: Readonly<Ref<string | null>>
  /** The last refused add / delete / open of this document, or null. */
  readonly failure: Readonly<Ref<AttachmentsFailure | null>>
  /** The server limits shown with the add button and the limit reason. */
  readonly limits: { readonly maxUploadMb: number; readonly maxCount: number }
  /** Uploads the files to the target document; nothing is sent when adding is disabled or a limit is passed. */
  add(files: readonly File[]): Promise<boolean>
  /** Deletes one attachment by its stored name; nothing is sent when deleting is disabled. */
  remove(filename: string): Promise<boolean>
  /** Downloads one attachment and hands it to the browser to save. */
  open(filename: string): Promise<boolean>
  reload(): Promise<void>
}

/** Hands a downloaded file to the browser's save (an object URL on a download link, no new window). */
function save(file: AttachmentsFile): void {
  const url = URL.createObjectURL(file.blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = file.filename
  anchor.rel = 'noopener'
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function useAttachments(target: () => InspectorTarget | null): AttachmentsState {
  const scope = () => scopeOf(target())
  const docId = () => scope()?.docId ?? null
  const list = useInspectorResource<AttachmentsList>({ target: scope, read: attachmentsRead, events: ATTACHMENTS_EVENTS })
  const guard = useInspectorResource<AttachmentsGuard>({
    target: scope,
    read: async (id) => {
      const flowId = target()?.flowId
      if (!flowId) throw new Error('no target Flow')
      const [activity, document] = await Promise.all([reviewActivityRead(flowId), reviewDocumentRead(id)])
      return { aiRunning: activity.active, groupDisposed: document.groupDisposed }
    },
    events: ATTACHMENTS_GUARD_EVENTS,
    matches: attachmentsGuardMatches,
  })

  const busy = ref<AttachmentsBusy | null>(null)
  const opening = ref<string | null>(null)
  const failure = ref<AttachmentsFailure | null>(null)
  const blocked = ref<AttachmentsBlocked>(null)

  // A refusal and what it blocks belong to the document they were refused on.
  watch(docId, () => {
    failure.value = null
    blocked.value = null
  })

  // The group state the mutability refusal came from may have changed: let the guard read decide again.
  useInspectorEvents(scope, {
    events: ATTACHMENTS_GUARD_EVENTS,
    matches: attachmentsGuardMatches,
    resync: false,
    changed: () => {
      blocked.value = null
    },
  })

  const readList = computed(() => {
    const read = list.data.value
    const current = docId()
    return read && current && read.docId === current ? read : null
  })

  const rows = computed(() => (readList.value ? attachmentsView(readList.value.items) : null))

  const availability = computed(() =>
    attachmentsAvailability({
      readOnly: target()?.readOnly ?? null,
      // An unanswered guard read does not block: the server refuses on the same facts.
      groupDisposed: guard.data.value?.groupDisposed === true,
      aiRunning: guard.data.value?.aiRunning === true,
      blocked: blocked.value,
      count: readList.value ? readList.value.items.length : null,
      maxCount: LIMITS.maxCount,
      busy: busy.value !== null,
    }),
  )

  const errorKey = computed(() => (list.status.value === 'error' ? attachmentsErrorKey(attachmentsErrorOf(list.error.value).kind) : null))

  /** Runs one change; success or refusal, the list and the guard are read again before it ends. */
  async function change(operation: 'add' | 'delete', filename: string | null, call: (id: string) => Promise<unknown>): Promise<boolean> {
    const id = docId()
    if (id === null) return false
    busy.value = { operation, filename }
    failure.value = null
    let ok = false
    try {
      try {
        await call(id)
        ok = true
      } catch (error) {
        if (docId() === id) {
          const read = attachmentsErrorOf(error)
          failure.value = { operation, filename, key: attachmentsFailureKey(operation, read.kind), params: {} }
          blocked.value = attachmentsBlockedBy(read.kind, read.reason) ?? blocked.value
        }
      }
      // Changes stay closed until the read is back, so the old list cannot send the same change twice.
      // The tab number reads again as well: a refusal may also find the server's list changed.
      changed(id)
      await Promise.all([list.reload(), guard.reload()])
    } finally {
      busy.value = null
    }
    return ok
  }

  async function add(files: readonly File[]): Promise<boolean> {
    if (docId() === null || !availability.value.add.enabled) return false
    const refusal = attachmentsUploadRefusal(files, readList.value ? readList.value.items.length : null, LIMITS)
    if (refusal) {
      failure.value = { operation: 'add', filename: null, key: refusal.key, params: refusal.params }
      return false
    }
    return change('add', null, (id) => attachmentsUpload(id, files))
  }

  async function remove(filename: string): Promise<boolean> {
    if (!availability.value.delete.enabled || !readList.value?.items.some((item) => item.filename === filename)) return false
    return change('delete', filename, (id) => attachmentsDelete(id, filename))
  }

  async function open(filename: string): Promise<boolean> {
    const id = docId()
    if (id === null || opening.value !== null) return false
    opening.value = filename
    if (failure.value?.operation === 'open') failure.value = null
    try {
      const file = await attachmentsDownload(id, filename)
      if (docId() === id) save(file)
      return docId() === id
    } catch (error) {
      if (docId() !== id) return false
      const kind = attachmentsErrorOf(error).kind
      failure.value = { operation: 'open', filename, key: attachmentsFailureKey('open', kind), params: {} }
      // A row whose file is gone: show what the server holds now, in the list and the tab number.
      if (kind === 'not_found') {
        changed(id)
        await list.reload()
      }
      return false
    } finally {
      opening.value = null
    }
  }

  return {
    rows,
    availability,
    status: list.status,
    stale: list.stale,
    errorKey,
    busy,
    opening,
    failure,
    limits: { maxUploadMb: Math.floor(LIMITS.maxBytes / (1024 * 1024)), maxCount: LIMITS.maxCount },
    add,
    remove,
    open,
    reload: async () => {
      blocked.value = null
      await Promise.all([list.reload(), guard.reload()])
    },
  }
}

/**
 * The attachments tab number supplier (D#1 §5.2, §10.4): the number of attachments of the target
 * document, read on its own and again after this folder's add / delete or a reconnect. None while
 * there is no target, before the first read succeeds, or with no attachment; a failed read again keeps
 * the last known number.
 */
export function useAttachmentsCount(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<number | null>> {
  const scope = () => scopeOf(target.value)
  const list = useInspectorResource<AttachmentsList>({ target: scope, read: attachmentsRead, events: ATTACHMENTS_EVENTS })
  watch(
    () => {
      const id = scope()?.docId ?? null
      return id === null ? null : `${changes.get(id) ?? 0}\n${id}`
    },
    (now, before) => {
      // A new document is read by the resource itself; only a change of the same document reads again.
      if (now !== null && before !== null && now.split('\n')[1] === before.split('\n')[1]) void list.reload()
    },
  )
  return computed(() => {
    const read = list.data.value
    const id = scope()?.docId ?? null
    return read && id && read.docId === id ? attachmentsTabCount(read.items) : null
  })
}
