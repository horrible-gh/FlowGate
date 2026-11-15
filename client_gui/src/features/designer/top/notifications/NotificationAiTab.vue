<template>
  <p v-if="hasMore || (total != null && total > items.length)" data-test="notification-truncated">{{ t('gui.designer.top.notifications.truncated', { shown: items.length, total: total ?? '—' }) }}</p>
  <p v-if="!items.length">{{ t('gui.designer.top.notifications.empty_ai') }}</p>
  <ul v-else class="notification-list">
    <li v-for="item in items" :key="item.run_id" data-test="notification-ai">
      <strong>{{ item.doc_title || item.doc_ref || item.run_id }}</strong>
      <span>{{ t(`gui.designer.top.notifications.${item.succeeded === true ? 'success' : item.succeeded === false ? 'failure' : 'unknown'}`) }}</span>
      <p>{{ item.last_message_excerpt || item.outcome }}</p>
      <time v-if="item.finished_at" :datetime="item.finished_at">{{ formatTime(item.finished_at) }}</time>
      <button :disabled="!item.doc_ref" @click="item.doc_ref && emit('open-document', item.doc_ref)">{{ t('gui.designer.top.notifications.open_document') }}</button>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { AiNotification } from './notificationApi'
defineProps<{ items: AiNotification[]; total?: number; hasMore?: boolean }>()
const emit = defineEmits<{ 'open-document': [id: string] }>()
const { t, locale } = useI18n()
function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? t('gui.designer.top.notifications.unknown_time')
    : new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
</script>
