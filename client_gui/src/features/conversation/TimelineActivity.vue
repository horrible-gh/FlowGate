<template>
  <!-- flowgate.v02.0031 — a non-message entry of the timeline: a command request (R0001 §5),
       a work change (§6) or a status event (§3). It is recorded where it happened and scrolls
       up with the messages after it; nothing here is pinned above the composer.
       flowgate.v02.0032 — the command card shows the server state and its reason code
       (D0006 §6.1) and the run change card lists its files, each opening the stored diff. -->
  <div
    v-if="activity.kind === 'command'"
    class="tl-act tl-act--command"
    data-test="tl-command"
    :data-state="activity.state"
  >
    <div class="tl-act-head">
      <span aria-hidden="true">⌘</span>
      <span>{{ t('gui.conversation.command.title') }}</span>
      <span class="tl-act-state" data-test="tl-command-state">{{ commandStateText }}</span>
    </div>
    <div class="tl-act-body">
      <div class="tl-command-line" :title="commandTitle">{{ activity.command }}</div>
      <p v-if="commandReason" class="tl-act-detail" data-test="tl-command-reason">{{ commandReason }}</p>
      <p v-if="activity.detail" class="tl-act-detail">{{ activity.detail }}</p>
      <div v-if="decidable || busy" class="tl-act-actions">
        <button
          type="button"
          class="tl-act-btn tl-act-btn--reject"
          data-test="tl-command-reject"
          :disabled="busy || readOnly"
          @click="emit('decide', 'reject')"
        >
          {{ t('gui.conversation.command.reject') }}
        </button>
        <button
          type="button"
          class="tl-act-btn tl-act-btn--ok"
          data-test="tl-command-approve"
          :disabled="busy || readOnly"
          @click="emit('decide', 'approve')"
        >
          {{ t('gui.conversation.command.approve') }}
        </button>
      </div>
    </div>
  </div>

  <div v-else-if="activity.kind === 'change'" class="tl-act tl-act--change" data-test="tl-change">
    <div class="tl-act-head">
      <span aria-hidden="true">±</span>
      <span>{{ t('gui.conversation.change.title') }}</span>
      <span class="tl-act-state" data-test="tl-change-count">{{ t('gui.conversation.change.files', { n: summary.fileCount }) }}</span>
    </div>
    <div class="tl-act-body">
      <div class="tl-change-stats">
        <span class="tl-stat" data-test="tl-change-added">+{{ summary.added ?? '—' }}</span>
        <span class="tl-stat tl-stat--del" data-test="tl-change-removed">−{{ summary.removed ?? '—' }}</span>
        <span v-if="summary.firstName" class="tl-change-name">
          {{ summary.fileCount > 1 ? t('gui.conversation.change.and_more', { name: summary.firstName, n: summary.fileCount - 1 }) : summary.firstName }}
        </span>
      </div>
      <ul class="tl-change-files" data-test="tl-change-files">
        <li v-for="file in activity.files" :key="file.path">
          <button
            type="button"
            class="tl-change-file"
            data-test="tl-change-file"
            :data-path="file.path"
            :aria-expanded="isOpen(file.path)"
            :title="t('gui.conversation.change.open_file', { path: file.path })"
            @click="emit('open-file', activity.runId, file.path)"
          >
            <span class="tl-file-status" :data-status="file.status ?? ''">{{ file.status ?? '?' }}</span>
            <span class="tl-file-path">{{ file.path }}</span>
            <span v-if="file.insertions == null && file.deletions == null" class="tl-file-nums">{{ t('gui.conversation.change.no_stats') }}</span>
            <span v-else class="tl-file-nums">+{{ file.insertions ?? 0 }} −{{ file.deletions ?? 0 }}</span>
          </button>
          <RunChangeDiff v-if="isOpen(file.path) && fileDiff" :file-diff="fileDiff" @close="emit('close-file')" />
        </li>
      </ul>
      <div class="tl-act-actions">
        <button type="button" class="tl-act-btn" data-test="tl-change-open" @click="emit('open-change')">
          {{ t('gui.conversation.change.open') }}
        </button>
      </div>
    </div>
  </div>

  <div v-else class="tl-status" :class="`tl-status--${activity.tone}`" data-test="tl-status" role="status">
    <span class="tl-status-text">{{ statusText }}</span>
    <span v-if="activity.createdAt" class="tl-status-time">{{ formatTimelineTime(activity.createdAt) }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import RunChangeDiff from './RunChangeDiff.vue'
import type { OpenFileDiff } from './conversationSession'
import { formatTimelineTime, summarizeChange } from './timeline'
import type { CommandDecision, ConversationActivity } from './types'

const props = defineProps<{
  activity: ConversationActivity
  /** A decision for this command is in flight. */
  busy?: boolean
  readOnly?: boolean
  /** The open run change file diff, when it belongs to this card. */
  fileDiff?: OpenFileDiff | null
}>()

const emit = defineEmits<{
  decide: [decision: CommandDecision]
  'open-change': []
  'open-file': [runId: string, path: string]
  'close-file': []
}>()

const { t, te } = useI18n()

const summary = computed(() =>
  summarizeChange(props.activity.kind === 'change' ? props.activity : { files: [], filesChanged: 0, insertions: null, deletions: null }),
)

/** [승인]/[거절] only for a pending request this user was not refused on (D0006 §6.1, §6.2). */
const decidable = computed(
  () => props.activity.kind === 'command' && props.activity.state === 'pending_approval' && !props.activity.decisionForbidden,
)

const commandTitle = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return ''
  return a.cwd ? `${a.command}\n(${a.cwd})` : a.command
})

