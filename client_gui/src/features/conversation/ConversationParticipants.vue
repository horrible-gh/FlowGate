<template>
  <!-- flowgate.v02.0031 — compact strip of who is in the conversation and how far each has
       read (R0001 §7, D0002 §6). Filled by the turns call itself; no separate endpoint.
       flowgate.v02.0047: the same chips, right under the title. -->
  <div v-if="participants.length > 0" class="conv-people" data-test="conv-participants">
    <span class="conv-people-label">{{ t('gui.conversation.participants.label') }}</span>
    <span
      v-for="p in participants"
      :key="p.key"
      class="conv-person"
      :class="{ 'conv-person--ai': p.kind !== 'user' }"
      data-test="conv-participant"
      :title="t('gui.conversation.participants.position', { name: p.displayName || p.key, seq: p.lastReadSeq })"
    >
      <span aria-hidden="true">{{ p.kind === 'user' ? '●' : '◆' }}</span>
      <span class="conv-person-name">{{ p.displayName || p.key }}</span>
      <em>#{{ p.lastReadSeq }}</em>
    </span>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { ConversationParticipant } from './types'

defineProps<{ participants: readonly ConversationParticipant[] }>()

const { t } = useI18n()
</script>

<style scoped>
.conv-people {
  flex: 0 0 auto;
  min-height: 29px;
  padding: 5px 8px;
  display: flex;
  align-items: center;
  gap: 5px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-control-surface-flat);
  overflow: hidden;
}

.conv-people-label {
  flex: 0 0 auto;
  font-size: 10px;
  color: var(--fg2-muted-2);
}

.conv-person {
  height: 19px;
  min-width: 0;
  padding: 0 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-pill);
  color: var(--fg2-muted);
  font-size: 10px;
  display: flex;
  align-items: center;
  gap: 3px;
}

.conv-person--ai {
  color: var(--fg2-violet);
}

.conv-person-name {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-person em {
  font-style: normal;
  color: var(--fg2-muted-2);
}
</style>
