<template>
  <ModelessShell kind-color="#4ba3ff" kind="merge" ref="shell" :open="true" :title="windowTitle" :initial-width="initialWidth" :initial-height="400"
    :initial-x="16" :initial-y="80" :min-width="300" :min-height="200" :minimized="win?.minimized ?? false"
    @update:minimized="value => { if (win) win.minimized = value }" @close="close">

  <section class="merge-window" data-test="merge-window" :data-screen="screen" :aria-busy="busy ? 'true' : 'false'">
    <p v-if="screen === 'loading' && !readFailed" class="mz-note" role="status">{{ t('gui.inspector.merge.loading') }}</p>
    <p v-if="readFailed" class="mz-alert" role="alert" data-test="merge-read-failed">
      {{ t('gui.inspector.merge.read_failed') }}
      <button type="button" class="mz-link" @click="reload">{{ t('gui.inspector.merge.retry_read') }}</button>
    </p>

    <!-- automatic conflict resolution (read only: the server runs and ends it): running outside the progress box, or out of tries -->
    <div v-if="autoDetail && autoResolve" class="mz" :class="{ 'is-info': autoResolve.state === 'running' }" data-test="merge-auto-box" :data-auto-state="autoResolve.state">
      <h4 class="mz-title" :class="autoResolve.state === 'running' ? 'is-info' : 'is-danger'">
        <span v-if="autoResolve.state === 'running'" class="mz-spin"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.spinner" /></svg></span>
        <svg v-else viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.conflict" /></svg>{{ autoTitle }}
      </h4>
      <p v-if="autoText" class="mz-text" data-test="merge-auto-text">{{ autoText }}</p>
      <MergeAutoResolve :auto="autoResolve" :show-unresolved="autoResolve.state === 'exhausted'" />
    </div>
    <p v-else-if="autoNote" class="mz-note" role="status" data-test="merge-auto-note">{{ autoNote }}</p>

    <!-- waiting (blue) -->
    <div v-if="screen === 'waiting' && state" class="mz is-info" data-test="merge-box">
      <h4 class="mz-title is-info"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.hourglass" /></svg>{{ t('gui.inspector.merge.waiting.title') }}</h4>
      <p class="mz-text" data-test="merge-waiting-text">
        <template v-if="state.blockedBy">
          {{ t('gui.inspector.merge.waiting.before') }}<b>{{ t(state.blockedBy.why === 'conflict' ? 'gui.inspector.merge.waiting.blocked_conflict' : 'gui.inspector.merge.waiting.blocked_merging', { title: state.blockedBy.title }) }}</b>{{ t('gui.inspector.merge.waiting.after') }}
        </template>
        <template v-else>{{ t('gui.inspector.merge.waiting.turn') }}</template>
      </p>
      <div v-if="state.blockedBy" class="mz-btns">
        <button type="button" class="mz-b" data-test="merge-go-blocked" @click="goCard(state.blockedBy.nodeKey)">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.arrow" /></svg>{{ t('gui.inspector.merge.waiting.go', { title: state.blockedBy.title }) }}
        </button>
      </div>
    </div>

    <!-- ① catching up (blue): progress + step log -->
    <div v-else-if="screen === 'catching_up' && state" class="mz is-info" data-test="merge-box">
      <h4 class="mz-title is-info"><span class="mz-spin"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.spinner" /></svg></span>{{ autoInBox ? autoTitle : t(state.resolving ? 'gui.inspector.merge.catching_up.title_ai' : 'gui.inspector.merge.catching_up.title_merging') }}</h4>
      <p class="mz-text" data-test="merge-catching-up-text">
        {{ autoInBox ? autoText : state.resolving ? tc('gui.inspector.merge.catching_up.resolving', state.files.length) : t('gui.inspector.merge.catching_up.merging') }}
      </p>
      <div class="mz-prog" :class="{ 'is-indeterminate': !state.files.length }"><i :style="progressStyle" /></div>
      <MergeAutoResolve v-if="autoInBox && autoResolve" :auto="autoResolve" />
      <div v-else class="mz-log" data-test="merge-log">
        <div v-for="step in steps" :key="step.key" :data-step="step.state"><span :class="`mz-log-${step.state}`">{{ STEP_MARKS[step.state] }}</span> {{ step.text }}</div>
      </div>
    </div>

    <!-- ② could not reconcile (red); after every automatic try failed, the box above tells it (one box, 시안 jvj9ff9t §1) -->
    <div v-else-if="screen === 'needs_choice' && state && !exhaustedBox" class="mz" data-test="merge-box">
      <h4 class="mz-title is-danger"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.conflict" /></svg>{{ t(allResolved ? 'gui.inspector.merge.needs_choice.title_resolved' : 'gui.inspector.merge.needs_choice.title') }}</h4>
      <p class="mz-text" data-test="merge-summary">
        <template v-if="allResolved">{{ t('gui.inspector.merge.needs_choice.resolved_all') }}</template>
        <template v-else-if="summary.file">{{ summary.before }}<b>{{ summary.file }}</b>{{ summary.after }}</template>
        <template v-else>{{ summary.before }}</template>
      </p>
      <div v-if="state.files.length" class="mz-files" data-test="merge-files">
        <div v-for="file in state.files" :key="file.path" class="mz-fr" :data-resolved="file.resolved ? 'true' : 'false'" :data-kind="file.kind">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.file" /></svg>
          <code :title="file.path">{{ file.path }}</code>
          <span v-if="file.resolved" class="mz-why is-done">{{ t('gui.inspector.merge.file_resolved') }}</span>
          <span v-else class="mz-why">{{ t(`gui.inspector.merge.kinds.${file.kind}`) }}</span>
        </div>
      </div>
      <p v-if="state.waitingCards.length" class="mz-meta" data-test="merge-meta">
        <template v-if="state.waitingCards.length">
          {{ t('gui.inspector.merge.waiting_cards') }}
          <template v-for="(waiting, index) in state.waitingCards" :key="waiting.nodeKey"><template v-if="index">, </template><button type="button" class="mz-link" data-test="merge-waiting-card" @click="goCard(waiting.nodeKey)">{{ waiting.title }}</button></template>
        </template>
      </p>
    </div>

    <!-- ③ per-file pick (red) -->
    <div v-else-if="screen === 'pick' && state" class="mz" data-test="merge-box">
      <h4 class="mz-title is-danger"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.hand" /></svg>{{ t('gui.inspector.merge.pick.title') }}</h4>
      <p class="mz-text">{{ t('gui.inspector.merge.pick.before') }}<b>{{ t('gui.inspector.merge.pick.ai') }}</b>{{ t('gui.inspector.merge.pick.after') }}</p>
      <div class="mz-files" data-test="merge-pick-list">
        <div v-for="file in openFiles" :key="file.path" class="mz-fr" :data-path="file.path" :data-kind="file.kind">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.file" /></svg>
          <code :title="file.path">{{ file.path }}</code>
          <span class="mz-pick" role="radiogroup" :aria-label="file.path">
            <button
              v-for="side in SIDES"
              :key="side"
              type="button"
              role="radio"
              :class="{ on: choices[file.path] === side }"
              :aria-checked="choices[file.path] === side ? 'true' : 'false'"
              :disabled="busy || !sideAvailable(file.kind, side)"
              :title="sideAvailable(file.kind, side) ? undefined : t('gui.inspector.merge.pick.deleted_side')"
              :data-test="`merge-pick-${side}`"
              @click="choices[file.path] = side"
            >{{ t(`gui.inspector.merge.pick.${side}`) }}</button>
          </span>
        </div>
      </div>
    </div>

    <!-- failed (red) -->
    <div v-else-if="screen === 'failed' && state" class="mz" data-test="merge-box">
      <h4 class="mz-title is-danger"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.failed" /></svg>{{ t('gui.inspector.merge.failed.title') }}</h4>
      <p class="mz-text" data-test="merge-failed-text">{{ reasonText || t('gui.inspector.merge.failed.body') }}</p>
      <p v-if="state.waitingCards.length" class="mz-meta">
        {{ t('gui.inspector.merge.waiting_cards') }}
        <template v-for="(waiting, index) in state.waitingCards" :key="waiting.nodeKey"><template v-if="index">, </template><button type="button" class="mz-link" @click="goCard(waiting.nodeKey)">{{ waiting.title }}</button></template>
      </p>
    </div>

    <!-- ④ merged (green); a merge an AI resolved offers [병합 취소] (시안 jvj9ff9t §2) -->
    <div v-else-if="screen === 'merged'" class="mz is-ok" data-test="merge-box" :data-ai-merged="aiResolved ? 'true' : 'false'">
      <h4 class="mz-title is-ok"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.check" /></svg>{{ t(aiResolved ? 'gui.inspector.merge.merged.title_ai' : 'gui.inspector.merge.merged.title') }}</h4>
      <p class="mz-text" data-test="merge-merged-text">{{ t(aiResolved ? 'gui.inspector.merge.merged.body_ai' : 'gui.inspector.merge.merged.body') }}</p>
      <MergeAutoResolve v-if="autoResolve?.attempts.length" :auto="autoResolve" />
    </div>

    <p v-if="exhaustedBox && state?.waitingCards.length" class="mz-meta" data-test="merge-meta">
      {{ t('gui.inspector.merge.waiting_cards') }}
      <template v-for="(waiting, index) in state.waitingCards" :key="waiting.nodeKey"><template v-if="index">, </template><button type="button" class="mz-link" data-test="merge-waiting-card" @click="goCard(waiting.nodeKey)">{{ waiting.title }}</button></template>
    </p>

    <p v-if="errorText" class="mz-alert" role="alert" data-test="merge-error">{{ errorText }}</p>
    <p v-if="undoFailed" class="mz-alert" role="alert" data-test="merge-undo-failed">{{ t('gui.inspector.merge.undo.failed') }}</p>

    <!-- The window's buttons stay at its bottom: seen without scrolling at the default size (시안 jvj9ff9t §1). -->
    <div v-if="footShown" class="mz-foot" data-test="merge-foot">
      <template v-if="screen === 'catching_up' && autoInBox">
        <div class="mz-btns">
          <button v-if="can('ai_resolve')" type="button" class="mz-b is-pri" :disabled="busy" data-test="merge-ai" @click="startAi">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.sparkle" /></svg>{{ t('gui.inspector.merge.needs_choice.ai') }}
          </button>
          <button v-if="can('pick') && openFiles.length" type="button" class="mz-b" :disabled="busy" data-test="merge-pick-open" @click="openPick">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.hand" /></svg>{{ t('gui.inspector.merge.needs_choice.pick') }}
          </button>
        </div>
      </template>
      <template v-else-if="screen === 'needs_choice' && can('merge')">
        <div class="mz-btns">
          <button type="button" class="mz-b is-pri" :disabled="busy" data-test="merge-merge" @click="merge">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.check" /></svg>{{ t('gui.inspector.merge.needs_choice.merge') }}
          </button>
          <button v-if="can('discard_rerun')" type="button" class="mz-b is-dng" :disabled="busy" data-test="merge-discard" @click="confirmOpen = true">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.rerun" /></svg>{{ t('gui.inspector.merge.needs_choice.discard') }}
          </button>
        </div>
      </template>
      <template v-else-if="screen === 'needs_choice'">
        <div v-if="can('ai_resolve')" class="mz-btns">
          <button type="button" class="mz-b is-pri" :disabled="busy" data-test="merge-ai" @click="startAi">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.sparkle" /></svg>{{ t('gui.inspector.merge.needs_choice.ai') }}
          </button>
          <label class="mz-aisel">
            <span class="mz-sr">{{ t('gui.inspector.merge.needs_choice.ai_pick') }}</span>
            <select v-model="providerId" :disabled="busy" data-test="merge-ai-provider">
              <option :value="''">{{ defaultLabel }}</option>
              <option v-for="provider in otherProviders" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
            </select>
          </label>
        </div>
        <div class="mz-btns">
          <button v-if="can('pick')" type="button" class="mz-b" :disabled="busy" data-test="merge-pick-open" @click="openPick">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.hand" /></svg>{{ t('gui.inspector.merge.needs_choice.pick') }}
          </button>
          <button v-if="can('discard_rerun')" type="button" class="mz-b is-dng" :disabled="busy" data-test="merge-discard" @click="confirmOpen = true">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.rerun" /></svg>{{ t('gui.inspector.merge.needs_choice.discard') }}
          </button>
        </div>
      </template>
      <template v-else-if="screen === 'pick'">
        <div class="mz-btns">
          <button type="button" class="mz-b is-pri" :disabled="busy" data-test="merge-pick-submit" @click="submitPick">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.check" /></svg>{{ t('gui.inspector.merge.pick.submit') }}
          </button>
          <button type="button" class="mz-b" :disabled="busy" data-test="merge-pick-cancel" @click="picking = false">{{ t('gui.inspector.merge.pick.cancel') }}</button>
        </div>
      </template>
      <template v-else-if="screen === 'failed'">
        <div class="mz-btns">
          <button v-if="can('retry')" type="button" class="mz-b" :disabled="busy" data-test="merge-retry" @click="retry">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.retry" /></svg>{{ t('gui.inspector.merge.failed.retry') }}
          </button>
          <button v-if="can('discard_rerun')" type="button" class="mz-b is-dng" :disabled="busy" data-test="merge-discard" @click="confirmOpen = true">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.rerun" /></svg>{{ t('gui.inspector.merge.needs_choice.discard') }}
          </button>
        </div>
      </template>
      <template v-else-if="undoShown">
        <div class="mz-btns">
          <button type="button" class="mz-b is-dng" :disabled="undoBusy || !!undoBlocked" :aria-describedby="undoBlocked ? undoReasonId : undefined" data-test="merge-undo" @click="openUndo">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path :d="ICONS.undo" /></svg>{{ t('gui.inspector.merge.undo.button') }}
          </button>
          <span v-if="undoBlocked" :id="undoReasonId" class="mz-why-off" data-test="merge-undo-reason">{{ t(`gui.inspector.merge.undo.blocked.${undoBlocked}`) }}</span>
        </div>
      </template>
    </div>

    <ConfirmDialog
      :open="confirmOpen"
      danger
      :title="t('gui.inspector.merge.confirm_discard.title')"
      :message="t('gui.inspector.merge.confirm_discard.body')"
      :confirm-label="t('gui.inspector.merge.confirm_discard.ok')"
      :cancel-label="t('gui.inspector.merge.confirm_discard.cancel')"
      @confirm="discard"
      @cancel="confirmOpen = false"
    />
    <ConfirmDialog
      :open="undoOpen"
      danger
      :busy="undoBusy"
      :title="t('gui.inspector.merge.undo.title')"
      :confirm-label="t('gui.inspector.merge.undo.ok')"
      :cancel-label="t('gui.inspector.merge.undo.cancel')"
      @confirm="runUndo"
      @cancel="undoOpen = false"
    >
      <template #message="{ descriptionId }">
        <ul :id="descriptionId" class="mz-undo-list" data-test="merge-undo-confirm">
          <li class="is-back" data-test="merge-undo-back">{{ t('gui.inspector.merge.undo.back', { cards: undoImpact.back.join(', ') }) }}</li>
          <li v-if="undoImpact.keep.length" class="is-keep" data-test="merge-undo-keep">{{ t('gui.inspector.merge.undo.keep', { cards: undoImpact.keep.join(', ') }) }}</li>
          <li>{{ t('gui.inspector.merge.undo.note') }}</li>
        </ul>
      </template>
    </ConfirmDialog>
  </section>
  </ModelessShell>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../../../../components/ui/ModelessShell.vue'
