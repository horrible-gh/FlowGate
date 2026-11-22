<template>
  <p v-if="hasMore || (total != null && total > shown.length)" class="notification-context" data-test="notification-truncated">{{ t('gui.designer.top.notifications.truncated', { shown: shown.length, total: total ?? '—' }) }}</p>
  <p v-if="!items.length" class="notification-note">{{ t('gui.designer.top.notifications.empty_ai') }}</p>
  <ul v-else class="notification-list ai-list">
    <!-- 0016-CH option B card row: the whole row opens the detail; the outcome is the status box's line icon. -->
    <li v-for="item in shown" :key="item.run_id" class="notification-card ai-card" data-test="notification-ai">
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
  <!-- The tab opens short: the newest rows only. The rest of what was loaded opens in place. -->
  <button v-if="items.length > AI_TAB_SHORT_COUNT" type="button" class="ai-more" data-test="notification-ai-more" :aria-expanded="expanded" @click="expanded = !expanded">{{ expanded ? t('gui.designer.top.notifications.show_less') : t('gui.designer.top.notifications.show_more', { count: items.length - AI_TAB_SHORT_COUNT }) }}</button>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
import type { AiNotification } from './notificationApi'
import { openAiNotificationDetail } from './aiNotificationDetailWindow'
import { aiNotificationState as state, aiNotificationTitle as title, aiNotificationSummary as summary, relativeNotificationTime } from './notificationAiModel'
const props = defineProps<{ items: AiNotification[]; total?: number; hasMore?: boolean }>()
/** Rows drawn before "show more": the popover stays about half a screen tall instead of listing all 50. */
const AI_TAB_SHORT_COUNT = 6
const expanded = ref(false)
const shown = computed(() => expanded.value ? props.items : props.items.slice(0, AI_TAB_SHORT_COUNT))
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
.ai-more { align-self: center; height: 30px; margin: 2px auto 0; padding: 0 14px; border: 1px solid var(--fg2-line); border-radius: 8px; background: var(--fg2-control-surface-flat); color: var(--fg2-text-2); font-size: var(--fg2-fs-sub); }
.ai-more:hover { color: var(--fg2-text); }
@media (max-width: 380px) { .ai-row { grid-template-columns: 32px minmax(0, 1fr); } .ai-row time { grid-column: 2; } }
</style>
