<template>
  <ModelessShell
    ref="shellRef"
    :open="open && result !== null"
    :title="windowTitle"
    :initial-x="initialX"
    :initial-y="initialY"
    :initial-width="460"
    :initial-height="initialHeight"
    :min-width="340"
    :min-height="240"
    @close="emit('close')"
  >
    <!-- the titlebar names the Flow the result is of (a long name ends in an ellipsis). -->
    <template #header>
      <span :title="windowTitle" data-test="run-result-title">{{ windowTitle }}</span>
    </template>
    <section v-if="result" ref="contentRef" class="run-result" data-test="run-result" :data-state="result.state" :data-scope="result.scope">
      <div class="run-result-state" :class="held ? 'is-held' : `is-${result.state}`" data-test="run-result-state">
        <span class="run-result-symbol" aria-hidden="true">
          <!-- mock ②: an orange warning triangle, not a glyph in a circle. -->
          <svg v-if="held" class="run-result-warning" width="24" height="22" viewBox="0 0 24 22" data-test="run-result-warning">
            <path d="M12 2 23 20H1Z" fill="#3a2a12" stroke="#ffae57" stroke-width="1.8" stroke-linejoin="round" />
            <path d="M12 8.5v5.2" stroke="#ffae57" stroke-width="2" stroke-linecap="round" />
            <circle cx="12" cy="16.6" r="1.2" fill="#ffae57" />
          </svg>
          <template v-else>{{ result.state === 'failed' ? '!' : '✓' }}</template>
        </span>
        <strong>{{ stateLabel(tr, has, result) }}</strong>
      </div>
      <div class="run-result-metrics">
        <div class="run-result-metric">
          <span class="run-result-label">{{ t('gui.runResult.label.scope') }}</span>
          <strong class="run-result-value" data-test="run-result-scope">{{ scopeLabel(tr, result) }}</strong>
        </div>
        <div class="run-result-metric">
          <span class="run-result-label">{{ t(heldCards ? 'gui.runResult.label.counts_held' : 'gui.runResult.label.counts') }}</span>
          <strong class="run-result-value" data-test="run-result-counts">
            <template v-if="heldCards">{{ t('gui.runResult.count_done', { n: result.counts.done }) }} · <span class="run-result-held">{{ t('gui.runResult.held_count', { n: heldCards }) }}</span> · {{ t('gui.runResult.count_failed', { n: result.counts.failed }) }}</template>
            <template v-else>{{ countsLabel(result) }}</template>
          </strong>
        </div>
        <div class="run-result-metric">
          <span class="run-result-label">{{ t('gui.runResult.label.next') }}</span>
          <strong class="run-result-value" data-test="run-result-next">{{ nextLabel(tr, has, result) }}</strong>
        </div>
        <div class="run-result-metric" data-test="run-result-flow" :title="originFlow.title">
          <span class="run-result-label">{{ t('gui.runResult.label.origin_flow') }}</span>
          <strong class="run-result-value" data-test="run-result-flow-name" :title="originFlow.name">{{ originFlow.name }}</strong>
          <span v-if="originFlow.id && originFlow.id !== originFlow.name" class="run-result-value run-result-flow-id" data-test="run-result-flow-id" :title="originFlow.id">{{ originFlow.id }}</span>
        </div>
        <div class="run-result-metric run-result-metric--steps">
          <span class="run-result-label">{{ t('gui.runResult.label.steps') }}</span>
          <ul class="run-result-steps" data-test="run-result-steps">
            <li v-for="(line, i) in steps.lines" :key="i" :class="{ 'is-held': stepHeld[i] }" :title="line">{{ line }}</li>
            <li v-if="steps.more > 0" class="run-result-more">{{ t('gui.runResult.more', { n: steps.more }) }}</li>
          </ul>
        </div>
      </div>
      <p v-if="result.failure" class="run-result-issue" data-test="run-result-failure">
        <span class="run-result-label">{{ t('gui.runResult.label.reason') }}</span> {{ failureLine(tr, has, result) }}
      </p>
      <p v-if="held" class="run-result-issue is-held" data-test="run-result-hold">
        <span class="run-result-label">{{ t('gui.runResult.label.hold_reason') }}</span> {{ t('gui.runResult.hold_reason') }}
      </p>
      <!-- AI attachment requests waiting for a decision (the flow attachment window). -->
      <p v-if="attachmentLine" class="run-result-attachments" data-test="run-result-attachments">{{ attachmentLine }}</p>
      <footer class="run-result-footer">
        <Button size="sm" variant="secondary" data-test="run-result-detail" @click="emit('detail')">{{ t('gui.runResult.detail') }}</Button>
        <Button size="sm" variant="primary" data-test="run-result-close" @click="emit('close')">{{ t('gui.runResult.close') }}</Button>
      </footer>
    </section>
  </ModelessShell>
</template>

