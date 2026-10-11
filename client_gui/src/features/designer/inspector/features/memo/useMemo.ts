// flowgate.v02.0053 T#9 — the header ✎ 메모: reads the card document's memos and writes or opens one. The
// memos are read again when the target document changes and on a server document refresh; a write reads
// them again after its answer. A click before the first read has answered waits for it, so a memo is never
// written twice. Memos that could not be read are not "none": the click reads them again and writes nothing
// until a read answers. The memo opens in the document window: the existing one as it reads, a new one in its
// editor.
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { documentErrorOf, memoCreate, memoList, type MemoRecord } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument } from '../../openDocument'
import { useInspectorResource } from '../../useInspectorResource'
import { memoFailureOf, memoLatest, memoTitleWithin, type MemoFailure } from './memoModel'

/** Server events after which a document's memos are read again. */
export const MEMO_EVENTS = ['document_explorer_refresh'] as const

export interface MemoState {
  /** The memo the button opens, or null (none yet: the button writes one). */
  readonly latest: ComputedRef<MemoRecord | null>
  /** The last read of the memos failed: the button neither shows "none" nor writes until a read answers. */
  readonly unread: ComputedRef<boolean>
  /** A write is waiting for its answer. */
  readonly busy: Readonly<Ref<boolean>>
  /** The last refused write for this target, or null. */
  readonly failure: Readonly<Ref<MemoFailure | null>>
  /** Opens the newest memo, or writes a new one titled `title` and opens it in its editor. */
  open(title: string): Promise<void>
}

export function useMemo(target: () => InspectorTarget | null): MemoState {
  const memos = useInspectorResource<MemoRecord[]>({ target, read: memoList, events: MEMO_EVENTS })
  const busy = ref(false)
  const failure = ref<MemoFailure | null>(null)

  watch(
    () => target()?.docId ?? null,
    () => {
      failure.value = null
    },
  )

  const latest = computed(() => memoLatest(memos.data.value))
  const unread = computed(() => memos.status.value === 'error')

  async function open(title: string): Promise<void> {
    const docId = target()?.docId
    if (!docId || busy.value) return
    busy.value = true
    failure.value = null
    try {
      if (memos.status.value !== 'ready') await memos.reload()
      if (target()?.docId !== docId) return
      // Still unread (`unread` tells it): nothing is opened or written.
      if (memos.status.value !== 'ready') return
      const found = latest.value
      if (found) {
        openDocument(found.docId, 'view')
        return
      }
      const created = await memoCreate(docId, memoTitleWithin(title, docId))
      openDocument(created, 'edit')
      if (target()?.docId === docId) await memos.reload()
    } catch (error) {
      if (target()?.docId === docId) failure.value = memoFailureOf(documentErrorOf(error))
    } finally {
      busy.value = false
    }
  }

  return { latest, unread, busy, failure, open }
}