import { closeMergeWindow, type MergeWindowEntry } from './mergeWindows'
import ConfirmDialog from '../../../../../components/ui/ConfirmDialog.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import MergeAutoResolve from './MergeAutoResolve.vue'
import { mergeAutoCurrent, mergeAutoProgress, mergeAutoRunning, mergeCountKey, mergeReasonKey } from './mergeModel'
import { openCardMerge, useMerge } from './useMerge'

const props = defineProps<{ target: InspectorTarget; win?: MergeWindowEntry }>()
const { t, te } = useI18n()

// Verification counts are not part of CardMergeState; never infer them from resolved files.
/** The mockup's glyphs (24px box, stroke 2). */
const ICONS = {
  conflict: 'M7 4l10 16 M17 4L7 20 M4 9h4 M16 15h4',
  hourglass: 'M7 3h10 M7 21h10 M8 3c0 5 8 5 8 9s-8 4-8 9 M16 3c0 5-8 5-8 9s8 4 8 9',
  spinner: 'M12 3a9 9 0 1 0 9 9',
  file: 'M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z M14 3v5h5',
  sparkle: 'M12 3l1.8 4.6L18 9l-4.2 1.4L12 15l-1.8-4.6L6 9l4.2-1.4z M18 15l.9 2.1L21 18l-2.1.9L18 21l-.9-2.1L15 18l2.1-.9z',
  hand: 'M8 13V5a1.5 1.5 0 0 1 3 0v6 M11 11V4a1.5 1.5 0 0 1 3 0v7 M14 11V5a1.5 1.5 0 0 1 3 0v8 M8 13l-1.5-1.5a1.6 1.6 0 0 0-2.3 2.3L8 18c1.2 1.6 2.6 3 5 3h1a5 5 0 0 0 5-5v-3',
  rerun: 'M4 12a8 8 0 1 0 2.3-5.7 M4 4v5h5',
  retry: 'M20 12a8 8 0 1 1-2.3-5.7 M20 4v5h-5',
  check: 'M5 12l4 4L19 6',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  failed: 'M5 3v4l6 6v8 M17 3v4l-6 6 M15 16l5 5 M20 16l-5 5',
  undo: 'M9 14L4 9l5-5 M4 9h10a6 6 0 0 1 0 12h-3',
} as const

