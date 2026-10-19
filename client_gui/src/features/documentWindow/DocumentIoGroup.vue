<template>
  <!-- flowgate.v02.0068 T#1 — the [업로드][다운로드] group of one document, connected: the Inspector's document line
       and the document window's header both draw this, so both go through the one coordinator (documentIo). -->
  <DocumentIoButtons
    :upload="view.upload"
    :download="view.download"
    :upload-title="uploadTitle"
    :data-doc="docId"
    @upload="documentIoUpload(docId, { context: { projectId, groupId } })"
    @download="documentIoDownload(docId)"
  />
</template>

<script setup lang="ts">
// The document is read for the buttons when the group draws it (the first time, after a re-selection or a
// re-open, or another document), and again on its project events — a frame about the document or a refresh
// of its Flow — and on a reconnect (L §2.15 re-check path). No polling of its own.
// flowgate.v02.0074 T#2: a work plan's [업로드] opens its window's [JSON 가져오기] (the coordinator decides, for every place
// that draws this group); its tooltip is the plan window's own wording — 「JSON 가져오기」 or why the import is off.
import { computed, getCurrentInstance, onScopeDispose, shallowRef, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useProjectStore } from '@/features/project'
import { workPlanWindowMessages } from '@/features/workPlanWindow/messages'
import DocumentIoButtons from './DocumentIoButtons.vue'
import {
  documentIoButtons,
  documentIoDownload,
  documentIoDraw,
  documentIoInfo,
  documentIoInfoGeneration,
  documentIoIsWorkPlan,
  documentIoRefresh,
  documentIoUpload,
} from './documentIo'
import { DOCUMENT_WINDOW_EVENTS, DOCUMENT_WINDOW_REFRESH_COALESCE_MS, documentWindowEventMatches, documentWindowFlowEventMatches } from './useDocumentWindowEvents'

const props = defineProps<{
  /** The document the presses act on (the line's document; the window's shown or edited one). */
  docId: string
  /** Stable parent context is available even when the first information read fails. */
  projectId?: string | null
  groupId?: string | null
}>()

const view = computed(() => documentIoButtons(props.docId))

const { t: gt } = useI18n({ useScope: 'global' })
const { t: wt } = useI18n({ useScope: 'local', messages: workPlanWindowMessages, inheritLocale: true })
/** A work plan's upload tooltip (no new wording: the plan window's and the group's own); null for any other document. */
const uploadTitle = computed<string | null>(() => {
  if (!documentIoIsWorkPlan(props.docId)) return null
  const reason = view.value.upload.reason
  if (reason === 'locked' || reason === 'step_pending') return wt(`json.blocked.${reason}`)
  if (reason === 'forbidden') return gt('gui.document_io.disabled.forbidden')
  return wt('json.import')
})

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