<script setup lang="ts">
// the 1st run result window: shown only when a command
// ended completed or failed (runResultWatch). Its state icon and name, the scope, the cards it processed (by step or
// branch, a few lines), the done / failed counts, the next state, the failure's one line, [상세] and [닫기]. No long output
// or log here and no fixed sentence — the 2nd window holds the messages. ModelessShell: dragged, the clicked window
// comes to the front, nothing behind it is blocked. The wire is the 0029-TR mock's resultWindow.
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import type { FlowCommandResult } from '@/features/project'
import { installRunResultMessages } from './messages'
import {
  attachmentPendingLine,
  isHeldNode,
  isReviewHold,
  heldCount,
  countsLabel,
  failureLine,
  nextLabel,
  scopeLabel,
  stateLabel,
  stepLines,
  type HasKey,
  type Translate,
} from './runResultView'
import { useFitModelessHeight, type FittableShell } from './fitModelessHeight'

// `flowName` — the name of the Flow the result is of (the caller's, taken when the result showed).
const props = defineProps<{ result: FlowCommandResult | null; open: boolean; flowName?: string | null }>()
const emit = defineEmits<{ close: []; detail: [] }>()

const composer = useI18n({ useScope: 'global' })
installRunResultMessages(composer)
const { t, te } = composer
const tr: Translate = (key, values) => (values ? t(key, values) : t(key))
const has: HasKey = (key) => te(key)

const viewportWidth = typeof window === 'undefined' ? 1280 : window.innerWidth
const viewportHeight = typeof window === 'undefined' ? 800 : window.innerHeight
const initialX = Math.max(8, Math.round(viewportWidth / 2 - 230))
// no fixed 320 — the failed cards and their causes show without scrolling (the screen less 80).
// that is now the cap only — once laid out the window takes its content's height (a short result
// leaves no empty space below) and is centred again; a longer one stops at the screen less 80 and scrolls.
const VIEWPORT_MARGIN = 80
const initialHeight = Math.max(240, viewportHeight - VIEWPORT_MARGIN)
const initialY = Math.max(8, Math.round((viewportHeight - initialHeight) / 2))

const shellRef = ref<FittableShell | null>(null)
const contentRef = ref<HTMLElement | null>(null)
useFitModelessHeight(shellRef, contentRef, VIEWPORT_MARGIN)

const windowTitle = computed(() => (props.flowName ? t('gui.runResult.title_with_flow', { name: props.flowName }) : t('gui.runResult.title')))

// Resolve identity from the result only; a name without its origin ID is unverified.
const originFlow = computed(() => {
  const flowId = props.result?.flowId?.trim()
  if (!flowId) {
    const unknown = t('gui.runResult.origin_unknown')
    return { name: unknown, id: '', title: unknown }
  }
  const name = props.flowName?.trim() || flowId
  return { name, id: flowId, title: name !== flowId ? `${name} (${flowId})` : flowId }
})

const steps = computed(() => (props.result ? stepLines(tr, has, props.result) : { lines: [], more: 0 }))
const held = computed(() => (props.result ? isReviewHold(props.result) : false))
const heldCards = computed(() => (props.result ? heldCount(props.result) : 0))
const stepHeld = computed(() => (props.result?.nodes ?? []).slice(0, steps.value.lines.length).map(isHeldNode))
const attachmentLine = computed(() => attachmentPendingLine(tr, props.result))
</script>

<style scoped>
/* no padding of its own — the shell's body already pads it (no doubled space around it). */
.run-result { display: flex; flex-direction: column; gap: 12px; color: var(--fg2-text); font-size: 13px; }
.run-result-state { display: flex; align-items: center; gap: 8px; font-size: 15px; }
.run-result-symbol { display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px; border-radius: 50%; font-weight: 800;
  background: var(--fg2-status-success-bg, #12402c); color: var(--fg2-status-success-fg, #6fe0a8); }
.run-result-state.is-failed .run-result-symbol { background: var(--fg2-status-danger-bg, #4a1c1c); color: var(--fg2-status-danger-fg, #f0a2a2); }
.run-result-state.is-held { font-size: 16px; }
.run-result-state.is-held .run-result-symbol { width: 26px; height: 26px; border-radius: 0; background: transparent; }
.run-result-state.is-held strong { color: #ffc58a; }
.run-result-warning { display: block; }
.run-result-held, .run-result-steps li.is-held { color: #ffc58a; }
.run-result-metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 7px; }
.run-result-metric { min-width: 0; padding: 9px 11px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface-flat); }
.run-result-metric--steps { grid-column: 1 / -1; }
.run-result-label { display: block; margin-bottom: 5px; color: var(--fg2-muted); font-size: 11px; }
.run-result-issue .run-result-label { display: inline; }
.run-result-value { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-result-flow-id { margin-top: 3px; color: var(--fg2-muted); font-size: 11px; }
.run-result-steps { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 3px; }
.run-result-steps li { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.run-result-more { color: var(--fg2-muted); }
.run-result-issue { margin: 0; color: var(--fg2-status-danger-fg, #f0a2a2); line-height: 1.5; overflow-wrap: anywhere; }
.run-result-footer { display: flex; justify-content: flex-end; gap: 8px; }
.run-result-issue.is-held { padding: 9px 11px; border: 1px solid #6b5326; border-radius: 7px; background: #2a2213; color: #ffd79a; font-size: 12px; }
.run-result-issue.is-held .run-result-label { color: #ffd79a; font-weight: 700; margin-right: 4px; }
.run-result-attachments { margin: 0; color: var(--fg2-status-warning-fg, #e3b341); }
</style>
