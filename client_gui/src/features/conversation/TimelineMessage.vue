<template>
  <!-- flowgate.v02.0031 — one user / AI message of the timeline (R0001 §4). -->
  <div
    class="tl-msg"
    :class="[isUser ? 'tl-msg--user' : 'tl-msg--ai', { 'is-pending': pending, 'is-failed': failed }]"
    data-test="tl-message"
    :data-speaker="isUser ? 'user' : 'ai'"
    :data-seq="turn.seq || undefined"
  >
    <div class="tl-bubble">
      <div class="tl-meta">
        <span class="tl-speaker">
          <span class="tl-avatar" aria-hidden="true">{{ isUser ? '●' : '◆' }}</span>
          {{ isUser ? t('gui.conversation.message.user') : t('gui.conversation.message.ai') }}
        </span>
        <!-- 0293 R0001: the model name is a badge only; identity is participant_key. -->
        <span v-if="!isUser && turn.displayName" class="tl-provider" data-test="tl-provider" :title="turn.displayName">{{ turn.displayName }}</span>
        <span v-if="pending" class="tl-time">{{ t('gui.conversation.message.sending') }}</span>
        <span v-else-if="turn.createdAt" class="tl-time" :title="turn.createdAt">{{ formatTimelineTime(turn.createdAt) }}</span>
      </div>
      <div class="tl-text">{{ turn.body }}</div>
      <!-- Crossed talk (P0003 scenario 12): written without having seen the turn above. -->
      <p v-if="turn.staleSinceSeq" class="tl-note">{{ t('gui.conversation.message.stale', { seq: turn.staleSinceSeq }) }}</p>
      <p v-if="failed" class="tl-note tl-note--failed">
        {{ t('gui.conversation.message.failed') }}
        <button v-if="!readOnly" type="button" class="tl-retry" data-test="tl-retry" @click="emit('retry')">{{ t('gui.conversation.retry') }}</button>
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatTimelineTime } from './timeline'
import type { ConversationTurn } from './types'

const props = defineProps<{
  turn: ConversationTurn
  pending: boolean
  failed: boolean
  readOnly?: boolean
}>()

const emit = defineEmits<{ retry: [] }>()

const { t } = useI18n()

const isUser = computed(() => props.turn.speaker === 'user')
</script>

<style scoped>
.tl-msg {
  display: flex;
  width: 100%;
}

.tl-msg--user {
  justify-content: flex-end;
}

.tl-bubble {
  max-width: 92%;
  min-width: 0;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-control-surface);
  padding: 7px 8px 8px;
  color: var(--fg2-control-fg);
}

.tl-msg--user .tl-bubble {
  border-color: var(--fg2-status-info-border);
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.tl-msg.is-pending .tl-bubble {
  opacity: 0.7;
}

.tl-msg.is-failed .tl-bubble {
  border-color: var(--fg2-status-danger-border);
}

.tl-meta {
  min-height: 18px;
  display: flex;
  align-items: center;
  gap: 5px;
  margin-bottom: 3px;
  color: var(--fg2-muted);
  font-size: 10px;
  white-space: nowrap;
}

.tl-speaker {
  display: flex;
  align-items: center;
  gap: 4px;
  font-weight: 700;
  color: var(--fg2-text);
}

.tl-avatar {
  width: 15px;
  height: 15px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  background: var(--fg2-control-surface-strong);
  color: var(--fg2-status-info-fg);
  font-size: 8px;
}

.tl-msg--ai .tl-avatar {
  color: var(--fg2-violet);
}

.tl-provider {
  max-width: 110px;
  overflow: hidden;
  text-overflow: ellipsis;
  padding: 1px 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: var(--fg2-muted);
}

.tl-time {
  margin-left: auto;
  color: var(--fg2-muted-2);
}

.tl-text {
  font-size: 12px;
  line-height: 1.48;
  white-space: pre-wrap;
  word-break: break-word;
}

.tl-note {
  margin: 5px 0 0;
  font-size: 10px;
  color: var(--fg2-status-warning-fg);
}

.tl-note--failed {
  color: var(--fg2-status-danger-fg);
}

.tl-retry {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  text-decoration: underline;
  cursor: pointer;
}
</style>
