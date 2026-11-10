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
      <div class="tl-act-actions">
        <!-- flowgate.v02.0150 — every command card opens its detail in place (one column),
             whatever its state and also read-only: the full command, its output and exit. -->
        <button
          type="button"
          class="tl-act-btn tl-act-btn--detail"
          data-test="tl-command-detail-toggle"
          :aria-expanded="detailOpen"
          :aria-controls="detailId"
          @click="detailOpen = !detailOpen"
        >
          {{ detailOpen ? t('gui.conversation.command.detail.hide') : t('gui.conversation.command.detail.show') }}
        </button>
        <template v-if="decidable || busy">
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
        </template>
      </div>
      <div v-if="detailOpen" :id="detailId" class="tl-cmd-detail" data-test="tl-command-detail">
        <dl class="tl-cmd-meta">
          <dt>{{ t('gui.conversation.command.detail.command') }}</dt>
          <dd><pre class="tl-cmd-pre" data-test="tl-command-detail-command">{{ activity.command }}</pre></dd>
          <template v-if="activity.cwd">
            <dt>{{ t('gui.conversation.command.detail.cwd') }}</dt>
            <dd class="tl-cmd-mono" data-test="tl-command-detail-cwd">{{ activity.cwd }}</dd>
          </template>
          <dt>{{ t('gui.conversation.command.detail.state') }}</dt>
          <dd data-test="tl-command-detail-state">{{ commandStateText }}</dd>
          <dt>{{ t('gui.conversation.command.detail.exit_code') }}</dt>
          <dd class="tl-cmd-mono" data-test="tl-command-detail-exit">
            {{ activity.exitCode != null ? activity.exitCode : t('gui.conversation.command.detail.none') }}
          </dd>
          <template v-if="activity.durationMs != null">
            <dt>{{ t('gui.conversation.command.detail.duration') }}</dt>
            <dd data-test="tl-command-detail-duration">{{ formatDuration(activity.durationMs) }}</dd>
          </template>
          <template v-if="errorLines.length">
            <dt>{{ t('gui.conversation.command.detail.error') }}</dt>
            <dd class="tl-cmd-error" data-test="tl-command-detail-error">
              <div v-for="line in errorLines" :key="line">{{ line }}</div>
            </dd>
          </template>
        </dl>
        <p v-if="outputNotice" class="tl-cmd-notice" data-test="tl-command-detail-no-output">{{ outputNotice }}</p>
        <template v-else>
          <section v-for="stream in streams" :key="stream.name" class="tl-cmd-stream" :data-test="`tl-command-detail-${stream.name}`">
            <div class="tl-cmd-stream-head">
              <span>{{ stream.name }}</span>
              <span v-if="stream.truncated" class="tl-cmd-cut" :data-test="`tl-command-detail-${stream.name}-truncated`">
                {{ t('gui.conversation.command.detail.truncated') }}
              </span>
            </div>
            <pre v-if="stream.text" class="tl-cmd-pre tl-cmd-out">{{ stream.text }}</pre>
            <p v-else class="tl-cmd-empty">{{ t('gui.conversation.command.detail.empty') }}</p>
          </section>
        </template>
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
            aria-haspopup="dialog"
            :title="t('gui.conversation.change.open_file', { path: file.path })"
            @click="emit('open-file', activity.runId, file.path)"
          >
            <span class="tl-file-status" :data-status="file.status ?? ''">{{ file.status ?? '?' }}</span>
            <span class="tl-file-path">{{ file.path }}</span>
            <span v-if="file.insertions == null && file.deletions == null" class="tl-file-nums">{{ t('gui.conversation.change.no_stats') }}</span>
            <span v-else class="tl-file-nums">+{{ file.insertions ?? 0 }} −{{ file.deletions ?? 0 }}</span>
          </button>
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
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatTimelineTime, summarizeChange } from './timeline'
import type { CommandDecision, TimelineActivityEntry } from './types'

const props = defineProps<{
  activity: TimelineActivityEntry
  /** A decision for this command is in flight. */
  busy?: boolean
  readOnly?: boolean
}>()

const emit = defineEmits<{
  decide: [decision: CommandDecision]
  'open-change': []
  'open-file': [runId: string, path: string]
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

const detailOpen = ref(false)
const detailId = computed(() => `tl-command-detail-${props.activity.id}`)

/** Every reason the server gave for the outcome, the raw code next to its wording. */
const errorLines = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return []
  const lines: string[] = []
  if (a.state === 'rejected' && a.rejectedBy) lines.push(t(`gui.conversation.command.rejected_by.${a.rejectedBy}`))
  if (a.timedOut) lines.push(t('gui.conversation.command.detail.timed_out'))
  if (a.errorCode) {
    const key = `gui.conversation.command.reason.${a.errorCode}`
    lines.push(te(key) ? `${t(key)} (${a.errorCode})` : a.errorCode)
  }
  return lines
})

/** A command that never started has no output, which is not the same as an empty output. */
const outputNotice = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return ''
  if (a.state === 'running') return t('gui.conversation.command.detail.running')
  if (a.startedAt) return ''
  if (a.state === 'pending_approval' || a.state === 'approving' || a.state === 'rejecting' || a.state === 'approved') {
    return t('gui.conversation.command.detail.not_started')
  }
  // A failure before the start can still have stored its message on stderr.
  if (a.stdout || a.stderr) return ''
  return t('gui.conversation.command.detail.not_run')
})

const streams = computed(() => {
  const a = props.activity
  if (a.kind !== 'command') return []
  return [
    { name: 'stdout', text: a.stdout ?? '', truncated: !!a.stdoutTruncated },
    { name: 'stderr', text: a.stderr ?? '', truncated: !!a.stderrTruncated },
  ]
})

function formatDuration(ms: number): string {
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)} s`
}

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
  /* flowgate.v02.0147 T0005: a direct child of the column-flex .conv-log must never be
     shrunk below its content height (overflow:hidden would clip it to ~2px). */
  flex-shrink: 0;
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

.tl-act-btn--detail {
  margin-right: auto;
  border-color: var(--fg2-line-strong);
  background: var(--fg2-bg);
  color: var(--fg2-text);
}

.tl-act-btn--detail[aria-expanded='true'] {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.tl-cmd-detail {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid var(--fg2-line);
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
}

.tl-cmd-meta {
  margin: 0;
  display: grid;
  grid-template-columns: max-content minmax(0, 1fr);
  gap: 3px 8px;
  align-items: baseline;
}

.tl-cmd-meta dt {
  font-weight: 600;
  color: var(--fg2-muted);
  white-space: nowrap;
}

.tl-cmd-meta dd {
  margin: 0;
  min-width: 0;
  color: var(--fg2-text);
  overflow-wrap: anywhere;
}

.tl-cmd-mono,
.tl-cmd-pre {
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
}

.tl-cmd-pre {
  margin: 0;
  padding: 5px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.tl-cmd-out {
  max-height: 240px;
  overflow: auto;
}

.tl-cmd-meta dd.tl-cmd-error {
  color: var(--fg2-status-danger-fg);
}

.tl-cmd-stream {
  min-width: 0;
}

.tl-cmd-stream-head {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 2px;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10px;
  font-weight: 700;
  color: var(--fg2-muted);
}

.tl-cmd-cut {
  font-family: inherit;
  font-weight: 600;
  color: var(--fg2-status-warning-fg);
}

.tl-cmd-empty,
.tl-cmd-notice {
  margin: 0;
  color: var(--fg2-muted);
  font-style: italic;
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
