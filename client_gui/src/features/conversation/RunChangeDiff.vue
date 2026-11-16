<template>
  <!-- flowgate.v02.0032 — the stored diff of one run change file in its modeless change window (D0006 §3.8.3, §6.1). Content comes from the run start / run end trees
       the server recorded, never from the current worktree.
       flowgate.v02test.0031 B — mode 'full' shows the stored run end text (run start text for a deleted file) with line numbers, no add/del colours. -->
  <div class="rc-diff" data-test="rc-diff" :data-state="fileDiff.status" :data-mode="mode">
    <div class="rc-diff-head">
      <span class="rc-diff-path" :title="fileDiff.path">{{ fileDiff.path }}</span>
      <span v-if="full" class="rc-diff-basis" data-test="rc-diff-basis" :data-basis="full.basis">
        {{ t(full.basis === 'after' ? 'gui.conversation.diff.full_basis_after' : 'gui.conversation.diff.full_basis_before_delete') }}
      </span>
    </div>
    <p v-if="fileDiff.status === 'loading'" class="rc-diff-note" data-test="rc-diff-loading">{{ t('gui.conversation.diff.loading') }}</p>
    <p v-else-if="fileDiff.status === 'error' || fullMissing" class="rc-diff-note rc-diff-note--error" data-test="rc-diff-error">
      {{ t('gui.conversation.diff.failed') }}
    </p>
    <p v-else-if="unavailable" class="rc-diff-note" data-test="rc-diff-unavailable">{{ t(`gui.conversation.diff.${unavailable}`) }}</p>
    <template v-else-if="full">
      <pre class="rc-diff-lines rc-diff-lines--full" data-test="rc-diff-full"><span
        v-for="(text, i) in fullShown"
        :key="i"
        class="rc-diff-full-line"
        data-test="rc-diff-full-line"
      ><span class="rc-diff-no" aria-hidden="true">{{ i + 1 }}</span>{{ text }}</span></pre>
      <p v-if="fullHidden > 0" class="rc-diff-note rc-diff-more-row" data-test="rc-diff-more">
        {{ t('gui.conversation.diff.more_lines', { n: fullHidden }) }}
        <button type="button" class="rc-diff-show-more" data-test="rc-diff-show-more" @click="fullLimit += MAX_SHOWN_LINES">
          {{ t('gui.conversation.diff.show_more') }}
        </button>
      </p>
    </template>
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
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { diffLines, diffUnavailableReason, type OpenFileDiff } from './changeDiff'

/** Bound the rendered DOM even when a stored file contains many lines. */
const MAX_SHOWN_LINES = 2000

const props = withDefaults(defineProps<{
  fileDiff: OpenFileDiff
  mode?: 'changes' | 'full'
}>(), { mode: 'changes' })

const { t } = useI18n()

const unavailable = computed(() => (props.fileDiff.diff ? diffUnavailableReason(props.fileDiff.diff) : null))

// mode 'changes' — unchanged diffLines output (C card replaces it with the context projection).
const lines = computed(() => {
  const diff = props.fileDiff.diff
  if (props.mode !== 'changes' || !diff || unavailable.value) return []
  const result = diffLines(diff.old.content, diff.new.content)
  return result.every((line) => line.kind === 'context') ? [] : result
})
const shown = computed(() => lines.value.slice(0, MAX_SHOWN_LINES))
const hidden = computed(() => Math.max(0, lines.value.length - MAX_SHOWN_LINES))

/** Same line split as the diff: CRLF is one break and a final newline does not start a line. */
function splitText(text: string): string[] {
  if (text === '') return []
  const out = text.replace(/\r\n/g, '\n').split('\n')
  if (out[out.length - 1] === '') out.pop()
  return out
}

// mode 'full' — the stored run end side, or the run start side when the run deleted the file.
// Never the current worktree. A side that claims to exist but carries no text is not shown.
const fullSide = computed(() => {
  const diff = props.fileDiff.diff
  if (props.mode !== 'full' || props.fileDiff.status !== 'ready' || !diff || unavailable.value) return null
  return diff.new.exists
    ? { basis: 'after' as const, side: diff.new }
    : { basis: 'before_delete' as const, side: diff.old }
})
const fullMissing = computed(() => fullSide.value !== null && fullSide.value.side.exists && fullSide.value.side.content == null)
const full = computed(() => {
  const picked = fullSide.value
  if (!picked || fullMissing.value) return null
  return { basis: picked.basis, lines: splitText(picked.side.content ?? '') }
})
const fullLimit = ref(MAX_SHOWN_LINES)
const fullShown = computed(() => (full.value ? full.value.lines.slice(0, fullLimit.value) : []))
const fullHidden = computed(() => (full.value ? Math.max(0, full.value.lines.length - fullLimit.value) : 0))
watch(() => [props.fileDiff.runId, props.fileDiff.path, props.fileDiff.diff, props.mode], () => {
  fullLimit.value = MAX_SHOWN_LINES
})
</script>

<style scoped>
.rc-diff {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  overflow: hidden;
}

.rc-diff-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 6px;
  flex: 0 0 auto;
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

.rc-diff-basis {
  flex: 0 0 auto;
  padding: 0 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
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

.rc-diff-more-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.rc-diff-show-more {
  padding: 1px 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-text);
  font: inherit;
  cursor: pointer;
}

.rc-diff-lines {
  margin: 0;
  flex: 1;
  min-height: 0;
  overflow: auto;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--fg2-text);
}

.rc-diff-line,
.rc-diff-full-line {
  display: block;
  padding: 0 6px;
  white-space: pre;
}

.rc-diff-no {
  display: inline-block;
  min-width: 4ch;
  margin-right: 10px;
  text-align: right;
  color: var(--fg2-muted);
  user-select: none;
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
