<template>
  <p class="notification-context">{{ t('gui.designer.top.notifications.project_only', { name: projectName }) }}</p>
  <p v-if="!rows.length && !mergeItems.length" class="notification-note">{{ t('gui.designer.top.notifications.empty_runs') }}</p>
  <!-- flowgate.v02test.0042 (시안 v7 item 3): card result merges — red when a person must choose, blue while it only waits;
       [열기] opens the card's '병합 · 충돌' tab. -->
  <ul v-if="mergeItems.length" class="notification-list" data-test="notification-merges">
    <li v-for="item in mergeItems" :key="`${item.flowId}:${item.nodeKey}`" class="notification-card" data-test="notification-merge" :data-state="item.state">
      <span class="notification-status" :class="item.state === 'needs_choice' || item.state === 'failed' ? 'is-fail' : 'is-run'" aria-hidden="true"><LineIcon :name="item.state === 'needs_choice' || item.state === 'failed' ? 'stop-circle' : 'loader'" /></span>
      <div class="notification-main">
        <strong class="notification-title" :title="item.flowName">{{ item.flowName }}</strong>
        <span class="notification-meta" data-test="notification-merge-text">{{ mergeText(item) }}</span>
      </div>
      <div class="notification-side">
        <div class="notification-row-actions">
          <button type="button" class="notification-icon-btn" data-test="notification-merge-open" :aria-label="t('gui.designer.top.notifications.merge.open')" :title="t('gui.designer.top.notifications.merge.open')" @click="emit('open-merge', item)"><LineIcon name="external" /></button>
        </div>
      </div>
    </li>
  </ul>
  <ul v-if="rows.length" class="notification-list">
    <!-- 0016-CH B안 card row: [phase icon] [Flow / phase · progress · mode · AI (reason)] [open · stop]. -->
    <li v-for="run in rows" :key="run.key" class="notification-card" data-test="notification-run" :data-phase="run.phase">
      <span class="notification-status" :class="phaseTone(run.phase)" :title="label('phases', run.phase)" aria-hidden="true"><LineIcon :name="phaseIcon(run.phase)" /></span>
      <div class="notification-main">
        <strong class="notification-title" :title="run.flowName">{{ run.flowName }}</strong>
        <span class="notification-meta"><span>{{ label('phases', run.phase) }}</span><template v-if="run.mode === 'continuous' || run.target > 1"> · <span>{{ run.reached }} / {{ run.target || '—' }}</span></template> · <span>{{ label('modes', run.mode) }} · {{ run.provider || '—' }}</span></span>
        <p v-if="run.reason" class="notification-reason">{{ run.reason }}</p>
      </div>
      <div class="notification-side">
        <div class="notification-row-actions">
          <button type="button" class="notification-icon-btn" :disabled="!run.docId" :aria-label="t('gui.designer.top.notifications.open_document')" :title="t('gui.designer.top.notifications.open_document')" @click="run.docId && emit('open-document', run.docId)"><LineIcon name="external" /></button>
          <button type="button" class="notification-icon-btn" :disabled="!run.runId || busy.includes(run.key) || ['cancelling', 'pause_requested'].includes(run.phase)" :aria-label="t('gui.designer.top.notifications.stop')" :title="t('gui.designer.top.notifications.stop')" @click="emit('stop', run)"><LineIcon name="stop" /></button>
        </div>
      </div>
    </li>
  </ul>
</template>
<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import LineIcon, { type LineIconName } from '@/components/ui/LineIcon.vue'
import type { CardMergeItem } from '@/features/document'
import type { InvokeRun } from './notificationApi'
type MergeRow = CardMergeItem & { flowName: string }
withDefaults(defineProps<{ rows: (InvokeRun & { flowName: string })[]; projectName: string; busy: string[]; mergeItems?: MergeRow[] }>(), { mergeItems: () => [] })
const emit = defineEmits<{ 'open-document': [id: string]; stop: [run: InvokeRun]; 'open-merge': [item: MergeRow] }>()
const { t, te } = useI18n()
/** A merge row's sentence: the card (its title) and what it needs. */
function mergeText(item: MergeRow): string {
  const base = 'gui.designer.top.notifications.merge'
  if (item.state === 'waiting' && item.blockedBy) return t(`${base}.waiting_for`, { title: item.title, blocked: item.blockedBy.title })
  return t(`${base}.${item.state}`, { title: item.title })
}
function label(group: string, value: string) {
  const key = `gui.designer.top.notifications.${group}.${value}`
  return te(key) ? t(key) : t(`gui.designer.top.notifications.${group}.unknown`)
}
/** The status box of a phase: running blue, waiting / paused orange, stopping red, unknown grey. */
function phaseTone(phase: string): string {
  if (phase === 'running' || phase === 'starting') return 'is-run'
  if (phase === 'queued' || phase === 'paused' || phase === 'pause_requested') return 'is-wait'
  if (phase === 'cancelling') return 'is-fail'
  return ''
}
function phaseIcon(phase: string): LineIconName {
  if (phase === 'running' || phase === 'starting') return 'loader'
  if (phase === 'paused' || phase === 'pause_requested') return 'pause-circle'
  if (phase === 'cancelling') return 'stop-circle'
  if (phase === 'queued') return 'play'
  return 'help-circle'
}
</script>
