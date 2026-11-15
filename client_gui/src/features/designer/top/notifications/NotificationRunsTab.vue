<template>
  <p>{{ t('gui.designer.top.notifications.project_only', { name: projectName }) }}</p>
  <p v-if="!rows.length">{{ t('gui.designer.top.notifications.empty_runs') }}</p>
  <ul v-else class="notification-list">
    <li v-for="run in rows" :key="run.key" data-test="notification-run">
      <strong>{{ run.flowName }}</strong>
      <span>{{ label('phases', run.phase) }}</span>
      <span v-if="run.mode === 'continuous' || run.target > 1">{{ run.reached }} / {{ run.target || '—' }}</span>
      <span>{{ label('modes', run.mode) }} · {{ run.provider || '—' }}</span>
      <p v-if="run.reason">{{ run.reason }}</p>
      <div class="notification-actions">
        <button :disabled="!run.docId" :title="t('gui.designer.top.notifications.open_document')" @click="run.docId && emit('open-document', run.docId)">{{ t('gui.designer.top.notifications.open_document') }}</button>
        <button :disabled="!run.runId || busy.includes(run.key) || ['cancelling', 'pause_requested'].includes(run.phase)" :title="t('gui.designer.top.notifications.stop')" @click="emit('stop', run)">{{ t('gui.designer.top.notifications.stop') }}</button>
      </div>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { InvokeRun } from './notificationApi'
defineProps<{ rows: (InvokeRun & { flowName: string })[]; projectName: string; busy: string[] }>()
const emit = defineEmits<{ 'open-document': [id: string]; stop: [run: InvokeRun] }>()
const { t, te } = useI18n()
function label(group: string, value: string) {
  const key = `gui.designer.top.notifications.${group}.${value}`
  return te(key) ? t(key) : t(`gui.designer.top.notifications.${group}.unknown`)
}
</script>
