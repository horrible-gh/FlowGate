<template>
  <p class="notification-context">{{ t('gui.designer.top.notifications.project_only', { name: projectName }) }}</p>
  <p v-if="!rows.length && !mergeItems.length" class="notification-note">{{ t('gui.designer.top.notifications.empty_runs') }}</p>
  <!-- (mockup v7 item 3): card result merges — a red-framed row with the conflict mark when a person
       must choose, a blue hourglass while it waits (a spinner while it catches up); [열기] opens the card's '병합 · 충돌' tab. -->
  <ul v-if="mergeItems.length" class="notification-list" data-test="notification-merges">
    <li v-for="item in mergeItems" :key="`${item.flowId}:${item.nodeKey}`" class="notification-card notification-merge" :class="{ 'is-choice': choice(item) }" data-test="notification-merge" :data-state="item.state">
      <span class="notification-status" :class="[choice(item) ? 'is-fail' : 'is-run', { 'is-spinning': item.state === 'catching_up' }]" aria-hidden="true">
        <svg viewBox="0 0 24 24"><path :d="MERGE_ICONS[item.state]" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
      </span>
      <div class="notification-main">
        <strong class="notification-title" :title="item.flowName">{{ item.flowName }}</strong>
        <span class="notification-meta" data-test="notification-merge-text">{{ mergeText(item) }}</span>
      </div>
      <div class="notification-side">
        <button type="button" class="notification-merge-open" data-test="notification-merge-open" @click="emit('open-merge', item)">{{ t('gui.designer.top.notifications.merge.open') }}</button>
      </div>
    </li>
  </ul>
  <ul v-if="rows.length" class="notification-list">
    <!-- 0016-CH option B card row: [phase icon] [Flow / phase · progress · mode · AI (reason)] [open · stop]. -->
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
/** The mockup's marks: conflict (red), hourglass (waiting), spinner (catching up), merge-cross (failed). */
const MERGE_ICONS: Record<MergeRow['state'], string> = {
  needs_choice: 'M7 4l10 16 M17 4L7 20 M4 9h4 M16 15h4',
  failed: 'M5 3v4l6 6v8 M17 3v4l-6 6 M15 16l5 5 M20 16l-5 5',
  waiting: 'M7 3h10 M7 21h10 M8 3c0 5 8 5 8 9s-8 4-8 9 M16 3c0 5-8 5-8 9s8 4 8 9',
  catching_up: 'M12 3a9 9 0 1 0 9 9',
}
/** A person must choose (red row). */
const choice = (item: MergeRow) => item.state === 'needs_choice' || item.state === 'failed'
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

<style scoped>
/* flowgate.v02test.0042 (mockup v7): merge rows — red frame when a person must choose; a text [열기] button. */
.notification-list .notification-card.notification-merge.is-choice { border-color: rgba(255, 100, 120, 0.55); }
.notification-status.is-spinning svg { animation: notification-merge-spin 1s linear infinite; }
@keyframes notification-merge-spin { to { transform: rotate(360deg); } }
.notification-merge-open {
  height: 24px; padding: 0 8px; border: 1px solid #2b4566; border-radius: 7px; background: #13263d; color: #dbe8f6;
  font: inherit; font-size: 11.5px; font-weight: 700; white-space: nowrap; cursor: pointer;
}
.notification-merge-open:hover { filter: brightness(1.15); }
</style>