/** How a file overlaps (the merge state's file kind). */
type FileKind = 'both' | 'deleted_latest' | 'deleted_card'
const SIDES = ['ai', 'card', 'latest'] as const
type Side = (typeof SIDES)[number]
type StepState = 'ok' | 'run' | 'todo'
const STEP_MARKS: Record<StepState, string> = { ok: '✓', run: '●', todo: '○' }

const { state, screen, readFailed, error, busy, picking, providers, aiResolved, reload, aiResolve, pick, merge, discardRerun, retry, loadProviders } =
  useMerge(() => props.target, () => props.win?.refresh())

const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(560, window.innerWidth - 32)
const windowTitle = computed(() => t(`gui.inspector.merge.window.${autoInBox.value ? 'auto_running' : screen.value === 'pick' ? 'needs_choice' : screen.value}`) + ' · ' + props.target.title)
const close = () => { if (props.win) closeMergeWindow(props.win.key) }
watch(() => props.win?.raise, () => { shell.value?.bringToFront(); void reload() })

const can = (action: string) => !!state.value?.actions.includes(action as never)
const allResolved = computed(() => !!state.value?.files.length && state.value.files.every((file) => file.resolved))
const openFiles = computed(() => (state.value?.files ?? []).filter((file) => !file.resolved))

