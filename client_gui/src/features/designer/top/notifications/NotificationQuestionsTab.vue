<template>
  <p v-if="!items.length">{{ t('gui.designer.top.notifications.empty_questions') }}</p>
  <ul v-else class="notification-list">
    <NotificationQuestionDocument v-for="group in groups" :key="group.target.docId!" :target="group.target" :rows="group.rows" :flow-name="group.flowName" :refresh-key="refreshKey" />
  </ul>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import type { QaOpenItem } from '@/features/document/api/qa'
import type { ExplorerProject } from '../../projectExplorer/projectExplorerTree'
import { inspectorCardKind, type InspectorTarget } from '../../inspector/inspectorTarget'
import NotificationQuestionDocument from './NotificationQuestionDocument.vue'
const props = defineProps<{ items: QaOpenItem[]; projectId: string; project: ExplorerProject | null; refreshKey: number }>()
const { t } = useI18n()
const groups = computed(() => {
  const docs = new Map<string, QaOpenItem[]>()
  for (const row of props.items) docs.set(row.doc_id, [...(docs.get(row.doc_id) ?? []), row])
  const flows = props.project?.groups.flatMap(group => group.flows) ?? []
  return [...docs].map(([docId, rows]) => {
    const flowId = docId.split('.').slice(0, -1).join('.')
    const target: InspectorTarget = { projectId: props.projectId, flowId, docId, nodeId: docId, nodeKind: 'document', docType: rows[0]?.type_code ?? null, cardKind: inspectorCardKind(rows[0]?.type_code), title: '', canvasState: null, docReviewStatus: null, docStatus: null, supersedes: [], slot: null, nodeRun: null, cardDocuments: [], nodeType: null, cardDocType: null, rootBound: false, ownership: null, viewMode: null, readOnly: null }
    return { target, rows, flowName: flows.find(flow => flow.id === flowId)?.name ?? flowId }
  })
})
</script>
