<template>
  <!-- flowgate.default.0670 T0004 — one chat command request (R0001 §3/§5, mockups 01/02).
       The full command is always shown verbatim: the approval screen never hides or
       abbreviates what will actually run (R0001 §3). Rendered inside the AI bubble's
       max-width, never as a full-width panel (NR0003 §12). -->
  <div class="ccmd" :class="`ccmd--${command.status}`" :data-request-id="command.request_id">
    <div class="ccmd-hd">
      <AppIcon :name="headIcon" :spin="command.status === 'running'" />
      <span class="ccmd-title">{{ isRequest ? t('main.conversation_view.command_request_title') : t('main.conversation_view.command_result_title') }}</span>
      <span class="ccmd-badge">{{ t(`main.conversation_view.command_policy_${command.policy}`) }}</span>
      <span class="ccmd-badge ccmd-badge--muted">{{ t(`main.conversation_view.command_category_${command.category}`) }}</span>
      <span class="ccmd-status">{{ statusText }}</span>
    </div>
    <pre class="ccmd-cmd">{{ command.command }}</pre>
    <div class="ccmd-meta">
      <span><em>cwd</em> {{ cwdText }}</span>
      <span><em>timeout</em> {{ command.timeout_seconds }}s</span>
      <span v-if="command.provider_name">{{ t('main.conversation_view.command_requested_by', { name: command.provider_name }) }}</span>
    </div>
    <p v-if="command.high_impact" class="ccmd-warn">
      <AppIcon name="warning" />
      {{ t('main.conversation_view.command_high_impact') }}
    </p>
    <p v-if="command.status === 'rejected'" class="ccmd-note">
      {{ command.decision_source === 'policy'
        ? t('main.conversation_view.command_rejected_by_policy')
        : t('main.conversation_view.command_rejected_by_user') }}
    </p>
    <p v-else-if="command.error_code && command.status !== 'succeeded'" class="ccmd-note">
      {{ command.error_code }}
    </p>
    <template v-if="hasOutput">
      <pre v-if="command.stdout_tail" class="ccmd-out">{{ command.stdout_tail }}</pre>
      <pre v-if="command.stderr_tail" class="ccmd-out ccmd-out--err">{{ command.stderr_tail }}</pre>
    </template>
    <div v-if="command.status === 'pending_approval' && canDecide" class="ccmd-actions">
      <button type="button" class="ccmd-btn ccmd-btn--run" :disabled="deciding" @click="emit('decide', 'approve')">
        <AppIcon :name="deciding ? 'spinner' : 'play'" :spin="deciding" />
        {{ t('main.conversation_view.command_approve') }}
      </button>
      <button type="button" class="ccmd-btn" :disabled="deciding" @click="emit('decide', 'reject')">
        <AppIcon name="prohibit" />
        {{ t('main.conversation_view.command_reject') }}
      </button>
    </div>
    <div v-else-if="command.status === 'running' && canDecide" class="ccmd-actions">
      <button type="button" class="ccmd-btn" :disabled="deciding" @click="emit('decide', 'cancel')">
        <AppIcon name="stop-circle" />
        {{ t('main.conversation_view.command_cancel') }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import type { ChatCommand } from './chatActivityTypes'

const props = defineProps<{
  command: ChatCommand
  canDecide?: boolean
  deciding?: boolean
}>()

const emit = defineEmits<{ decide: [decision: 'approve' | 'reject' | 'cancel'] }>()

const { t } = useI18n()

const isRequest = computed(() => ['pending_approval', 'approved'].includes(props.command.status))
const hasOutput = computed(() => Boolean(props.command.stdout_tail || props.command.stderr_tail))
const cwdText = computed(() =>
  !props.command.cwd || props.command.cwd === '.' ? '<worktree>' : `<worktree>/${props.command.cwd}`,
)
const headIcon = computed(() => {
  switch (props.command.status) {
    case 'running':
      return 'spinner'
    case 'succeeded':
      return 'check-circle'
    case 'failed':
    case 'timed_out':
      return 'x-circle'
    case 'rejected':
    case 'cancelled':
      return 'prohibit'
    default:
      return 'terminal'
  }
})
const statusText = computed(() => {
  const c = props.command
  const label = t(`main.conversation_view.command_status_${c.status}`)
  const parts = [label]
  if (c.exit_code !== null && c.exit_code !== undefined) {
    parts.push(t('main.conversation_view.command_exit_code', { code: c.exit_code }))
  }
  if (typeof c.duration_ms === 'number') parts.push(`${(c.duration_ms / 1000).toFixed(1)}s`)
  return parts.join(' · ')
})
</script>

<style scoped>
.ccmd {
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 8px;
  background: #fff;
  padding: 10px 12px;
  font-size: 0.9rem;
  line-height: 1.5;
  max-width: 100%;
  box-sizing: border-box;
  overflow: hidden;
}
.ccmd--pending_approval { border-color: #f59e0b; background: #fffbeb; }
.ccmd--succeeded { border-color: #bbf7d0; }
.ccmd--failed,
.ccmd--timed_out { border-color: #fecaca; }
.ccmd-hd { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.ccmd-title { font-weight: 700; }
.ccmd-badge {
  font-size: 0.78rem;
  padding: 1px 8px;
  border-radius: 999px;
  background: #eef2ff;
  color: #3730a3;
}
.ccmd-badge--muted { background: #f1f5f9; color: #334155; }
.ccmd-status { margin-left: auto; color: #334155; font-weight: 600; font-variant-numeric: tabular-nums; }
.ccmd-cmd {
  margin: 8px 0 6px;
  padding: 8px 10px;
  background: #0f172a;
  color: #f8fafc;
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 0.92rem;
  font-weight: 500;
  line-height: 1.5;
}
.ccmd-meta { display: flex; gap: 14px; flex-wrap: wrap; color: #334155; }
.ccmd-meta em { font-style: normal; font-weight: 600; margin-right: 3px; }
.ccmd-warn { margin: 6px 0 0; color: #b45309; display: flex; gap: 4px; align-items: center; }
.ccmd-note { margin: 6px 0 0; color: #334155; }
.ccmd-out {
  margin: 6px 0 0;
  max-height: 240px;
  overflow: auto;
  padding: 8px 10px;
  background: #f8fafc;
  color: #0f172a;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  white-space: pre-wrap;
  word-break: break-all;
  font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace;
  font-size: 0.86rem;
  line-height: 1.5;
}
.ccmd-out--err { color: #b91c1c; }
.ccmd-actions { display: flex; gap: 6px; margin-top: 8px; }
.ccmd-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 5px 14px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: #fff;
  cursor: pointer;
  font-size: 0.88rem;
}
.ccmd-btn--run { background: #2563eb; border-color: #2563eb; color: #fff; }
.ccmd-btn:disabled { opacity: 0.6; cursor: default; }
</style>
