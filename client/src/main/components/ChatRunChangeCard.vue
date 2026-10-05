<template>
  <!-- flowgate.default.0670 T0004 — what one AI run changed (R0001 §8, mockup 03).
       Every number here is FlowGate's own start-tree ↔ end-tree measurement; nothing is
       taken from the model's reply. A run with no change has no card at all — the parent
       only mounts this for a stored summary (NR0003 §11.4). -->
  <div class="crc" :data-run-id="change.run_id">
    <div class="crc-hd">
      <AppIcon name="git-diff" />
      <span class="crc-title">{{ t('main.conversation_view.run_changes_title') }}</span>
      <span v-if="period" class="crc-period">{{ period }}</span>
    </div>
    <button type="button" class="crc-summary" @click="emit('open', null)">
      {{ t('main.conversation_view.run_changes_files', { n: change.files_changed }) }}
      <span v-if="change.insertions !== null && change.insertions !== undefined" class="crc-add">+{{ change.insertions }}</span>
      <span v-if="change.deletions !== null && change.deletions !== undefined" class="crc-del">−{{ change.deletions }}</span>
    </button>
    <ul class="crc-files">
      <li v-for="file in change.files" :key="file.path">
        <button type="button" class="crc-file" :title="file.path" @click="emit('open', file.path)">
          <span class="crc-status">{{ file.status }}</span>
          <span class="crc-path">{{ file.path }}</span>
          <span class="crc-add">{{ file.insertions === null || file.insertions === undefined ? '?' : `+${file.insertions}` }}</span>
          <span class="crc-del">{{ file.deletions === null || file.deletions === undefined ? '?' : `−${file.deletions}` }}</span>
        </button>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { runClock, type RunChange } from './chatActivityTypes'

const props = defineProps<{ change: RunChange }>()
const emit = defineEmits<{ open: [path: string | null] }>()
const { t } = useI18n()

const period = computed(() => {
  const start = runClock(props.change.run_started_at)
  const end = runClock(props.change.run_finished_at)
  if (!start || !end) return ''
  return t('main.conversation_view.run_changes_period', { start, end })
})
</script>

<style scoped>
.crc {
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: #fff;
  font-size: 0.9rem;
  line-height: 1.5;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
}
.crc-hd {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  padding: 8px 12px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}
.crc-title { font-weight: 700; }
.crc-period { margin-left: auto; color: #475569; font-variant-numeric: tabular-nums; }
.crc-summary {
  display: flex;
  gap: 8px;
  width: 100%;
  padding: 7px 12px;
  border: none;
  background: #f8fafc;
  color: var(--text, #0f172a);
  font-size: 0.9rem;
  cursor: pointer;
  font-weight: 600;
  text-align: left;
}
.crc-files { list-style: none; margin: 0; padding: 0; }
.crc-file {
  display: grid;
  grid-template-columns: 20px minmax(0, 1fr) 60px 60px;
  gap: 6px;
  width: 100%;
  padding: 6px 12px;
  border: none;
  border-top: 1px solid #f1f5f9;
  background: transparent;
  cursor: pointer;
  text-align: left;
  color: var(--text, #0f172a);
  font-size: 0.86rem;
}
.crc-file:hover { background: #f1f5ff; }
.crc-status { color: #475569; font-weight: 700; }
.crc-path { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; text-align: left; }
.crc-add { color: var(--success, #15803d); font-variant-numeric: tabular-nums; text-align: right; }
.crc-del { color: var(--danger, #b91c1c); font-variant-numeric: tabular-nums; text-align: right; }
</style>
