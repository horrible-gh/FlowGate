<template>
  <ModelessShell v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 90)" :initial-y="geometry('y', 80)" :initial-width="geometry('width', 1040)" :initial-height="geometry('height', 640)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header>{{ t(k('title')) }}</template>
    <template #actions>
      <span class="window-actions" @mousedown.stop>
      <Dropdown><template #trigger="{ toggle, triggerAttrs }"><Button size="sm" variant="secondary" v-bind="triggerAttrs" data-test="scenario-history" @click="toggle">{{ t(k('history'), { n: reviewCount }) }} ▾</Button></template>
        <div class="history">
          <InspectorPaneState v-if="card.reviewHistory.status.value === 'loading'" state="loading" />
          <InspectorPaneState v-else-if="card.reviewHistory.status.value === 'error'" state="error" retryable @retry="card.reviewHistory.reload()" />
          <p v-else-if="!reviewCount">{{ t(k('history_empty')) }}</p>
          <article v-for="review in card.reviewHistory.data.value?.reviews ?? []" :key="review.id"><b data-test="scenario-history-verdict">{{ t(`gui.document_window.history.verdict.${documentHistoryVerdictKey(review.verdict)}`, { n: review.findingCount }) }}</b> · {{ review.reviewerName }} · <span data-test="scenario-history-time">{{ documentHistoryWhen(review.at) }}</span><p>{{ review.comment }}</p><p v-for="(finding, i) in review.findings" :key="i">{{ finding.locus }} · {{ finding.note }}</p></article>
          <article v-for="rejection in card.reviewHistory.data.value?.rejections ?? []" :key="rejection.id"><b>{{ t(k('rejected')) }}</b> · {{ rejection.rejectionProviderName ?? rejection.rejectedBy }} · {{ documentHistoryWhen(rejection.rejectedAt) }}<p>{{ rejection.reason }}</p><p>{{ rejection.aiResponse }}</p></article>
        </div>
      </Dropdown>
      <Button size="sm" variant="secondary" data-test="scenario-raw" @click="openRaw">{{ t(k('raw')) }}</Button>
      <InspectorAiButton :label="t(k('rewrite'))" :title="reason('rewriteAll') || t(k('rewrite'))" :disabled="!card.can('rewriteAll').enabled" data-test="scenario-rewrite" @click="rewrite" />
      <Button size="sm" :disabled="!card.can('caseAdd').enabled" :title="reason('caseAdd')" data-test="scenario-add" @click="card.openWindow('case', '__new__')">{{ t(k('add')) }}</Button>
      </span>
    </template>
    <div class="scenario" data-test="scenario-window">
      <h3>{{ t(k('cases')) }} <small>{{ t(k('count'), { n: card.cases.value.length }) }}<template v-if="summary"> · {{ summary }}</template></small></h3>
      <template v-if="card.formatErrors.value.length"><p class="errors">{{ t(k('errors'), { n: card.formatErrors.value.length }) }}</p><ul class="errors" data-test="scenario-errors"><li v-for="(error, i) in card.formatErrors.value" :key="i">{{ error.caseId }} · {{ error.field }} · {{ error.message }}</li></ul></template>
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <div class="table-scroll"><table><thead><tr><th v-for="col in columns" :key="col">{{ t(k(col)) }}</th></tr></thead>
        <tbody v-if="card.writing.value"><tr v-for="i in Math.max(3, card.cases.value.length)" :key="i" class="placeholder" data-test="scenario-writing"><td colspan="7">{{ t(k('writing')) }}</td></tr></tbody>
        <tbody v-else><tr v-for="item in card.cases.value" :key="item.caseId" :data-case="item.caseId">
          <td><TestMarkBadge v-if="resultMark(item.caseId)" :mark="resultMark(item.caseId)!" /></td><td class="case-id">{{ item.caseId }}</td><td>{{ item.title }}</td><td>{{ item.fields.requirement }}</td>
          <td>{{ t(k(modeKey(item.executionMode))) }}<span v-if="item.executionMode === 'automated' && !['case_selectable', 'suite_only'].includes(card.view.value?.caseCapabilities[item.caseId] ?? '')" class="warning"> · {{ t(k('no_code')) }}</span></td>
          <td :class="{ warning: !item.required }">{{ t(k(item.required ? 'stop' : 'ignore')) }}</td>
          <td><IconButton :label="t(k('edit'), { id: item.caseId })" :title="t(k('edit'), { id: item.caseId })" variant="ghost" size="sm" data-test="scenario-edit" @click="card.openWindow('case', item.caseId)">✎</IconButton></td>
        </tr></tbody></table></div>
      <section v-if="raw.open" class="raw" data-test="scenario-raw-overlay">
        <h3>{{ t(k('raw_title')) }}</h3>
        <ul v-if="card.formatErrors.value.length" class="errors" data-test="raw-errors"><li v-for="(error, i) in card.formatErrors.value" :key="i">{{ error.caseId }} · {{ error.field }} · {{ error.message }}</li></ul>
        <InspectorPaneState v-if="reading" state="loading" />
        <InspectorPaneState v-else-if="readError" state="error" :message="readError" retryable @retry="loadRaw" />
        <Textarea v-else v-model="raw.text" class="mono raw-text" :rows="16" :disabled="!card.can('rawSave').enabled" data-test="scenario-raw-text" />
        <template v-if="rawConflict"><InspectorReason tone="danger" :text="t(k('conflict'))" /><Button size="sm" variant="secondary" data-test="raw-reload" @click="loadRaw">{{ t(k('reload')) }}</Button></template>
        <InspectorReason v-if="reason('rawSave')" :text="reason('rawSave')" />
        <InspectorReason v-if="!rawConflict && card.refusal.value?.operation === 'rawSave'" tone="danger" :text="refusalText" data-test="raw-refusal" />
        <footer><Button size="sm" variant="secondary" @click="raw.open = false">{{ t(k('close')) }}</Button>
          <Button size="sm" :disabled="!raw.loaded || reading || !!readError || raw.text === raw.baseline || rawConflict || !card.can('rawSave').enabled" :title="reason('rawSave')" data-test="scenario-raw-save" @click="saveRaw">{{ t(k('save')) }}</Button></footer>
      </section>
      <ToastHost :toasts="toasts" @dismiss="toasts = []" />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import IconButton from '@/components/ui/IconButton.vue'
