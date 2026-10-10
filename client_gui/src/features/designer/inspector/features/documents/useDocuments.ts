// flowgate.v02.0048 T#5 — the documents tab's state (D#1 §7.2 / §7.3, §10.2). Reads the target
// document with its review extras and the documents it superseded through `@/features/document`,
// and reads them again when the server says the document changed: its review status, an AI
// review result, or its documents (a new revision supersedes it). The tab changes nothing on the
// server; ↗ 열기 hands the document to the v0.1 screen (openDocument, D#1 §9.2).
import { computed, type ComputedRef, type Ref } from 'vue'
import {
  documentDetailRead,
  documentErrorOf,
  documentRelationsRead,
  type DocumentDetail,
  type DocumentRelation,
} from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument } from '../../openDocument'
import { useInspectorResource, type InspectorResourceStatus } from '../../useInspectorResource'
import { documentsAvailability, documentsErrorKey, documentsView, type DocumentsView } from './documentsModel'

/** Server events after which the documents tab reads again (D#1 §7.4). */
export const DOCUMENTS_EVENTS = ['doc_review_status_changed', 'ai_review_arrived', 'document_explorer_refresh'] as const

export interface DocumentsRead {
  readonly detail: DocumentDetail
  /** The documents the target document superseded (Canvas ids), each read on its own. */
  readonly previous: readonly DocumentRelation[]
}

export interface DocumentsState {
  readonly view: ComputedRef<DocumentsView | null>
  readonly availability: ComputedRef<ReturnType<typeof documentsAvailability>>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  /** The shown data is older than a known change (a read again is running or failed). */
  readonly stale: Readonly<Ref<boolean>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  reload(): Promise<void>
  /** ↗ 열기: the v0.1 document screen in a new tab. */
  open(docId: string): void
}

export function useDocuments(target: () => InspectorTarget | null): DocumentsState {
  const resource = useInspectorResource<DocumentsRead>({
    target,
    read: async (docId) => {
      const supersedes = target()?.supersedes ?? []
      const [detail, previous] = await Promise.all([documentDetailRead(docId), documentRelationsRead(supersedes)])
      return { detail, previous }
    },
    events: DOCUMENTS_EVENTS,
  })

  const view = computed(() => {
    const current = target()
    const read = resource.data.value
    return current ? documentsView(current, read?.detail ?? null, read?.previous ?? null) : null
  })
  const availability = computed(() => documentsAvailability(target()))
  const errorKey = computed(() =>
    resource.status.value === 'error' ? documentsErrorKey(documentErrorOf(resource.error.value).kind) : null,
  )

  return {
    view,
    availability,
    status: resource.status,
    stale: resource.stale,
    errorKey,
    reload: resource.reload,
    open: (docId) => {
      if (docId) openDocument(docId)
    },
  }
}
