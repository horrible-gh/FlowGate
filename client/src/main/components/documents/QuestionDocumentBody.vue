<template>
  <!-- Q (question) body — the last body still rendered from MainPanel's template after the
       AC/DC/generic move (T#1) and the CH/WP move (T0007 §3-4). It leaves with them so the
       router is the single place a new document type gets a body, and MainPanel's template
       holds no body markup at all. -->
  <div class="card md-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <span class="doc-tag c-Q" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">Q</span>
        {{ tab.title }}
      </span>
    </div>
    <div class="card-bd" style="padding:16px;">
      <QTDetailViewer :q-id="tab.id" :read-only="readOnly" @status-changed="emit('status-changed', $event)" />
    </div>
  </div>
</template>

<script setup lang="ts">
import type { Tab } from '../../stores/tabs'
import QTDetailViewer from '../QTDetailViewer.vue'

defineProps<{
  tab: Tab
  readOnly: boolean
}>()

const emit = defineEmits<{
  'status-changed': [payload: { qId: string; status: string; done: boolean }]
}>()
</script>

<style scoped>
/* .card (global) has overflow:hidden, which clips anything a card head pops out of it. */
.md-preview-card { overflow: visible; }
</style>