/** A `{count}` sentence that never shows an empty count: without one (or 0) the counted part is left out. */
const tc = (key: string, count: number | null | undefined, params: Record<string, unknown> = {}) =>
  t(mergeCountKey(key, count), { ...params, count: count ?? 0 })
/** The overlapping files: the merge state's list, else the count the automatic resolution sent (e.g. while paused). */
const fileCount = computed(() => state.value?.files.length || state.value?.autoResolve?.fileCount || 0)
const summary = computed(() => ({ before: tc('gui.inspector.merge.needs_choice.both', fileCount.value), file: '', after: '' }))

const reasonText = computed(() => {
  const reason = state.value?.reason ?? null
  if (!reason) return ''
  return t(`gui.inspector.merge.reasons.${mergeReasonKey(reason)}`, { reason })
})

// The server's automatic conflict resolution: shown as it is read; this screen never starts or stops it.
const autoResolve = computed(() => state.value?.autoResolve ?? null)
/** Running while the merge is in progress: the progress box itself tells it (title, text, the tries). */
const autoInBox = computed(() => mergeAutoRunning(autoResolve.value) && screen.value === 'catching_up')
/** The framed box: running anywhere but the progress box, or every try used up. */
const autoDetail = computed(() => {
  const auto = autoResolve.value
  if (!auto || screen.value === 'pick' || screen.value === 'loading') return false
  return auto.state === 'exhausted' || (auto.state === 'running' && !autoInBox.value)
})
const autoAiName = computed(() => mergeAutoCurrent(autoResolve.value)?.provider?.name ?? state.value?.defaultProvider?.name ?? t('gui.inspector.merge.needs_choice.ai_default'))
const autoTitle = computed(() => {
  const auto = autoResolve.value
  if (!auto) return ''
  if (auto.state === 'running') return t('gui.inspector.merge.auto.running_title', mergeAutoProgress(auto))
  return tc('gui.inspector.merge.auto.exhausted_title', auto.attempts.length || auto.maxAttempts)
})
/** Out of tries: no sentence; the buttons right below tell what to do. */
const autoText = computed(() => (autoResolve.value?.state === 'running'
  ? t('gui.inspector.merge.auto.running_text', { ai: autoAiName.value })
  : ''))
