<template>
  <NotificationAiDetail v-if="item" :key="item.run_id" :item="item" @close="closeAiNotificationDetail" @open-document="openDocumentWindow($event)" />
</template>
<script setup lang="ts">
// flowgate.v02gui.0033 0002-CH — draws the AI notification 「상세보기」 window apart from the notification popover
// (see aiNotificationDetailWindow.ts), so closing the popover never closes the window. A project switch or the
// header going away closes it: the run belongs to the project it was opened from.
import { computed, onBeforeUnmount, watch } from 'vue'
import { openDocumentWindow } from '@/features/documentWindow/documentWindows'
import NotificationAiDetail from './NotificationAiDetail.vue'
import { aiNotificationDetail, closeAiNotificationDetail } from './aiNotificationDetailWindow'
const props = withDefaults(defineProps<{ projectId?: string | null }>(), { projectId: null })
const item = computed(() => aiNotificationDetail.value)
watch(() => props.projectId, () => closeAiNotificationDetail())
onBeforeUnmount(closeAiNotificationDetail)
</script>
