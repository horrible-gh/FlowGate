<template>
  <div class="ma" data-test="merge-auto-detail">
    <div v-if="showUnresolved && auto.unresolved.length" class="ma-sec" data-test="merge-auto-unresolved">
      <div class="ma-h">{{ t('gui.inspector.merge.auto.unresolved') }}</div>
      <div class="ma-files">
        <div v-for="file in auto.unresolved" :key="file.path" class="ma-fr" data-test="merge-auto-file">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="FILE_ICON" /></svg>
          <code :title="file.path">{{ file.path }}</code>
          <span v-if="file.hunks.length" class="ma-hunks" data-test="merge-auto-hunks">{{ hunkText(file.hunks) }}</span>
          <span v-else class="ma-hunks is-none" data-test="merge-auto-no-hunks">{{ t('gui.inspector.merge.auto.no_hunks') }}</span>
        </div>
      </div>
    </div>
    <!-- F84-9: folded, the tries are one closed line ("자동 해결 3회 실패 · 기록 보기") under the window's current step -->
    <component :is="historyFolded ? 'details' : 'div'" v-if="auto.attempts.length" class="ma-sec" :class="{ 'ma-fold': historyFolded }" data-test="merge-auto-history" :data-folded="historyFolded ? 'true' : 'false'">
      <summary v-if="historyFolded" class="ma-fold-sum" data-test="merge-auto-fold">{{ foldLabel }}</summary>
      <div v-else class="ma-h">{{ t('gui.inspector.merge.auto.history') }}</div>
      <ol class="ma-list">
        <li v-for="attempt in auto.attempts" :key="attempt.no" class="ma-row" :data-result="attempt.result ?? 'unknown'" data-test="merge-auto-attempt">
          <span class="ma-mark" :class="`is-${tone(attempt.result)}`" aria-hidden="true">{{ MARKS[tone(attempt.result)] }}</span>
          <span class="ma-line">
            <b>{{ t('gui.inspector.merge.auto.attempt_no', { no: attempt.no }) }}</b>
            · {{ t(`gui.inspector.merge.auto.modes.${attempt.mode ?? 'unknown'}`) }}
            <template v-if="attempt.provider?.name">
              · <span data-test="merge-auto-ai">{{ attempt.provider.name }}</span>
            </template>
            <template v-else-if="attempt.reasonCode === 'no_available_ai'">
              · <span data-test="merge-auto-ai">{{ t('gui.inspector.merge.auto.no_ai') }}</span>
            </template>
            · <span class="ma-result" :class="`is-${tone(attempt.result)}`" data-test="merge-auto-result">{{ t(`gui.inspector.merge.auto.results.${mergeAutoResultKey(attempt.result)}`) }}</span>
          </span>
          <template v-if="attempt.reason || attempt.reasonSummary">
            <span class="ma-reason" data-test="merge-auto-reason">{{ mergeAutoReason(attempt).line }}</span>
            <details v-if="mergeAutoReason(attempt).raw" class="ma-raw" data-test="merge-auto-reason-raw">
              <summary>{{ t('gui.inspector.merge.auto.reason_raw') }}</summary>
              <pre>{{ mergeAutoReason(attempt).raw }}</pre>
            </details>
          </template>
        </li>
      </ol>
    </component>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { mergeAutoReason, mergeAutoResultKey, mergeHunkRange, type MergeAuto } from './mergeModel'

// The automatic resolution's detail: the files and conflict parts it could not settle, and what each try did.
// historyFolded (F84-9): the tries sit in a closed fold whose one line tells how many failed.
const props = withDefaults(defineProps<{ auto: MergeAuto; showUnresolved?: boolean; historyFolded?: boolean }>(), { showUnresolved: false, historyFolded: false })
const { t } = useI18n()

const FILE_ICON = 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5'
type Tone = 'run' | 'ok' | 'bad'
const MARKS: Record<Tone, string> = { run: '●', ok: '✓', bad: '✗' }
const tone = (result: string | null): Tone => (result === 'running' ? 'run' : result === 'resolved' ? 'ok' : 'bad')
const failedCount = computed(() => props.auto.attempts.filter((attempt) => tone(attempt.result) === 'bad').length)
const foldLabel = computed(() => (failedCount.value
  ? t('gui.inspector.merge.auto.fold', { count: failedCount.value })
  : t('gui.inspector.merge.auto.fold_nocount')))

/** `12–18, 40` — the conflict parts of one file, by line. */
const hunkText = (hunks: readonly { startLine: number; endLine: number }[]): string => t('gui.inspector.merge.auto.hunks', { ranges: hunks.map(mergeHunkRange).join(', '), count: hunks.length })
</script>

<style scoped>
.ma { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.ma-h { margin: 0 0 4px; color: #8ea5bd; font-size: 11.5px; font-weight: 700; }
.ma-files { overflow: hidden; border: 1px solid #233650; border-radius: 8px; }
.ma-fr { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 6px 10px; border-bottom: 1px solid #1b2b42; background: #0b1828; font-size: 12px; }
.ma-fr:last-child { border-bottom: 0; }
.ma-fr svg { flex: none; width: 14px; height: 14px; fill: none; stroke: #8fb0d0; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.ma-fr code { flex: 1 1 auto; min-width: 0; overflow: hidden; color: #dbe8f6; font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; text-overflow: ellipsis; white-space: nowrap; }
.ma-hunks { flex: none; color: #ff96a6; font-size: 11px; white-space: nowrap; }
.ma-list { display: flex; flex-direction: column; gap: 4px; margin: 0; padding: 8px 10px; list-style: none; border: 1px solid #1b2b42; border-radius: 6px; background: #08131f; }
.ma-row { display: grid; grid-template-columns: 14px minmax(0, 1fr); column-gap: 6px; color: #a9bed4; font-size: 11.5px; line-height: 1.6; }
.ma-line { min-width: 0; overflow-wrap: anywhere; }
.ma-line b { color: #eef5ff; }
.ma-mark.is-ok, .ma-result.is-ok { color: #4fe0b0; }
.ma-mark.is-run, .ma-result.is-run { color: #7cc0ff; }
.ma-mark.is-bad, .ma-result.is-bad { color: #ff96a6; }
.ma-reason { grid-column: 2; min-width: 0; color: #8ea5bd; overflow-wrap: anywhere; white-space: pre-wrap; }
.ma-hunks.is-none { color: #8ea5bd; font-style: italic; }
.ma-fold-sum { width: max-content; max-width: 100%; color: #8ea5bd; font-size: 11.5px; cursor: pointer; }
.ma-fold-sum:hover { color: #cfe0f3; }
.ma-fold[open] > .ma-fold-sum { margin-bottom: 4px; }
.ma-raw { grid-column: 2; min-width: 0; color: #6f87a0; font-size: 11px; }
.ma-raw summary { cursor: pointer; width: max-content; }
.ma-raw pre { max-height: 160px; margin: 4px 0 0; padding: 6px 8px; overflow: auto; border: 1px solid #1b2b42; border-radius: 4px; background: #050d17; color: #a9bed4; font-family: ui-monospace, Consolas, monospace; font-size: 11px; white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
