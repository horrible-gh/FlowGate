<template>
  <section v-if="target" class="gw-diff" data-test="git-center-diff" :data-state="session.state.load" :data-path="target.path">
    <div class="gw-diff-nav">
      <button type="button" class="gw-diff-back" data-test="git-center-diff-back" @click="emit('back')">‹ {{ t('gui.git.centerDiff.back') }}</button>
    </div>
    <dl class="gw-diff-meta">
      <div class="gw-diff-meta-row">
        <dt>{{ t('gui.git.centerDiff.path_label') }}</dt>
        <dd>
          <span v-if="letter" class="gw-mark" :class="`mk-${markClass}`" data-test="git-center-diff-letter">{{ letter }}</span>
          <span class="gw-diff-path" data-test="git-center-diff-path" :title="target.path">{{ target.path }}</span>
        </dd>
      </div>
      <div class="gw-diff-meta-row">
        <dt>{{ t('gui.git.centerDiff.compare_label') }}</dt>
        <dd data-test="git-center-diff-compare">{{ compareLabel }}</dd>
      </div>
    </dl>
    <p v-if="session.state.load === 'loading'" class="gw-diff-note" data-test="git-center-diff-loading">{{ t('gui.git.centerDiff.loading') }}</p>
    <div v-else-if="error" class="gw-diff-note is-error" data-test="git-center-diff-error" :data-kind="error.kind" :title="error.code ?? undefined">
      <p>{{ errorText }}</p>
      <p v-if="error.kind === 'floor'" class="gw-diff-hint">{{ t('gui.git.centerDiff.floor_hint') }}</p>
      <button v-if="error.kind === 'network'" type="button" class="gw-act" data-test="git-center-diff-retry" @click="session.reload()">
        {{ t('gui.git.centerDiff.reload') }}
      </button>
    </div>
    <p v-else-if="unavailable" class="gw-diff-note" data-test="git-center-diff-unavailable">{{ t(`gui.git.centerDiff.${unavailable}`) }}</p>
    <p v-else-if="diff && !changed" class="gw-diff-note" data-test="git-center-diff-same">{{ t('gui.git.centerDiff.same') }}</p>
    <template v-else-if="diff">
      <div class="gw-diff-code" data-test="git-center-diff-lines">
        <div
          v-for="(line, i) in shown"
          :key="i"
          class="gw-diff-line"
          :class="`is-${line.kind}`"
          :data-kind="line.kind"
        ><span class="gw-diff-no">{{ line.oldNo ?? '' }}</span><span class="gw-diff-no">{{ line.newNo ?? '' }}</span><span class="gw-diff-sign">{{ line.kind === 'add' ? '+' : line.kind === 'del' ? '-' : ' ' }}</span><span class="gw-diff-text">{{ line.text }}</span></div>
      </div>
      <p v-if="hiddenLines > 0" class="gw-diff-note" data-test="git-center-diff-more">{{ t('gui.git.centerDiff.more_lines', { n: hiddenLines }) }}</p>
    </template>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the Designer Center Diff screen (0066 D#2 §3-10, §6-6): a head line (path,
// state letter, what is compared, read again, close) and the body — line diff, or one line saying
// why there is none. The server message is never shown; an error code is a tooltip only.
// flowgate.v02.0067 T#4 — the detail of the Git modeless `Diff` tab (0067-R §6-2–§6-5, v27
// `workDiff()`): `‹ 목록으로` (the tab decides where back is), then `파일 경로` with the full path
// once — no separate file name — and `비교 기준`, each a label of its own; then the body. The code
// keeps its columns: every line is as wide as its text and the code area scrolls sideways inside
// itself, never the window. No difference (`차이 없음`) and a failed read are different lines.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { diffLines } from '@/features/conversation'
import type { GitCenterDiffSession } from './gitCenterDiff'

/** Lines drawn at most; the rest is counted (the Conversation diff's bound). */
const CENTER_DIFF_MAX_LINES = 2000

const props = defineProps<{ session: GitCenterDiffSession }>()
const emit = defineEmits<{ back: [] }>()
const { t } = useI18n()

const target = computed(() => props.session.state.open)
const diff = computed(() => (props.session.state.load === 'ready' ? props.session.state.diff : null))
const error = computed(() => (props.session.state.load === 'error' ? props.session.state.error : null))

// The answer's own letter once it is in; until then the one the row showed.
const letter = computed(() => diff.value?.status ?? target.value?.status ?? null)
const markClass = computed(() => {
  const value = letter.value ?? ''
  return value === '?' ? 'u' : value.toLowerCase() || 'none'
})

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
