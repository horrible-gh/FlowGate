<template>
  <!-- flowgate.v02.0053 T#3 — every open document window of this tab (mounted once by the App Shell).
       flowgate.v02.0056 F1 — and every open 검수·반려 이력 window (DocumentHistoryWindow). -->
  <DocumentWindow v-for="win in windows" :key="win.docId" :win="win" />
  <DocumentHistoryWindow v-for="win in historyWindows" :key="`history:${win.docId}`" :win="win" />
</template>

<script setup lang="ts">
import DocumentHistoryWindow from './DocumentHistoryWindow.vue'
import DocumentWindow from './DocumentWindow.vue'
import { documentHistoryWindowList, documentWindowList } from './documentWindows'

const windows = documentWindowList()
const historyWindows = documentHistoryWindowList()
</script>
