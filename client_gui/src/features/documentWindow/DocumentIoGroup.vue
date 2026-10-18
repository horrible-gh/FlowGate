<template>
  <!-- flowgate.v02.0068 T#1 — the [업로드][다운로드] group of one document, connected: the Inspector's document line
       and the document window's header both draw this, so both go through the one coordinator (documentIo). -->
  <DocumentIoButtons
    :upload="view.upload"
    :download="view.download"
    :data-doc="docId"
    @upload="documentIoUpload(docId)"
    @download="documentIoDownload(docId)"
  />
</template>

<script setup lang="ts">
// The document is read for the buttons when the group draws it (the first time, after a re-selection or a
// re-open, or another document), and again on its project events — a frame about the document or a refresh
// of its Flow — and on a reconnect (L §2.15 re-check path). No polling of its own.
import { computed, getCurrentInstance, onScopeDispose, shallowRef, watch } from 'vue'
import { useProjectStore } from '@/features/project'
import DocumentIoButtons from './DocumentIoButtons.vue'
import { documentIoButtons, documentIoDownload, documentIoDraw, documentIoInfo, documentIoInfoGeneration, documentIoRefresh, documentIoUpload } from './documentIo'
import { DOCUMENT_WINDOW_EVENTS, DOCUMENT_WINDOW_REFRESH_COALESCE_MS, documentWindowEventMatches, documentWindowFlowEventMatches } from './useDocumentWindowEvents'

const props = defineProps<{
  /** The document the presses act on (the line's document; the window's shown or edited one). */
  docId: string
  /** Stable parent context is available even when the first information read fails. */
  projectId?: string | null
  groupId?: string | null
}>()

const view = computed(() => documentIoButtons(props.docId))

watch([() => props.docId, documentIoInfoGeneration], ([docId]) => documentIoDraw(docId), { immediate: true })

const record = computed(() => documentIoInfo(props.docId).record)
// Keep the last known context independently of READY/DENIED/GONE/ERROR and cache invalidation.
const known = shallowRef<{ projectId: string | null; groupId: string | null } | null>(null)
watch([() => props.docId, record], ([docId, current], previous) => {
  if (previous && previous[0] !== docId) known.value = null
  if (current) known.value = { projectId: current.projectId, groupId: current.groupId }
}, { immediate: true, flush: 'sync' })
const projectId = computed(() => props.projectId ?? known.value?.projectId ?? null)
const groupId = computed(() => props.groupId ?? known.value?.groupId ?? null)

// The project event hub lives in the app's Pinia; mounted without one (a unit test) nothing is heard.
const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
let pending: ReturnType<typeof setTimeout> | null = null
const refresh = () => {
  if (pending !== null) return
  pending = setTimeout(() => {
    pending = null
    documentIoRefresh(props.docId)
  }, DOCUMENT_WINDOW_REFRESH_COALESCE_MS)
}

watch(
  projectId,
  (projectId, _previous, onCleanup) => {
    if (!pinia || projectId === null) return
    const store = useProjectStore(pinia)
    onCleanup(
      store.subscribeProjectEvents(projectId, {
        events: DOCUMENT_WINDOW_EVENTS,
        event: (frame) => {
          if (documentWindowEventMatches(frame, props.docId) || documentWindowFlowEventMatches(frame, groupId.value)) refresh()
        },
        resync: refresh,
      }),
    )
  },
  { immediate: true },
)

onScopeDispose(() => {
  if (pending !== null) clearTimeout(pending)
})
</script>
