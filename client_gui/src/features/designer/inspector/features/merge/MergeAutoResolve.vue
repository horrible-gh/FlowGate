<template>
  <div class="ma" data-test="merge-auto-detail">
    <div v-if="showUnresolved && auto.unresolved.length" class="ma-sec" data-test="merge-auto-unresolved">
      <div class="ma-h">{{ t('gui.inspector.merge.auto.unresolved') }}</div>
      <div class="ma-files">
        <div v-for="file in auto.unresolved" :key="file.path" class="ma-fr" data-test="merge-auto-file">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="FILE_ICON" /></svg>
          <code :title="file.path">{{ file.path }}</code>
          <span v-if="file.hunks.length" class="ma-hunks" data-test="merge-auto-hunks">{{ hunkText(file.hunks) }}</span>
        </div>
      </div>
    </div>
    <div v-if="auto.attempts.length" class="ma-sec" data-test="merge-auto-history">
      <div class="ma-h">{{ t('gui.inspector.merge.auto.history') }}</div>
      <ol class="ma-list">
        <li v-for="attempt in auto.attempts" :key="attempt.no" class="ma-row" :data-result="attempt.result ?? 'unknown'" data-test="merge-auto-attempt">
          <span class="ma-mark" :class="`is-${tone(attempt.result)}`" aria-hidden="true">{{ MARKS[tone(attempt.result)] }}</span>
          <span class="ma-line">
            <b>{{ t('gui.inspector.merge.auto.attempt_no', { no: attempt.no }) }}</b>
            · {{ t(`gui.inspector.merge.auto.modes.${attempt.mode ?? 'unknown'}`) }}
            · <span data-test="merge-auto-ai">{{ attempt.provider?.name ?? t('gui.inspector.merge.auto.no_ai') }}</span>
            · <span class="ma-result" :class="`is-${tone(attempt.result)}`" data-test="merge-auto-result">{{ t(`gui.inspector.merge.auto.results.${attempt.result ?? 'unknown'}`) }}</span>
          </span>
          <span v-if="attempt.reason" class="ma-reason" data-test="merge-auto-reason">{{ attempt.reason }}</span>
        </li>
      </ol>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import { mergeHunkRange, type MergeAuto } from './mergeModel'

// The automatic resolution's detail: the files and conflict parts it could not settle, and what each try did.
withDefaults(defineProps<{ auto: MergeAuto; showUnresolved?: boolean }>(), { showUnresolved: false })
const { t } = useI18n()

const FILE_ICON = 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5'
type Tone = 'run' | 'ok' | 'bad'
const MARKS: Record<Tone, string> = { run: '●', ok: '✓', bad: '✗' }
const tone = (result: string | null): Tone => (result === 'running' ? 'run' : result === 'resolved' ? 'ok' : 'bad')

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
</style>