/** Every automatic try failed and nothing is settled yet: the automatic box is the window's one box (its files and tries). */
const exhaustedBox = computed(() => autoDetail.value && autoResolve.value?.state === 'exhausted' && screen.value === 'needs_choice' && !allResolved.value)
/** The other states are one short line. */
const autoNote = computed(() => {
  const auto = autoResolve.value
  if (!auto || screen.value === 'pick' || screen.value === 'loading') return ''
  return auto.state === 'succeeded' || auto.state === 'stopped_by_person' || auto.state === 'skipped_paused' ? t(`gui.inspector.merge.auto.states.${auto.state}`) : ''
})

const errorText = computed(() => {
  const failure = error.value
  if (!failure) return ''
  const code = failure.network ? 'network' : failure.code ?? 'error'
  const known = `gui.inspector.merge.error_codes.${code}`
  const message = te(known) ? t(known) : failure.message || String(failure.status ?? '')
  return t('gui.inspector.merge.error', { code, message })
})

// ① the step log and the bar: the files the AI has reconciled so far, or the merge itself.
const resolvedCount = computed(() => (state.value?.files ?? []).filter((file) => file.resolved).length)
const progressStyle = computed(() => {
  const total = state.value?.files.length ?? 0
  return total ? { width: `${Math.max(8, Math.round((resolvedCount.value / total) * 100))}%` } : {}
})
const steps = computed<{ key: string; state: StepState; text: string }[]>(() => {
  const current = state.value
  if (!current) return []
  const base = 'gui.inspector.merge.catching_up.steps'
  if (current.resolving) {
    const total = current.files.length
    const done = resolvedCount.value
    return [
      { key: 'found', state: 'ok', text: tc(`${base}.found`, total) },
      { key: 'resolve', state: total && done >= total ? 'ok' : 'run', text: tc(`${base}.resolve`, total, { done, ai: aiName.value }) },
      { key: 'verify', state: 'todo', text: t(`${base}.verify`) },
      { key: 'merge', state: 'todo', text: t(`${base}.merge_after`) },
    ]
  }
  return [
    { key: 'approved', state: 'ok', text: t(`${base}.approved`) },
    { key: 'merging', state: 'run', text: t(`${base}.merging`) },
    { key: 'done', state: 'todo', text: t(`${base}.done`) },
  ]
})