import Textarea from '@/components/ui/Textarea.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { documentContentRead, documentContentSave } from '@/features/document'
import { TestMarkBadge, testVerdictMark, TEST_PENDING_MARK, documentHistoryVerdictKey, documentHistoryWhen } from '@/features/documentWindow'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import InspectorAiButton from '../../../parts/InspectorAiButton.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import type { TestCardCommand } from '../useTestCard'
import { sessionDraft, useScenarioWindow, type RawDraft } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.scenario.${key}`
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'scenario')
const reading = ref(false), readError = ref(''), rawConflict = ref(false)
const toasts = ref<ToastHostItem[]>([])
const raw = computed(() => sessionDraft<RawDraft>(card.value!.drafts, 't2:raw', () => ({ docId: '', baseline: '', text: '', loaded: false, open: false })))
const columns = ['result', 'id', 'case_title', 'requirement', 'mode', 'required', 'edit_column']
const reviewCount = computed(() => (card.value?.reviewHistory.data.value?.reviews.length ?? 0) + (card.value?.reviewHistory.data.value?.rejections.length ?? 0))
const summary = computed(() => {
  const cases = card.value?.cases.value ?? []
  const counts = { human: cases.filter(c => c.executionMode === 'manual').length,
    ai: cases.filter(c => c.executionMode === 'ai').length,
    ignore: cases.filter(c => !c.required).length,
    noCode: cases.filter(c => c.executionMode === 'automated' && !['case_selectable', 'suite_only'].includes(card.value?.view.value?.caseCapabilities[c.caseId] ?? '')).length }
  return Object.entries(counts).filter(([, n]) => n > 0).map(([key, n]) => t(k(`summary_${key}`), { n })).join(' · ')
})
const modeKey = (mode: string | null) => mode === 'automated' ? 'code' : mode === 'ai' ? 'ai' : mode === 'manual' ? 'human' : 'external'
const reason = (op: TestCardCommand) => { const key = card.value?.can(op).reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' }
const refusalText = computed(() => { const r = card.value?.refusal.value; return r ? [t(`gui.inspector.testSpec.${r.key}`), r.message].filter(Boolean).join(' · ') : '' })
function resultMark(id: string) {
  const c = card.value
  if (!c) return null
  const item = c.cases.value.find(item => item.caseId === id)
  const runnable = item?.executionMode === 'automated' && ['case_selectable', 'suite_only'].includes(c.view.value?.caseCapabilities[id] ?? '')
  if (c.test.runState.value.active && runnable && (c.presentation.value.runScope === 'all' || c.presentation.value.retryCaseId === id)) return TEST_PENDING_MARK
  if (!['done', 'unchecked'].includes(c.presentation.value.resultState.key)) return null
  const status = c.rows.value.find(r => r.caseId === id)?.status
  return status ? testVerdictMark(status) : null
}
async function rewrite() {
  if (!current() || !card.value?.can('rewriteAll').enabled) return
  await card.value.rewriteAll()
}
async function openRaw() {
  raw.value.open = true
  if (!raw.value.loaded || raw.value.docId !== card.value?.view.value?.tsDocId) await loadRaw()
}
let readSequence = 0
async function loadRaw() {
  const id = card.value?.view.value?.tsDocId
  if (!id) return
  const sequence = ++readSequence
  reading.value = true; readError.value = ''
  try {
    const data = await documentContentRead(id)
    if (!current() || sequence !== readSequence) return
    if (data.missing) { readError.value = t(k('read_failed')); return }
    Object.assign(raw.value, { docId: id, baseline: data.content, text: data.content, loaded: true })
    rawConflict.value = false
    if (card.value?.refusal.value?.operation === 'rawSave') card.value.refusal.value = null
  } catch { if (current() && sequence === readSequence) readError.value = t(k('read_failed')) }
  finally { if (current() && sequence === readSequence) reading.value = false }
}
async function saveRaw() {
  const c = card.value, draft = raw.value
  if (!current() || !c?.can('rawSave').enabled || !draft.loaded || reading.value || draft.text === draft.baseline || rawConflict.value) return
  const text = draft.text
  const ok = await c.changeDocument('rawSave', async () => {
    const latest = await documentContentRead(draft.docId)
    if (!current()) throw new Error('inactive')
    if (latest.missing || latest.content !== draft.baseline) {
      rawConflict.value = true
      throw { response: { status: 409, data: { error: 'content_conflict' } } }
    }
    await documentContentSave(draft.docId, text)
  })
  if (ok && current()) { draft.baseline = text; toasts.value = [{ id: Date.now(), message: t(k('saved')), tone: 'success' }] }
}
</script>
<style scoped>
.scenario { color: var(--fg2-text); font-size: var(--fg2-control-font-size); position: relative; }
.window-actions { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; max-width: 65vw; }
small { color: var(--fg2-muted); font-size: 12px; font-weight: normal; }
h3 { display: flex; gap: 14px; align-items: center; font-size: 13px; } h3 small { margin-left: auto; }
.table-scroll { overflow: auto; } table { width: 100%; border-collapse: collapse; font-size: 12px; } th { text-align: left; color: var(--fg2-muted); font-weight: normal; }
th, td { padding: 9px 8px; border-bottom: 1px solid var(--fg2-line); vertical-align: middle; } td:nth-child(3) { min-width: 170px; } td:nth-child(4) { min-width: 130px; }
.warning { color: var(--fg2-yellow); } .mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
/* flowgate.v02.0094: a case id is written in the list's own type (no monospace of its own), muted, on one line. */
.case-id { color: var(--fg2-muted); white-space: nowrap; } .errors { color: var(--fg2-status-danger-fg); }
.confirm, footer { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 10px; } .confirm { border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius); }
.raw { position: absolute; inset: 0; min-height: 480px; background: var(--fg2-panel); border: 1px solid var(--fg2-line-strong); padding: 14px; box-sizing: border-box; z-index: 1; }
.raw-text { width: 100%; } .placeholder { color: var(--fg2-muted); } .history { max-width: 640px; max-height: 400px; overflow: auto; padding: 10px; color: var(--fg2-text); font-size: 12px; } .history article { border-bottom: 1px solid var(--fg2-line); padding: 8px; white-space: pre-wrap; }
</style>
