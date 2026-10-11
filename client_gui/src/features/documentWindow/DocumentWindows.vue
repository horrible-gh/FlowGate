<template>
  <!-- flowgate.v02.0053 T#3 — every open document window of this tab (mounted once by the App Shell). -->
  <DocumentWindow v-for="win in windows" :key="win.docId" :win="win" />
</template>

<script setup lang="ts">
import DocumentWindow from './DocumentWindow.vue'
import { documentWindowList } from './documentWindows'

const windows = documentWindowList()
</script>
