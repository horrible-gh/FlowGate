<template>
  <p v-if="hasMore || (total != null && total > items.length)" data-test="notification-truncated">{{ t('gui.designer.top.notifications.truncated', { shown: items.length, total: total ?? '—' }) }}</p>
  <p v-if="!items.length">{{ t('gui.designer.top.notifications.empty_ai') }}</p>
  <ul v-else class="notification-list ai-list">
    <li v-for="item in items" :key="item.run_id" data-test="notification-ai">
      <button class="ai-row" :aria-label="`${title(item)} · ${t(k(state(item)))}`" @click="selected = item">
        <span :title="t(k(state(item)))" :data-state="state(item)" aria-hidden="true">{{ icons[state(item)] }}</span>
        <span class="ai-title" :title="item.doc_ref || item.run_id">{{ title(item) }}</span>
        <span class="ai-summary" :title="item.last_message_excerpt || item.stop_code || item.outcome || undefined">{{ summary(item) || t(k(state(item))) }}</span>
        <time v-if="item.finished_at" :datetime="item.finished_at" :title="item.finished_at">{{ relativeNotificationTime(item.finished_at, locale) || t('gui.designer.top.notifications.unknown_time') }}</time>
      </button>
      <button class="ai-detail" :title="t(k('detail'))" :aria-label="t(k('detail'))" @click="selected = item">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3h7v7M21 3l-9 9M10 3H3v18h18v-7" /></svg>
      </button>
    </li>
  </ul>
  <NotificationAiDetail v-if="selected" :key="selected.run_id" :item="selected" @close="selected = null" @open-document="emit('open-document', $event)" />
</template>
<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import type { AiNotification } from './notificationApi'
import NotificationAiDetail from './NotificationAiDetail.vue'
import { aiNotificationState as state, aiNotificationTitle as title, aiNotificationSummary as summary, relativeNotificationTime } from './notificationAiModel'
defineProps<{ items: AiNotification[]; total?: number; hasMore?: boolean }>()
const emit = defineEmits<{ 'open-document': [id: string] }>()
const { t, locale } = useI18n()
const selected = ref<AiNotification | null>(null)
const icons = { success: '✓', failure: '!', stopped: '■', unknown: '?' }
const k = (key: string) => `gui.notification_ai.${key}`
</script>
<style scoped>
.ai-list li { display: flex; flex-wrap: nowrap; align-items: center; min-width: 0; gap: 4px; }
.ai-list .ai-row { display: flex; flex: 1; align-items: center; gap: 6px; min-width: 0; border: 0; padding: 6px 0; text-align: left; }
.ai-title { flex: 0 1 30%; font-weight: 600; }
.ai-summary { flex: 1; }
.ai-title, .ai-summary { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
time { flex: none; font-size: 11px; color: var(--fg2-muted); }
.ai-detail { flex: none; }
.ai-detail svg { display: block; width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.6; }
[data-state="success"] { color: var(--fg2-text); }
@media (max-width: 380px) { .ai-row { flex-wrap: wrap; } .ai-title { flex-basis: 65%; } .ai-summary { flex-basis: 60%; } }
</style>
