<template>
  <ModelessShell kind-color="#f0a23a" v-if="card && state && draft" ref="shell" :open="state.open" :minimized="state.minimized"
    :title="`${t(k('name'))} · ${caseLabel}`" :initial-x="geometry('x', 220)" :initial-y="geometry('y', 130)" :initial-width="geometry('width', 720)" :initial-height="geometry('height', 700)"
    :close-on-escape="false" @update:minimized="state.minimized = $event" @close="close">
    <!-- the kind, then — muted, as the write window names its card — which case: its id and title once. -->
    <template #header><span class="heading" data-test="case-heading"><span class="heading-kind">{{ t(k('name')) }}</span><span class="heading-case" :title="caseLabel" data-test="case-window-id">{{ caseLabel }}</span></span></template>
    <div class="case" data-test="case-window">
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <template v-if="card.writing.value === 'rewrite-case' && card.writingCaseId.value === state.caseId"><p class="placeholder" data-test="case-writing">{{ t(k('writing')) }}</p></template>
      <template v-else>
        <!-- F81 case fields, in this order: title · content · procedure · expected · check · command · result · evidence · remarks. -->
        <label><span>{{ t(k('title')) }}</span><Input v-model="draft.title" :disabled="locked" data-test="case-title" /></label>
        <label><span>{{ t(k('content')) }}</span><Textarea v-model="draft.content" :rows="3" :placeholder="t(k('content_placeholder'))" :disabled="locked" data-test="case-content" /></label>
        <label v-for="field in textFields" :key="field"><span>{{ t(k(field)) }}</span><Textarea v-model="draft[field]" :rows="4" :disabled="locked" :data-test="`case-${field}`" /></label>
        <div class="field" role="group" :aria-label="t(k('mode'))"><span>{{ t(k('mode')) }}</span><div class="control"><InspectorSegment :model-value="draft.check" :options="checkOptions" :disabled="locked" :aria-label="t(k('mode'))" data-test="case-mode" @update:model-value="draft.check = $event === 'code' ? 'code' : 'manual'" /></div></div>
        <!-- shown for a code case only. No language or framework is assumed; one command says what runs, and why it cannot run shows under it. -->
        <label v-if="draft.check === 'code'"><span>{{ t(k('command')) }}</span><span class="command"><Input v-model="draft.run_command" :placeholder="t(k('command_placeholder'))" class="mono" :disabled="locked" data-test="case-command" />
          <InspectorReason v-if="commandCode" tone="danger" :text="commandText" data-test="case-command-error" /></span></label>
        <!-- The result is on the window from the start: four buttons, anyone may press one, and who did is shown. -->
        <div class="field" role="group" :aria-label="t(k('result'))"><span>{{ t(k('result')) }}</span><div class="control">
          <InspectorSegment :model-value="skipping ? 'skip' : shownResult" :options="resultOptions" :disabled="!resultEnabled" :aria-label="t(k('result'))" data-test="case-result" @update:model-value="pickResult" />
          <span v-if="resultBy" class="hint" data-test="case-result-by">{{ resultBy }}</span>
          <span v-if="!skipping && row?.result === 'skip' && row.skipReason" class="hint" data-test="case-skip-shown">{{ t(k('skip_reason')) }}: {{ row.skipReason }}</span>
          <span v-if="skipping" class="skip"><Input v-model="skipReason" :placeholder="t(k('skip_reason_placeholder'))" data-test="case-skip-reason" @keydown.enter.prevent="recordSkip" />
            <Button size="sm" variant="primary" :disabled="!skipReason.trim() || !resultEnabled" data-test="case-skip-submit" @click="recordSkip">{{ t(k('record')) }}</Button>
            <Button size="sm" variant="secondary" data-test="case-skip-cancel" @click="skipping = false">{{ t(k('cancel')) }}</Button></span>
          <span v-if="resultReason" class="hint" data-test="case-result-reason">{{ resultReason }}</span>
          <InspectorReason v-if="resultFailure" tone="danger" :text="resultFailure" data-test="case-result-failure" />
        </div></div>
        <EvidenceFiles v-if="!adding && card.view.value?.tsDocId && state.caseId" :ts-id="card.view.value.tsDocId" :case-id="state.caseId" :revision="card.view.value" @eligibility="passReason = $event" @uploaded="card.reload()" />
        <!-- Remarks: one line only. It opens the modeless remark window beside this one. -->
        <div class="field" role="group" :aria-label="t(k('remarks'))"><span>{{ t(k('remarks')) }}</span><div class="control">
          <button type="button" class="remark-line" :class="{ 'remark-line--none': !remarkTally.open && !remarkTally.resolved }" :disabled="adding" data-test="case-remarks" @click="openRemarks">{{ remarkLabel }}</button>
        </div></div>
      </template>
      <template v-if="conflict"><InspectorReason tone="danger" :text="t(k('conflict'))" data-test="case-conflict" /><Button size="sm" variant="secondary" data-test="case-reload" @click="resetDraft">{{ t(k('reload')) }}</Button></template>
      <InspectorReason v-if="!conflict && saveReason" :text="saveReason" />
      <template v-if="refusal && !conflict && (!commandCode || otherErrors.length)"><InspectorReason tone="danger" :text="refusalText" data-test="case-refusal" /><ul class="errors"><li v-for="(error, i) in otherErrors" :key="i">{{ error.caseId }} · {{ error.field }} · {{ error.message }}</li></ul></template>
      <div v-if="confirming" class="confirm" role="group" data-test="case-delete-confirm"><span>{{ t(k('delete_confirm'), { id: draft.original.case_id }) }}</span>
        <Button size="sm" variant="secondary" @click="confirming = false">{{ t(k('cancel')) }}</Button>
        <Button size="sm" variant="danger" :disabled="conflict || !card.can('caseDelete').enabled" :title="deleteReason" data-test="case-delete-submit" @click="remove">{{ t(k('delete')) }}</Button>
      </div>
      <!-- Existing card footer: [삭제] · [저장] · [닫기]. Unimplemented actions stay hidden. -->
      <div class="case-bar">
        <Button v-if="!adding" size="sm" variant="secondary" class="case-delete" :disabled="!card.can('caseDelete').enabled || conflict || confirming" :title="deleteReason" data-test="case-delete" @click="confirming = true">{{ t(k('delete')) }}</Button>
        <Button size="sm" variant="primary" :disabled="!valid || locked || conflict || checking" :loading="checking" :title="saveReason" data-test="case-save" @click="save">{{ t(k('save')) }}</Button>
        <Button size="sm" variant="secondary" data-test="case-close" @click="close">{{ t(k('close')) }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Input from '@/components/ui/Input.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { testSpecCasePayload, type TestCaseResult } from '@/features/document'
import { documentHistoryWhen, TestEvidenceFiles as EvidenceFiles } from '@/features/documentWindow'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import InspectorSegment from '../../../parts/InspectorSegment.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { caseDraft, newCaseDraft, addedCase, mergeCase, runChanged, scenarioSnapshot, sessionDraft, useScenarioWindow, notifySaved, type CaseDraft } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t, locale } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.case.${key}`
const { card, shell, state, close, current, geometry } = useScenarioWindow(() => props.target, 'case')
const confirming = ref(false), checking = ref(false)
const adding = computed(() => state.value?.caseId === '__new__')
const draft = computed(() => {
  const c = card.value, item = c?.cases.value.find(item => item.caseId === state.value?.caseId)
  if (c && adding.value) return sessionDraft<CaseDraft>(c.drafts, 't2:case:__new__', () => newCaseDraft(c.cases.value, c.view.value?.details?.title ?? null, c.view.value?.tsDocId ?? null))
  return c && item ? sessionDraft<CaseDraft>(c.drafts, `t2:case:${item.caseId}`, () => caseDraft(item, c.cases.value, c.view.value?.details?.title ?? null, c.view.value?.tsDocId ?? null)) : null
})
/** Which case the window edits: its id and, once written, its title. */
const caseLabel = computed(() => [draft.value?.original.case_id, draft.value?.original.title?.trim()].filter(Boolean).join(' · '))
// Loading, failed and partial reads cannot prove that the target disappeared.
watch(() => [card.value?.test.status.value, card.value?.view.value, card.value?.writing.value,
  card.value?.busy.value, state.value?.open, state.value?.caseId] as const, () => {
  const c = card.value, id = state.value?.caseId
  if (adding.value || !c || !state.value?.open || !id || c.test.status.value !== 'ready' || c.writing.value
    || c.test.view.value?.details?.complete !== true || c.formatErrors.value.length
    || c.busy.value === 'caseDelete' || c.cases.value.some(item => item.caseId === id)) return
  close()
  notifySaved(t(k('missing'), { id }), 'info')
}, { immediate: true, flush: 'sync' })
const textFields = ['procedure', 'expected'] as const
/** The draft's pin must be the very snapshot the draft was shown from (§2.15.3); a pin of another snapshot is a conflict, never a rebase. */
const pinStale = computed(() => !!draft.value?.pin && draft.value.snapshot !== scenarioSnapshot(draft.value.pin.cases, draft.value.pin.title))
const pinReady = computed(() => !!draft.value?.pin && !pinStale.value)
async function ensurePin(d: CaseDraft | null) {
  const c = card.value
  if (!d || !c || d.pin || d.pinState === 'loading') return
  d.pinState = 'loading'
  const pin = await c.pinCases()
  if (d.pin) return
  if (pin) { d.pin = pin; d.pinState = 'idle' } else d.pinState = 'failed'
}
watch(draft, (d) => { void ensurePin(d) }, { immediate: true })
// A failed pin read is tried again when the window is opened again (a pin that exists is never replaced).
watch(() => state.value?.open, (open) => { if (open && draft.value?.pinState === 'failed') { draft.value.pinState = 'idle'; void ensurePin(draft.value) } })
const conflict = computed(() => pinStale.value || !!draft.value && !!card.value && card.value.test.status.value === 'ready' && card.value.view.value?.details?.complete === true && (draft.value.docId !== card.value.view.value?.tsDocId || draft.value.snapshot !== scenarioSnapshot(card.value.cases.value, card.value.view.value?.details?.title ?? null)))
// What a case must carry (server REQUIRED_CASE_FIELDS): its title, procedure and expected result; the check is always set.
const valid = computed(() => pinReady.value && !!draft.value && (['title', 'procedure', 'expected'] as const).every(field => !!draft.value![field].trim()))
function builderFor(deleting: boolean, d: CaseDraft) {
  // Derived from the pin's own cases (the same v2 response as the base revision), never from the separately read v1 view.
  return (cases: Parameters<typeof mergeCase>[0]) => deleting ? cases.filter(item => item.caseId !== d.original.case_id).map(testSpecCasePayload) : adding.value ? addedCase(cases, d) : mergeCase(cases, d)
}
const locked = computed(() => !card.value?.can(adding.value ? 'caseAdd' : 'caseSave').enabled)
const saveReason = computed(() => conflict.value ? t(k('conflict')) : !valid.value ? t(k('required_fields')) : reason(adding.value ? 'caseAdd' : 'caseSave'))
const deleteReason = computed(() => conflict.value ? t(k('conflict')) : reason('caseDelete'))
function reason(op: 'caseSave' | 'caseAdd' | 'caseDelete' | 'rewriteCase') { const key = card.value?.can(op).reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' }
const refusal = computed(() => ['caseSave', 'caseAdd', 'caseDelete'].includes(card.value?.refusal.value?.operation ?? '') ? card.value?.refusal.value : null)
const refusalText = computed(() => refusal.value ? [t(`gui.inspector.testSpec.${refusal.value.key}`), refusal.value.message].filter(Boolean).join(' · ') : '')
// Why the command cannot run (B0005 §2): the server's run judgment, found before [저장] writes or by the save's refusal.
const commandCode = ref<string | null>(null)
const commandText = computed(() => t(`gui.inspector.testSpec.reason.${['command_missing', 'command_invalid'].includes(commandCode.value ?? '') ? commandCode.value : 'automation_unbound'}`))
const otherErrors = computed(() => refusal.value?.fieldErrors.filter(error => error.field !== 'run_command') ?? [])
watch(() => [draft.value?.run_command, draft.value?.check, state.value?.caseId], () => { commandCode.value = null })
/** An unchanged command keeps the server's judgment of it; a changed one must be written, the server judges it on save. */
function commandProblem(d: CaseDraft): string | null {
  if (d.check !== 'code') return null
  if (!adding.value && !runChanged(d)) {
    const capability = card.value?.view.value?.caseCapabilities[d.original.case_id]
    return !capability || ['case_selectable', 'suite_only'].includes(capability) ? null : capability
  }
  return d.run_command.trim() ? null : 'command_missing'
}
// The two checks. An earlier case's mode is already read as one of them (automated -> code, the rest -> manual).
const checkOptions = computed(() => [{ value: 'code', label: t(k('code')) }, { value: 'manual', label: t(k('manual')) }])

// ── Result: not run / pass / fail / skip ───────────────────────────────────────────────────────────
const row = computed(() => card.value?.scenarioRows.value.find(item => item.caseId === state.value?.caseId) ?? null)
const shownResult = computed<TestCaseResult>(() => row.value?.result ?? 'not_run')
const passReason = ref<string | null>('evidence_unavailable')
watch(() => [state.value?.caseId, card.value?.view.value?.tsDocId], () => { passReason.value = 'evidence_unavailable' }, { flush: 'sync' })
const passWords = computed(() => {
  const lang = locale.value.split('-')[0]
  if (passReason.value === 'exit_zero_required') return lang === 'ko' ? '정상 종료(종료 코드 0)가 필요합니다.' : lang === 'ja' ? '正常終了（終了コード0）が必要です。' : 'A normal exit (code 0) is required.'
  if (passReason.value === 'output_evidence_required') return lang === 'ko' ? '현재 시도의 출력 증거 파일이 필요합니다.' : lang === 'ja' ? '現在の試行の出力証拠ファイルが必要です。' : 'An output evidence file for this attempt is required.'
  return lang === 'ko' ? '증거 상태를 확인할 수 없습니다.' : lang === 'ja' ? '証拠の状態を確認できません。' : 'Evidence status is unavailable.'
})
const resultOptions = computed(() => (['not_run', 'pass', 'fail', 'skip'] as const).map(value => ({ value, label: t(k(`result_${value}`)), disabled: value === 'pass' && !!passReason.value, title: value === 'pass' && passReason.value ? passWords.value : undefined })))
const resultAvailability = computed(() => adding.value || !state.value?.caseId ? { enabled: false, reason: 'guard.save_first' } : card.value?.can('resultEntry', state.value.caseId) ?? { enabled: false, reason: 'reason.busy' })
const resultEnabled = computed(() => resultAvailability.value.enabled && !recording.value)
const resultReason = computed(() => { const key = resultAvailability.value.reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' })
/** Who pressed the result, as recorded: the name, whether an AI or a person, and when. */
const resultBy = computed(() => {
  const by = row.value?.resultBy
  if (!by || row.value?.result === 'not_run') return ''
  return [t(k(`by_${by.kind}`)), by.name, row.value?.resultAt ? documentHistoryWhen(row.value.resultAt) : null].filter(Boolean).join(' · ')
})
const skipping = ref(false), skipReason = ref(''), recording = ref(false), resultFailure = ref('')
watch(() => state.value?.caseId, () => { confirming.value = false; skipping.value = false; skipReason.value = ''; resultFailure.value = '' })
async function record(result: TestCaseResult, reasonText?: string) {
  const c = card.value, id = state.value?.caseId
  if (result === 'pass' && passReason.value) return
  if (!current() || !c || !id || adding.value || recording.value || !c.can('resultEntry', id).enabled) return
  recording.value = true; resultFailure.value = ''
  try {
    const ok = await c.enterResult({ case_id: id, result, ...(result === 'skip' ? { skip_reason: reasonText ?? '' } : {}) })
    if (!current()) return
    if (ok) { skipping.value = false; skipReason.value = '' }
    else { const key = c.test.failure.value?.key; resultFailure.value = key ? t(`gui.inspector.testSpec.${key}`) : t(k('result_failed')) }
  } finally { if (current()) recording.value = false }
}
/** A skip is recorded with its one-line reason; the other three at once. */
function pickResult(value: string) {
  if (value === 'pass' && passReason.value) return
  if (value === 'skip') { skipping.value = true; return }
  skipping.value = false
  if (value === 'not_run' || value === 'pass' || value === 'fail') void record(value)
}
function recordSkip() { if (skipReason.value.trim()) void record('skip', skipReason.value.trim()) }

// ── Evidence boxes and the remark line ─────────────────────────────────────────────────────────────
const remarkTally = computed(() => adding.value || !state.value?.caseId ? { open: 0, resolved: 0 } : card.value?.remarkCounts(state.value.caseId) ?? { open: 0, resolved: 0 })
const remarkLabel = computed(() => remarkTally.value.open || remarkTally.value.resolved ? t(k('remarks_line'), { open: remarkTally.value.open, resolved: remarkTally.value.resolved }) : t(k('remarks_add')))
function openRemarks() { const id = state.value?.caseId; if (id && !adding.value) card.value?.openWindow('remark', id) }

async function commit(deleting: boolean) {
  const c = card.value, d = draft.value, op = deleting ? 'caseDelete' : adding.value ? 'caseAdd' : 'caseSave'
  if (!current() || !c || !d || checking.value || conflict.value || (!deleting && !valid.value) || !c.can(op).enabled) return
  commandCode.value = deleting ? null : commandProblem(d)
  if (commandCode.value) return
  checking.value = true
  try {
    await c.reload()
    if (!current() || draft.value !== d || conflict.value || c.test.status.value !== 'ready' || !c.can(op).enabled) return
    if (!d.pin || pinStale.value) return
    const ok = await c.saveCases(d.pin, builderFor(deleting, d), op)
    if (!ok && current() && !deleting) commandCode.value = c.refusal.value?.fieldErrors.find(error => error.field === 'run_command')?.code ?? null
    if (!ok || !current() || state.value?.caseId !== (adding.value ? '__new__' : d.original.case_id)) return
    delete c.drafts[`t2:case:${adding.value ? '__new__' : d.original.case_id}`]
    close()
    notifySaved(t(k(deleting ? 'deleted' : 'saved'), { id: d.original.case_id }))
  } finally { if (current()) checking.value = false }
}
const save = () => commit(false)
const remove = () => { if (confirming.value) return commit(true) }
/** Adopts the latest snapshot explicitly: a new draft (and a new pin) is made from the current view; the old pin is dropped with it. */
function resetDraft() { if (card.value && draft.value) { delete card.value.drafts[`t2:case:${adding.value ? '__new__' : draft.value.original.case_id}`]; confirming.value = false } }
</script>
<style scoped>
/* The card memo window's form: the fields, then the bar at the bottom. */
.case { display: flex; flex-direction: column; gap: 12px; min-height: 100%; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
label, .field { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 12px; align-items: start; } label > span, .field > span { color: var(--fg2-muted); padding-top: 7px; }
.control { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; } .hint { color: var(--fg2-muted); font-size: 11.5px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; } /* The command and, under it, why it cannot run; not a label caption (label > span). */
.command { display: flex; flex-direction: column; min-width: 0; padding-top: 0; color: inherit; }
.skip { display: flex; align-items: center; gap: 6px; width: 100%; }
/* An evidence box: what it holds, then the add / drop place. */
.evidence { flex-direction: row; flex-wrap: wrap; align-items: center; gap: 10px; width: 100%; padding: 6px 8px; border: 1px dashed var(--fg2-line-strong); border-radius: 8px; box-sizing: border-box; }
.drop { margin-left: auto; padding: 2px 8px; border: 0; background: none; color: var(--fg2-muted); font: inherit; font-size: 11.5px; }
/* The remark line: one button the width of the field. */
.remark-line { width: 100%; padding: 6px 10px; border: 1px solid var(--fg2-line-strong); border-radius: 8px; background: none; color: var(--fg2-yellow, #d9a441); font: inherit; text-align: left; cursor: pointer; }
.remark-line--none { color: var(--fg2-muted); } .remark-line:disabled { cursor: default; opacity: 0.6; }
/* The write window's heading (SlotWriteWindow): the kind, then the case muted and cut with an ellipsis. */
.heading { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; } .heading-kind { flex: 0 0 auto; }
.heading-case { overflow: hidden; color: var(--fg2-muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
.confirm { display: flex; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; padding: 8px 10px; border: 1px solid #71313d; border-radius: 8px; background: #2a1820; color: #ffc2cc; font-size: 12px; }
.confirm > span { flex: 1; }
.case-bar { display: flex; flex: none; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--fg2-line); }
/* [삭제]: a soft red button — the strong red stays with the confirm step (the card memo window's). */
.case-bar .case-delete { border-color: rgba(255, 110, 130, 0.45); background: rgba(225, 75, 96, 0.14); color: #ff9eae; }
.case-bar .case-delete:hover:not(:disabled) { border-color: rgba(255, 130, 150, 0.7); background: rgba(225, 75, 96, 0.24); color: #ffd0d7; }
.errors { color: var(--fg2-status-danger-fg); } .placeholder { color: var(--fg2-muted); }
@media (max-width: 520px) { label, .field { grid-template-columns: 1fr; gap: 4px; } }
</style>