// ② [AI로 해결] beside its AI select: the card's own work AI by default (the server's default_provider).
const providerId = ref('')
const aiName = computed(() => {
  const chosen = providers.value.find((provider) => provider.id === providerId.value)
  return chosen?.name ?? state.value?.defaultProvider?.name ?? t('gui.inspector.merge.needs_choice.ai_default')
})
const defaultLabel = computed(() => {
  const provider = state.value?.defaultProvider
  return provider ? t('gui.inspector.merge.needs_choice.ai_default_named', { name: provider.name }) : t('gui.inspector.merge.needs_choice.ai_default')
})
const otherProviders = computed(() => providers.value.filter((provider) => provider.id !== state.value?.defaultProvider?.id))

watch(() => screen.value === 'needs_choice' || screen.value === 'pick', (choosing) => {
  if (choosing) void loadProviders()
}, { immediate: true })

async function startAi(): Promise<void> {
  await aiResolve(providerId.value || null)
}

// ③ per-file pick: every file starts on the AI; a side that deleted the file cannot be picked.
const choices = reactive<Record<string, Side>>({})
function sideAvailable(kind: FileKind, side: Side): boolean {
  return !((kind === 'deleted_latest' && side === 'latest') || (kind === 'deleted_card' && side === 'card'))
}
function openPick(): void {
  for (const file of openFiles.value) if (!choices[file.path]) choices[file.path] = 'ai'
  picking.value = true
}

async function submitPick(): Promise<void> {
  await pick(openFiles.value.map((file) => ({ path: file.path, side: choices[file.path] ?? 'ai' })), providerId.value || null)
}

const confirmOpen = ref(false)
async function discard(): Promise<void> {
  confirmOpen.value = false
  await discardRerun()
}

