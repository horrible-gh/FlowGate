<template>
  <p v-if="hasMore || (total != null && total > items.length)" class="notification-context" data-test="notification-truncated">{{ t('gui.designer.top.notifications.truncated', { shown: items.length, total: total ?? '—' }) }}</p>
  <p v-if="!items.length" class="notification-note">{{ t('gui.designer.top.notifications.empty_ai') }}</p>
  <ul v-else class="notification-list ai-list">
    <!-- 0016-CH B안 card row: the whole row opens the detail; the outcome is the status box's line icon. -->
    <li v-for="item in items" :key="item.run_id" class="notification-card ai-card" data-test="notification-ai">
      <button type="button" class="ai-row" :aria-label="`${title(item)} · ${t(k(state(item)))}`" @click="openAiNotificationDetail(item)">
        <span class="notification-status" :class="TONES[state(item)]" :title="t(k(state(item)))" :data-state="state(item)" aria-hidden="true"><LineIcon :name="ICONS[state(item)]" /></span>
        <span class="notification-main">
          <span class="notification-title ai-title" :title="item.doc_ref || item.run_id">{{ title(item) }}</span>
          <span class="notification-meta ai-summary" :title="item.last_message_excerpt || item.stop_code || item.outcome || undefined">{{ summary(item) || t(k(state(item))) }}</span>
        </span>
        <time v-if="item.finished_at" :datetime="item.finished_at" :title="item.finished_at">{{ relativeNotificationTime(item.finished_at, locale) || t('gui.designer.top.notifications.unknown_time') }}</time>
      </button>
      <div class="notification-row-actions">
        <button type="button" class="notification-icon-btn ai-detail" :title="t(k('detail'))" :aria-label="t(k('detail'))" @click="openAiNotificationDetail(item)"><LineIcon name="external" /></button>
      </div>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
import type { AiNotification } from './notificationApi'
import { openAiNotificationDetail } from './aiNotificationDetailWindow'
import { aiNotificationState as state, aiNotificationTitle as title, aiNotificationSummary as summary, relativeNotificationTime } from './notificationAiModel'
defineProps<{ items: AiNotification[]; total?: number; hasMore?: boolean }>()
const { t, locale } = useI18n()
const ICONS: Record<string, LineIconName> = { success: 'check-circle', failure: 'x-circle', stopped: 'stop-circle', unknown: 'help-circle' }
const TONES: Record<string, string> = { success: 'is-ok', failure: 'is-fail', stopped: 'is-wait', unknown: '' }
const k = (key: string) => `gui.notification_ai.${key}`
</script>
<style scoped>
/* The row button spans the status box and the text; the detail button sits in the card's last column. */
.notification-list .notification-card.ai-card { grid-template-columns: minmax(0, 1fr) auto; }
.ai-row { display: grid; grid-template-columns: 32px minmax(0, 1fr) auto; align-items: center; gap: 12px; min-width: 0; padding: 0; border: 0; background: transparent; text-align: left; }
.ai-row time { flex: none; color: var(--fg2-text-3); font-size: var(--fg2-fs-xs); white-space: nowrap; }
@media (max-width: 380px) { .ai-row { grid-template-columns: 32px minmax(0, 1fr); } .ai-row time { grid-column: 2; } }
</style>