const commandStateText = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return ''
  const key = `gui.conversation.command.state.${a.state}`
  return te(key) ? t(key) : a.state
})

/** The card's wording comes from the server's state and reason code, never from a guess. */
const commandReason = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return ''
  if (a.state === 'rejected' && a.rejectedBy) return t(`gui.conversation.command.rejected_by.${a.rejectedBy}`)
  if (a.errorCode) {
    const key = `gui.conversation.command.reason.${a.errorCode}`
    return te(key) ? t(key) : a.errorCode
  }
  if ((a.state === 'failed' || a.state === 'succeeded') && a.exitCode != null) return t('gui.conversation.command.exit_code', { code: a.exitCode })
  return ''
})

function isOpen(path: string): boolean {
  const open = props.fileDiff
  return props.activity.kind === 'change' && !!open && open.runId === props.activity.runId && open.path === path
}

const statusText = computed(() => {
  const a = props.activity
  if (a.kind !== 'status') return ''
  const key = a.code ? `gui.conversation.status.${a.code}` : ''
  const known = key && te(key) ? t(key) : ''
  if (known && a.text) return `${known} (${a.text})`
  return known || a.text || a.code || ''
})
</script>

<style scoped>
.tl-act {
  margin: 0 4px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface-flat);
  overflow: hidden;
}

.tl-act--command {
  border-color: var(--fg2-status-warning-border);
  background: var(--fg2-status-warning-bg);
}

.tl-act--change {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
}

.tl-act-head {
  height: 29px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 6px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 11px;
  font-weight: 700;
}

.tl-act--command .tl-act-head {
  color: var(--fg2-status-warning-fg);
}

.tl-act--change .tl-act-head {
  color: var(--fg2-status-success-fg);
}

.tl-act-state {
  margin-left: auto;
  font-size: 10px;
  font-weight: 600;
  opacity: 0.85;
}

.tl-act-body {
  padding: 7px 8px;
  font-size: 11px;
  line-height: 1.42;
  color: var(--fg2-muted);
}

.tl-command-line {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
  color: var(--fg2-text);
  background: var(--fg2-bg);
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  padding: 5px 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-act-detail {
  margin: 5px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
}

.tl-act-actions {
  display: flex;
  justify-content: flex-end;
  gap: 5px;
  margin-top: 6px;
}

.tl-act-btn {
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--fg2-status-info-border);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-size: 10.5px;
  cursor: pointer;
}

.tl-act-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.tl-act-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.tl-act-btn--ok {
  border-color: var(--fg2-status-success-border);
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.tl-act-btn--reject {
  border-color: var(--fg2-status-danger-border);
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.tl-change-stats {
  display: flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}

.tl-stat {
  flex: 0 0 auto;
  padding: 2px 5px;
  border-radius: 4px;
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.tl-stat--del {
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}

.tl-change-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-change-files {
  list-style: none;
  margin: 5px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.tl-change-file {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-text);
  font: inherit;
  font-size: 10.5px;
  text-align: left;
  cursor: pointer;
}

.tl-change-file:hover,
.tl-change-file[aria-expanded='true'] {
  background: var(--fg2-bg);
}

.tl-change-file:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.tl-file-status {
  flex: 0 0 auto;
  width: 12px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-weight: 700;
  color: var(--fg2-status-success-fg);
}

.tl-file-status[data-status='D'] {
  color: var(--fg2-status-danger-fg);
}

.tl-file-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-file-nums {
  flex: 0 0 auto;
  color: var(--fg2-muted);
}

.tl-status {
  align-self: center;
  max-width: 92%;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 9px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-pill);
  font-size: 10.5px;
  color: var(--fg2-muted);
}

.tl-status--success {
  color: var(--fg2-status-success-fg);
  border-color: var(--fg2-status-success-border);
}

.tl-status--warning {
  color: var(--fg2-status-warning-fg);
  border-color: var(--fg2-status-warning-border);
}

.tl-status--danger {
  color: var(--fg2-status-danger-fg);
  border-color: var(--fg2-status-danger-border);
}

.tl-status-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.tl-status-time {
  flex: 0 0 auto;
  color: var(--fg2-muted-2);
}
</style>