// flowgate.v02test.0084 (F84-8, 시안 jvj9ff9t §2): [병합 취소] of a merge an AI resolved — the Designer's Graph Time Machine
// of this card (props.win.undo): a confirmation names the cards it takes back and the ones it leaves, then it runs and the
// Designer shows the outcome. Disabled with the reason while the card cannot be rewound (the run not idle, …).
const undoShown = computed(() => screen.value === 'merged' && aiResolved.value && !!props.win?.undo)
const undoBlocked = computed(() => {
  if (!undoShown.value) return null
  const reason = props.win?.undo?.blocked() ?? null
  return reason && te(`gui.inspector.merge.undo.blocked.${reason}`) ? reason : reason ? 'unavailable' : null
})
const undoReasonId = `merge-undo-reason-${Math.random().toString(36).slice(2, 10)}`
const undoOpen = ref(false)
const undoBusy = ref(false)
const undoFailed = ref(false)
const undoImpact = ref<{ back: readonly string[]; keep: readonly string[] }>({ back: [], keep: [] })
function openUndo(): void {
  if (!props.win?.undo || undoBlocked.value) return
  undoImpact.value = props.win.undo.impact()
  if (!undoImpact.value.back.length) undoImpact.value = { back: [props.target.title], keep: undoImpact.value.keep }
  undoFailed.value = false
  undoOpen.value = true
}
async function runUndo(): Promise<void> {
  const undo = props.win?.undo
  if (!undo || undoBusy.value) return
  undoBusy.value = true
  let done = false
  try {
    done = await undo.run()
  } catch {
    done = false
  } finally {
    undoBusy.value = false
    undoOpen.value = false
    undoFailed.value = !done
    props.win?.refresh()
  }
  // Undone: the merge this window showed is gone; the Designer's Time Machine dialog tells the outcome.
  if (done) close()
  else void reload()
}

/** The footer shows a screen's buttons (kept at the window's bottom). */
const footShown = computed(() => {
  const current = screen.value
  if (current === 'catching_up') return autoInBox.value && (can('ai_resolve') || (can('pick') && openFiles.value.length > 0))
  if (current === 'needs_choice' || current === 'pick') return true
  if (current === 'failed') return can('retry') || can('discard_rerun')
  return undoShown.value
})

function goCard(nodeKey: string): void {
  openCardMerge(props.target.flowId, nodeKey)
}

watch(() => props.target.nodeId, () => {
  confirmOpen.value = false
  undoOpen.value = false
  undoFailed.value = false
  providerId.value = ''
  for (const key of Object.keys(choices)) delete choices[key]
})
</script>

