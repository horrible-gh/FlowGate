<template>
  <section v-if="target" class="gcd" data-test="git-center-diff" :data-state="session.state.load" :data-path="target.path">
    <header class="gcd-head">
      <div class="gcd-title">
        <span class="gcd-path" data-test="git-center-diff-path" :title="target.path">◧ {{ target.path }}</span>
        <span v-if="letter" class="gcd-letter" data-test="git-center-diff-letter">{{ letter }}</span>
      </div>
      <div class="gcd-sub">
        <span class="gcd-compare" data-test="git-center-diff-compare">{{ compareLabel }}</span>
        <span class="gcd-grow" />
        <button type="button" class="gcd-btn" data-test="git-center-diff-reload" @click="session.reload()">⟳ {{ t('gui.git.centerDiff.reload') }}</button>
        <button type="button" class="gcd-btn" data-test="git-center-diff-close" @click="session.close()">✕ {{ t('gui.git.centerDiff.close') }}</button>
      </div>
    </header>
    <div class="gcd-body">
      <p v-if="session.state.load === 'loading'" class="gcd-note" data-test="git-center-diff-loading">{{ t('gui.git.centerDiff.loading') }}</p>
      <div v-else-if="error" class="gcd-note is-error" data-test="git-center-diff-error" :data-kind="error.kind" :title="error.code ?? undefined">
        <p>{{ errorText }}</p>
        <p v-if="error.kind === 'floor'" class="gcd-hint">{{ t('gui.git.centerDiff.floor_hint') }}</p>
        <button v-if="error.kind === 'network'" type="button" class="gcd-btn" data-test="git-center-diff-retry" @click="session.reload()">
          {{ t('gui.git.centerDiff.reload') }}
        </button>
      </div>
      <p v-else-if="unavailable" class="gcd-note" data-test="git-center-diff-unavailable">{{ t(`gui.git.centerDiff.${unavailable}`) }}</p>
      <p v-else-if="diff && !changed" class="gcd-note" data-test="git-center-diff-same">{{ t('gui.git.centerDiff.same') }}</p>
      <template v-else-if="diff">
        <pre class="gcd-lines" data-test="git-center-diff-lines"><span
          v-for="(line, i) in shown"
          :key="i"
          class="gcd-line"
          :class="`is-${line.kind}`"
          :data-kind="line.kind"
        ><span class="gcd-no">{{ line.oldNo ?? '' }}</span><span class="gcd-no">{{ line.newNo ?? '' }}</span><span class="gcd-sign">{{ line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' ' }}</span>{{ line.text }}</span></pre>
        <p v-if="hiddenLines > 0" class="gcd-note" data-test="git-center-diff-more">{{ t('gui.git.centerDiff.more_lines', { n: hiddenLines }) }}</p>
      </template>
    </div>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the Designer Center Diff screen (0066 D#2 §3-10, §6-6): a head line (path,
// state letter, what is compared, read again, close) and the body — line diff, or one line saying
// why there is none. The Workspace draws it over the hidden (still mounted) Flow tabs and Canvas.
// The server message is never shown; an error code is a tooltip only.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { diffLines } from '@/features/conversation'
import type { GitCenterDiffSession } from './gitCenterDiff'

/** Lines drawn at most; the rest is counted (the Conversation diff's bound). */
const CENTER_DIFF_MAX_LINES = 2000

const props = defineProps<{ session: GitCenterDiffSession }>()
const { t } = useI18n()

const target = computed(() => props.session.state.open)
const diff = computed(() => (props.session.state.load === 'ready' ? props.session.state.diff : null))
const error = computed(() => (props.session.state.load === 'error' ? props.session.state.error : null))

// The answer's own letter once it is in; until then the one the row showed.
const letter = computed(() => diff.value?.status ?? target.value?.status ?? null)

const compareLabel = computed(() => {
  const base = target.value?.compareBase
  if (base) return t('gui.git.centerDiff.compare_target', { target: base })
  const branch = diff.value?.baseBranch
  return branch ? t('gui.git.centerDiff.compare_floor_named', { branch }) : t('gui.git.centerDiff.compare_floor')
})

const unavailable = computed(() => {
  const d = diff.value
  if (!d) return null
  if (d.old.binary || d.new.binary) return 'binary'
  if (d.old.truncated || d.new.truncated) return 'too_large'
  return null
})

const lines = computed(() => {
  const d = diff.value
  if (!d || unavailable.value) return []
  return diffLines(d.old.content, d.new.content)
})
// Same contents on both sides (a reverted file, say) is "차이 없음", not a page of context lines.
const changed = computed(() => lines.value.some((line) => line.kind !== 'context'))
const shown = computed(() => lines.value.slice(0, CENTER_DIFF_MAX_LINES))
const hiddenLines = computed(() => Math.max(0, lines.value.length - CENTER_DIFF_MAX_LINES))

const errorText = computed(() => {
  const e = error.value
  if (!e) return ''
  if (e.kind === 'compare' || e.kind === 'failed') return t(`gui.git.centerDiff.error.${e.kind}`, { code: e.code ?? '' })
  return t(`gui.git.centerDiff.error.${e.kind}`)
})
</script>

<style scoped>
.gcd {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font-family: var(--fg2-font);
}

.gcd-head {
  display: flex;
  flex: none;
  flex-direction: column;
  gap: 4px;
  padding: 8px 12px;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-panel);
}

.gcd-title,
.gcd-sub {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.gcd-path {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
  font-weight: 700;
}

.gcd-letter {
  flex: none;
  padding: 0 5px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  font: 700 10px ui-monospace, SFMono-Regular, Consolas, monospace;
}

.gcd-compare {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 10.5px;
  color: var(--fg2-muted);
}

.gcd-grow {
  flex: 1 1 auto;
}

.gcd-btn {
  flex: none;
  height: 24px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  font-size: 10.5px;
  cursor: pointer;
}

.gcd-body {
  flex: 1 1 auto;
  min-height: 0;
  overflow: auto;
}

.gcd-note {
  margin: 0;
  padding: 12px;
  font-size: 11px;
  color: var(--fg2-muted);
}

.gcd-note p {
  margin: 0 0 6px;
}

.gcd-note.is-error {
  color: var(--fg2-status-danger-fg);
}

.gcd-hint {
  color: var(--fg2-muted);
}

.gcd-lines {
  margin: 0;
  padding: 8px 0;
  font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 11px;
  line-height: 1.5;
}

.gcd-line {
  display: block;
  padding-right: 12px;
  white-space: pre;
}

.gcd-line.is-add {
  background: var(--fg2-status-success-bg);
}

.gcd-line.is-del {
  background: var(--fg2-status-danger-bg);
}

.gcd-no {
  display: inline-block;
  width: 42px;
  padding-right: 6px;
  text-align: right;
  color: var(--fg2-muted);
  user-select: none;
}

.gcd-sign {
  display: inline-block;
  width: 14px;
  user-select: none;
}
</style>
