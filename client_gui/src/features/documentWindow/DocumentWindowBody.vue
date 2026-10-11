<template>
  <!-- flowgate.v02.0053 T#3 — the body spot of the document window: picks the view a document gets
       (documentBodyView). Every document is the MD Viewer for now; the next task adds the special
       views (work plan, test scenario, test report) here, and the window around it stays as it is. -->
  <div class="document-window-body" data-test="document-window-body" :data-view="view">
    <MdViewer ref="viewer" :doc-id="docId" />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import MdViewer from './MdViewer.vue'
import { documentBodyView } from './documentWindowModel'
import type { DocumentContentChange } from './useDocumentWindowEvents'

const props = defineProps<{
  docId: string
  typeCode: string | null
}>()

const view = computed(() => documentBodyView(props.typeCode))

const viewer = ref<InstanceType<typeof MdViewer> | null>(null)

/** The stored body may have changed (a server event, a re-open): the view reads it again. */
function refresh(change: DocumentContentChange): void {
  viewer.value?.requestContentRefresh({ doc_id: change.docId, revision_no: change.revisionNo })
}

defineExpose({ refresh })
</script>

<style scoped>
.document-window-body {
  height: 100%;
  min-height: 0;
}
</style>
