<template>
  <ModelessShell :open="true" :title="t(k('detail'))" :initial-width="width" :initial-x="8" :initial-y="70" :min-width="240" @close="emit('close')">
    <section class="ai-notification-detail" role="region" :aria-label="t(k('detail'))" :aria-busy="loading" data-test="notification-ai-detail">
      <h3>{{ title(detail || item) }}</h3>
      <p v-if="loading" role="status">{{ t('gui.designer.top.notifications.loading') }}</p>
      <div v-else-if="error" role="alert">
        {{ t('gui.designer.top.notifications.load_error') }}
        <button @click="load">{{ t('gui.designer.top.notifications.refresh') }}</button>
      </div>
      <template v-else-if="detail">
        <p>{{ t(k(state(detail))) }}</p>
        <dl>
          <dt>{{ t(k('provider')) }}</dt><dd>{{ detail.provider_name || detail.provider?.name || '—' }}</dd>
          <dt>{{ t(k('finished')) }}</dt><dd>{{ detail.finished_at ? formatTime(detail.finished_at) : '—' }}</dd>
          <dt>{{ t(k('reason')) }}</dt><dd>{{ detail.stop_reason || detail.end_reason || '—' }}</dd>
        </dl>
        <pre>{{ detail.last_message || detail.last_message_excerpt || '—' }}</pre>
      </template>
      <button :disabled="!item.doc_ref" @click="item.doc_ref && emit('open-document', item.doc_ref)">{{ t('gui.designer.top.notifications.open_document') }}</button>
    </section>
  </ModelessShell>
</template>
<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { fetchAiNotificationDetail, type AiNotification, type AiNotificationDetail } from './notificationApi'
import { aiNotificationTitle as title, aiNotificationState as state } from './notificationAiModel'
const props = defineProps<{ item: AiNotification }>()
const emit = defineEmits<{ close: []; 'open-document': [id: string] }>()
const { t, locale } = useI18n()
const k = (key: string) => `gui.notification_ai.${key}`
const width = Math.min(440, Math.max(240, window.innerWidth - 16))
const detail = ref<AiNotificationDetail | null>(null), loading = ref(false), error = ref(false)
let disposed = false
async function load() {
  if (loading.value) return
  loading.value = true; error.value = false
  try {
    const result = await fetchAiNotificationDetail(props.item.run_id)
    if (!disposed) detail.value = result
  } catch { if (!disposed) error.value = true }
  finally { if (!disposed) loading.value = false }
}
function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(locale.value, { dateStyle: 'medium', timeStyle: 'short' }).format(date)
}
onMounted(load)
onBeforeUnmount(() => { disposed = true })
</script>
<style scoped>
.ai-notification-detail { min-width: 0; overflow-wrap: anywhere; font-size: 13px; }
dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 6px; }
dd { margin: 0; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
button { color: inherit; background: var(--fg2-panel); border: 1px solid var(--fg2-line-strong); padding: 6px; cursor: pointer; }
button:focus-visible { outline: 2px solid var(--fg2-focus); }
</style>