<style scoped>
/* The mockup's values (conflict.html .mz / .files / .b / .pick / .log / .meta), on the Designer's palette. */
.merge-window { display: flex; flex-direction: column; gap: 8px; min-width: 0; font-size: 12.5px; }
.mz {
  min-width: 0;
  padding: 12px 13px;
  border: 1px solid rgba(255, 100, 120, 0.45);
  border-radius: 10px;
  background: linear-gradient(180deg, rgba(255, 100, 120, 0.09), rgba(255, 100, 120, 0.03));
}
.mz.is-info { border-color: rgba(75, 163, 255, 0.4); background: linear-gradient(180deg, rgba(75, 163, 255, 0.1), rgba(75, 163, 255, 0.03)); }
.mz.is-ok { border-color: rgba(34, 201, 140, 0.45); background: linear-gradient(180deg, rgba(34, 201, 140, 0.1), rgba(34, 201, 140, 0.03)); }
.mz-title { display: flex; align-items: center; gap: 7px; margin: 0 0 4px; font-size: 13.5px; font-weight: 700; }
.mz-title svg, .mz-b svg, .mz-fr svg { fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; }
.mz-title svg { flex: none; width: 16px; height: 16px; }
.mz-title.is-danger { color: #ff96a6; }
.mz-title.is-info { color: #7cc0ff; }
.mz-title.is-ok { color: #4fe0b0; }
.mz-text { margin: 2px 0 8px; color: #c0d0e2; font-size: 12.5px; line-height: 1.6; overflow-wrap: anywhere; }
.mz-text b { color: #eef5ff; }
.mz-meta { margin: 8px 0 0; color: #8ea5bd; font-size: 11.5px; line-height: 1.6; }
.mz-note { margin: 0; color: #8ea5bd; }
.mz-alert { margin: 0; color: #ff96a6; }
.mz-link { padding: 0; border: 0; background: none; color: #7cc0ff; font: inherit; cursor: pointer; }
.mz-link:hover { text-decoration: underline; }
.mz-files { overflow: hidden; margin: 8px 0 10px; border: 1px solid #233650; border-radius: 8px; }
.mz-fr { display: flex; align-items: center; gap: 8px; min-width: 0; padding: 7px 10px; border-bottom: 1px solid #1b2b42; background: #0b1828; font-size: 12px; }
.mz-fr:last-child { border-bottom: 0; }
.mz-fr svg { flex: none; width: 14px; height: 14px; color: #8fb0d0; }
.mz-fr code { flex: 1 1 auto; min-width: 0; overflow: hidden; color: #dbe8f6; font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; text-overflow: ellipsis; white-space: nowrap; }
.mz-why { flex: none; color: #8ea5bd; font-size: 11px; white-space: nowrap; }
.mz-why.is-done { color: #4fe0b0; }
.mz-btns { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
/* Pinned to the bottom of the window's scrolling body (its 12px padding), so the buttons never scroll out of sight. */
.mz-foot { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; position: sticky; bottom: -12px; z-index: 1; margin: 0 -14px -12px; padding: 8px 14px; border-top: 1px solid #233650; background: var(--fg2-panel, #0b1626); }
.mz-why-off { color: #8ea5bd; font-size: 11.5px; }
.mz-undo-list { margin: 0 0 4px 16px; padding: 0; font-size: 12.5px; line-height: 1.7; color: #a9bed4; }
.mz-undo-list .is-back { color: #ff96a6; }
.mz-undo-list .is-keep { color: #4fe0b0; }
.mz-btns-gap { margin-top: 8px; }
.mz-b {
  display: inline-flex; align-items: center; gap: 6px; height: 30px; padding: 0 12px;
  border: 1px solid #2b4566; border-radius: 7px; background: #13263d; color: #dbe8f6;
  font: inherit; font-size: 12.5px; font-weight: 700; white-space: nowrap; cursor: pointer;
}
.mz-b svg { flex: none; width: 15px; height: 15px; }
.mz-b:hover:not(:disabled) { filter: brightness(1.15); }
.mz-b:disabled { cursor: not-allowed; opacity: 0.55; }
.mz-b.is-pri { border-color: #3b82f6; background: #2563eb; color: #fff; }
.mz-b.is-dng { border-color: rgba(255, 100, 120, 0.45); background: transparent; color: #ff96a6; }
.mz-aisel { display: inline-flex; align-items: center; min-width: 0; }
.mz-aisel select {
  height: 30px; max-width: 100%; padding: 0 10px; border: 1px solid #2b4566; border-radius: 7px;
  background: #0b1828; color: #cfe0f3; font: inherit; font-size: 12px; cursor: pointer;
}
.mz-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.mz-pick { display: inline-flex; flex: none; overflow: hidden; border: 1px solid #2b4566; border-radius: 6px; }
.mz-pick button { padding: 3px 8px; border: 0; border-right: 1px solid #2b4566; background: transparent; color: #9fb3c8; font: inherit; font-size: 11px; cursor: pointer; white-space: nowrap; }
.mz-pick button:last-child { border-right: 0; }
.mz-pick button.on { background: #1d3a5f; color: #fff; font-weight: 700; }
.mz-pick button:disabled { cursor: not-allowed; opacity: 0.4; }
.mz-prog { height: 6px; margin: 6px 0 8px; overflow: hidden; border-radius: 3px; background: #1b2b42; }
.mz-prog i { display: block; width: 8%; height: 100%; background: linear-gradient(90deg, #3b82f6, #7cc0ff); transition: width 0.3s ease; }
.mz-prog.is-indeterminate i { width: 35%; animation: mz-slide 1.4s ease-in-out infinite; }
.mz-log { padding: 8px 10px; border: 1px solid #1b2b42; border-radius: 6px; background: #08131f; color: #a9bed4; font-family: ui-monospace, Consolas, monospace; font-size: 11.5px; line-height: 1.7; }
.mz-log-ok { color: #4fe0b0; }
.mz-log-run { color: #7cc0ff; }
.mz-log-todo { opacity: 0.5; }
.mz-spin { display: inline-grid; place-items: center; }
.mz-spin svg { animation: mz-spin 1s linear infinite; }
@keyframes mz-spin { to { transform: rotate(360deg); } }
@keyframes mz-slide { 0% { transform: translateX(-100%); } 100% { transform: translateX(300%); } }
</style>
