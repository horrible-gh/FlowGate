<template>
  <!-- flowgate.v02.0053 T#3 — every open document window of this tab (mounted once by the App Shell).
       flowgate.v02.0056 F1 — and every open 검수·반려 이력 window (DocumentHistoryWindow).
       flowgate.v02.0056 F2-2 — and every [작성] window of a card line (SlotWriteWindow) and AI call window of a
       document (DocumentAiWindow).
       flowgate.v02.0068 T#1 — and the notices of the document upload / download (one toast host for the
       Inspector's lines and the windows' headers). -->
  <DocumentWindow v-for="win in windows" :key="win.id" :win="win" />
  <DocumentHistoryWindow v-for="win in historyWindows" :key="`history:${win.docId}`" :win="win" />
  <SlotWriteWindow v-for="win in writeWindows" :key="`write:${win.key}`" :win="win" />
  <DocumentAiWindow v-for="win in aiWindows" :key="`ai:${win.docId}`" :win="win" />
  <!-- flowgate.v02.0072 T#1 — and every work plan edit window (features/workPlanWindow; opened from the WP card in T#3). -->
  <WorkPlanEditWindows />
  <ToastHost :toasts="ioToasts" :dismiss-label="t('gui.document_window.close')" @dismiss="(id) => dismissDocumentIoNotice(Number(id))" />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import WorkPlanEditWindows from '@/features/workPlanWindow/WorkPlanEditWindows.vue'
import DocumentAiWindow from './DocumentAiWindow.vue'
import DocumentHistoryWindow from './DocumentHistoryWindow.vue'
import DocumentWindow from './DocumentWindow.vue'
import SlotWriteWindow from './SlotWriteWindow.vue'
import { dismissDocumentIoNotice, documentIoNotices } from './documentIo'
import { documentAiWindowList } from './documentAiWindows'
import { documentHistoryWindowList, documentWindowList } from './documentWindows'
import { slotWriteWindowList } from './slotWriteWindows'

const { t } = useI18n()

const windows = documentWindowList()
const historyWindows = documentHistoryWindowList()
const writeWindows = slotWriteWindowList()
const aiWindows = documentAiWindowList()

/** How long a notice stays (the settings screens' toasts). */
const NOTICE_MS = 3200

const notices = documentIoNotices()
const ioToasts = computed<ToastHostItem[]>(() =>
  notices.map((item) => ({ id: item.id, message: t(`gui.document_io.notice.${item.key}`), tone: item.tone, duration: NOTICE_MS })),
)
</script>
