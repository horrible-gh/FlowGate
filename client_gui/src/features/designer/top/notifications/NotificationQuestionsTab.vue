<template>
  <p v-if="hasMore || (total != null && total > items.length)" data-test="notification-truncated">{{ t('gui.designer.top.notifications.truncated', { shown: items.length, total: total ?? '—' }) }}</p>
  <p v-if="!items.length">{{ t('gui.designer.top.notifications.empty_questions') }}</p>
  <ul v-else class="notification-list">
    <li v-for="item in items" :key="item.doc_id" data-test="notification-question">
      <strong>{{ item.title || item.doc_id }}</strong>
      <span>{{ typeLabel(item.type_code) }}</span>
      <button @click="emit('open-document', item.doc_id)">{{ t('gui.designer.top.notifications.open_document') }}</button>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { QuestionNotification } from './notificationApi'
defineProps<{ items: QuestionNotification[]; total?: number; hasMore?: boolean }>()
const emit = defineEmits<{ 'open-document': [id: string] }>()
const { t, te } = useI18n()
function typeLabel(code?: string | null) {
  const key = `gui.document_window.types.${code?.toUpperCase()}`
  return te(key) ? t(key) : t('gui.document_window.types.other')
}
</script>
