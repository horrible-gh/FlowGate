<template>
  <!-- flowgate.v02.0032 — the stored diff of one run change file, opened under its row in the
       run change card (D0006 §3.8.3, §6.1). Content comes from the run start / run end trees
       the server recorded, never from the current worktree. -->
  <div class="rc-diff" data-test="rc-diff" :data-state="fileDiff.status">
    <div class="rc-diff-head">
      <span class="rc-diff-path" :title="fileDiff.path">{{ fileDiff.path }}</span>
      <button type="button" class="rc-diff-close" data-test="rc-diff-close" :aria-label="t('gui.conversation.close')" @click="emit('close')">×</button>
    </div>
    <p v-if="fileDiff.status === 'loading'" class="rc-diff-note" data-test="rc-diff-loading">{{ t('gui.conversation.diff.loading') }}</p>
    <p v-else-if="fileDiff.status === 'error'" class="rc-diff-note rc-diff-note--error" data-test="rc-diff-error">
      {{ t('gui.conversation.diff.failed') }}
    </p>
    <p v-else-if="unavailable" class="rc-diff-note" data-test="rc-diff-unavailable">{{ t(`gui.conversation.diff.${unavailable}`) }}</p>
    <p v-else-if="lines.length === 0" class="rc-diff-note" data-test="rc-diff-empty">{{ t('gui.conversation.diff.empty') }}</p>
    <template v-else>
      <pre class="rc-diff-lines" data-test="rc-diff-lines"><span
        v-for="(line, i) in shown"
        :key="i"
        class="rc-diff-line"
        :class="`rc-diff-line--${line.kind}`"
        :data-kind="line.kind"
      >{{ line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' ' }}{{ line.text }}</span></pre>
      <p v-if="hidden > 0" class="rc-diff-note" data-test="rc-diff-more">{{ t('gui.conversation.diff.more_lines', { n: hidden }) }}</p>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { diffLines, diffUnavailableReason } from './changeDiff'
import type { OpenFileDiff } from './conversationSession'

/** Lines rendered at most; the rest is counted, not drawn (a chat card is no file viewer). */
const MAX_SHOWN_LINES = 2000

const props = defineProps<{
  fileDiff: OpenFileDiff
}>()

const emit = defineEmits<{
  close: []
}>()

const { t } = useI18n()

const unavailable = computed(() => (props.fileDiff.diff ? diffUnavailableReason(props.fileDiff.diff) : null))
const lines = computed(() => {
  const diff = props.fileDiff.diff
  if (!diff || unavailable.value) return []
  return diffLines(diff.old.content, diff.new.content)
})
const shown = computed(() => lines.value.slice(0, MAX_SHOWN_LINES))
const hidden = computed(() => Math.max(0, lines.value.length - MAX_SHOWN_LINES))
</script>

<style scoped>
.rc-diff {
  margin: 3px 0 5px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  overflow: hidden;
}

.rc-diff-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 4px 2px 6px;
  border-bottom: 1px solid var(--fg2-line);
  font-size: 10.5px;
  color: var(--fg2-muted);
}

.rc-diff-path {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rc-diff-close {
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  cursor: pointer;
}

.rc-diff-close:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.rc-diff-note {
  margin: 0;
  padding: 5px 6px;
  font-size: 10.5px;
  color: var(--fg2-muted);
}

.rc-diff-note--error {
  color: var(--fg2-status-danger-fg);
}

.rc-diff-lines {
  margin: 0;
  max-height: 260px;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--fg2-text);
}

.rc-diff-line {
  display: block;
  padding: 0 6px;
  white-space: pre;
}

.rc-diff-line--add {
  background: var(--fg2-status-success-bg);
  color: var(--fg2-status-success-fg);
}

.rc-diff-line--del {
  background: var(--fg2-status-danger-bg);
  color: var(--fg2-status-danger-fg);
}
</style>
