<template>
  <!-- flowgate.v02.0053 T#3 — every open document window of this tab (mounted once by the App Shell).
       flowgate.v02.0056 F1 — and every open 검수·반려 이력 window (DocumentHistoryWindow).
       flowgate.v02.0056 F2-2 — and every [작성] window of a card line (SlotWriteWindow) and AI call window of a
       document (DocumentAiWindow). -->
  <DocumentWindow v-for="win in windows" :key="win.id" :win="win" />
  <DocumentHistoryWindow v-for="win in historyWindows" :key="`history:${win.docId}`" :win="win" />
  <SlotWriteWindow v-for="win in writeWindows" :key="`write:${win.key}`" :win="win" />
  <DocumentAiWindow v-for="win in aiWindows" :key="`ai:${win.docId}`" :win="win" />
</template>

<script setup lang="ts">
import DocumentAiWindow from './DocumentAiWindow.vue'
import DocumentHistoryWindow from './DocumentHistoryWindow.vue'
import DocumentWindow from './DocumentWindow.vue'
import SlotWriteWindow from './SlotWriteWindow.vue'
import { documentAiWindowList } from './documentAiWindows'
import { documentHistoryWindowList, documentWindowList } from './documentWindows'
import { slotWriteWindowList } from './slotWriteWindows'

const windows = documentWindowList()
const historyWindows = documentHistoryWindowList()
const writeWindows = slotWriteWindowList()
const aiWindows = documentAiWindowList()
</script>
