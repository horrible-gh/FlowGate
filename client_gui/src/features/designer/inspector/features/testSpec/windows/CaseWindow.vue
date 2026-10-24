<template>
  <ModelessShell v-if="card && state && draft" ref="shell" :open="state.open" :minimized="state.minimized"
    :title="`${draft.original.case_id} · ${draft.original.title}`" :initial-x="geometry('x', 220)" :initial-y="geometry('y', 130)" :initial-width="geometry('width', 720)" :initial-height="geometry('height', 660)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header><span class="mark">TC</span> {{ draft.original.case_id }} · {{ draft.original.title }} <small>—</small></template>
    <div class="case" data-test="case-window">
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <template v-if="card.writing.value === 'rewrite-case' && card.writingCaseId.value === state.caseId"><p class="placeholder" data-test="case-writing">{{ t(k('writing')) }}</p></template>
      <template v-else>
        <label><span>{{ t(k('title')) }}</span><Input v-model="draft.title" :disabled="locked" data-test="case-title" /></label>
        <label><span>{{ t(k('requirement')) }}</span><Select v-model="requirementChoice" :options="requirementOptions" :disabled="locked" data-test="case-requirement-select" /></label>
        <label v-if="requirementChoice === '__custom'"><span>{{ t(k('custom')) }}</span><Input v-model="draft.requirement" :disabled="locked" data-test="case-requirement" /></label>
        <label v-for="field in textFields" :key="field"><span>{{ t(k(field)) }}</span><Textarea v-model="draft[field]" :rows="field === 'precondition' ? 2 : 4" :disabled="locked" :data-test="`case-${field}`" /></label>
        <label><span>{{ t(k('mode')) }}</span><InspectorSegment :model-value="draft.execution_mode" :options="modeOptions" :disabled="locked" :aria-label="t(k('mode'))" data-test="case-mode" @update:model-value="draft.execution_mode = $event" /></label>
        <label v-if="draft.execution_mode === 'automated'"><span>{{ t(k('code')) }}</span><Textarea v-model="draft.automation_ref" class="mono" :rows="2" :disabled="locked" data-test="case-code" /></label>
        <label><span>{{ t(k('required')) }}</span><InspectorSegment :model-value="draft.required ? 'stop' : 'ignore'" :options="requiredOptions" :disabled="locked" :aria-label="t(k('required'))" data-test="case-required" @update:model-value="draft.required = $event === 'stop'" /></label>
      </template>
      <template v-if="conflict"><InspectorReason tone="danger" :text="t(k('conflict'))" data-test="case-conflict" /><Button size="sm" variant="secondary" data-test="case-reload" @click="resetDraft">{{ t(k('reload')) }}</Button></template>
      <InspectorReason v-if="saveReason" :text="saveReason" />
      <template v-if="refusal"><InspectorReason tone="danger" :text="refusalText" data-test="case-refusal" /><ul class="errors"><li v-for="(error, i) in refusal.fieldErrors" :key="i">{{ error.caseId }} · {{ error.field }} · {{ error.message }}</li></ul></template>
      <div v-if="confirming" class="confirm" role="group" data-test="case-delete-confirm"><span>{{ t(k('delete_confirm'), { id: draft.original.case_id }) }}</span>
        <Button size="sm" variant="secondary" @click="confirming = false">{{ t(k('cancel')) }}</Button>
        <Button size="sm" variant="danger" :disabled="conflict || !card.can('caseDelete').enabled" :title="deleteReason" data-test="case-delete-submit" @click="remove">{{ t(k('delete')) }}</Button></div>
      <footer><Button size="sm" variant="danger" :disabled="!card.can('caseDelete').enabled || conflict" :title="deleteReason" data-test="case-delete" @click="confirming = true">{{ t(k('delete')) }}</Button>
        <Button size="sm" variant="secondary" :disabled="!card.can('rewriteCase').enabled" :title="rewriteReason" data-test="case-rewrite">{{ t(k('rewrite')) }}</Button><span class="grow" />
        <Button size="sm" variant="secondary" @click="close">{{ t(k('close')) }}</Button>
        <Button size="sm" :disabled="!valid || locked || conflict || checking" :title="saveReason" data-test="case-save" @click="save">{{ t(k('save')) }}</Button></footer>
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
import Select from '@/components/ui/Select.vue'
import { testSpecCasePayload } from '@/features/document'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import InspectorSegment from '../../../parts/InspectorSegment.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { caseDraft, mergeCase, scenarioSnapshot, sessionDraft, useScenarioWindow, notifySaved, type CaseDraft } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.case.${key}`
const { card, shell, state, close, current, geometry } = useScenarioWindow(() => props.target, 'case')
const confirming = ref(false), checking = ref(false)
const draft = computed(() => {
  const c = card.value, item = c?.cases.value.find(item => item.caseId === state.value?.caseId)
  return c && item ? sessionDraft<CaseDraft>(c.drafts, `t2:case:${item.caseId}`, () => caseDraft(item, c.cases.value, c.view.value?.details?.title ?? null, c.view.value?.tsDocId ?? null)) : null
})
// Loading, failed and partial reads cannot prove that the target disappeared.
watch(() => [card.value?.test.status.value, card.value?.view.value, card.value?.writing.value,
  card.value?.busy.value, state.value?.open, state.value?.caseId] as const, () => {
  const c = card.value, id = state.value?.caseId
  if (!c || !state.value?.open || !id || c.test.status.value !== 'ready' || c.writing.value
    || c.test.view.value?.details?.complete !== true || c.formatErrors.value.length
    || c.busy.value === 'caseDelete' || c.cases.value.some(item => item.caseId === id)) return
  close()
  notifySaved(t(k('missing'), { id }), 'info')
}, { immediate: true, flush: 'sync' })
const textFields = ['precondition', 'procedure', 'expected'] as const
const conflict = computed(() => !!draft.value && !!card.value && (draft.value.docId !== card.value.view.value?.tsDocId || draft.value.snapshot !== scenarioSnapshot(card.value.cases.value, card.value.view.value?.details?.title ?? null)))
const valid = computed(() => !!draft.value?.title.trim() && !!draft.value.requirement.trim())
const locked = computed(() => !card.value?.can('caseSave').enabled)
const saveReason = computed(() => conflict.value ? t(k('conflict')) : !valid.value ? t(k('required_fields')) : reason('caseSave'))
const deleteReason = computed(() => conflict.value ? t(k('conflict')) : reason('caseDelete'))
const rewriteReason = computed(() => reason('rewriteCase'))
function reason(op: 'caseSave' | 'caseDelete' | 'rewriteCase') { const key = card.value?.can(op).reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' }
const refusal = computed(() => ['caseSave', 'caseDelete'].includes(card.value?.refusal.value?.operation ?? '') ? card.value?.refusal.value : null)
const refusalText = computed(() => refusal.value ? [t(`gui.inspector.testSpec.${refusal.value.key}`), refusal.value.message].filter(Boolean).join(' · ') : '')
const custom = ref(false)
watch(() => state.value?.caseId, () => { confirming.value = false; custom.value = false })
const requirements = computed(() => [...new Set(card.value?.cases.value.map(c => c.fields.requirement).filter((s): s is string => !!s))])
const requirementOptions = computed(() => [...requirements.value.map(value => ({ value, label: value })), { value: '__custom', label: t(k('custom')) }])
const requirementChoice = computed({ get: () => custom.value || !requirements.value.includes(draft.value?.requirement ?? '') ? '__custom' : draft.value!.requirement,
  set: (value: string) => { custom.value = value === '__custom'; if (!custom.value && draft.value) draft.value.requirement = value } })
const modeOptions = computed(() => [{ value: 'automated', label: t(k('automated')) }, { value: 'ai', label: t(k('ai')), disabled: true, title: t('gui.inspector.testSpec.guard.unconnected') }, { value: 'manual', label: t(k('manual')) },
  ...(!['automated', 'manual', ''].includes(draft.value?.execution_mode ?? '') ? [{ value: draft.value!.execution_mode, label: t(k('external')), disabled: true }] : [])])
const requiredOptions = computed(() => [{ value: 'ignore', label: t(k('ignore')) }, { value: 'stop', label: t(k('stop')) }])
async function commit(deleting: boolean) {
  const c = card.value, d = draft.value, op = deleting ? 'caseDelete' : 'caseSave'
  if (!current() || !c || !d || checking.value || conflict.value || (!deleting && !valid.value) || !c.can(op).enabled) return
  checking.value = true
  try {
    await c.reload()
    if (!current() || draft.value !== d || conflict.value || c.test.status.value !== 'ready' || !c.can(op).enabled) return
    const payload = deleting ? c.cases.value.filter(item => item.caseId !== d.original.case_id).map(testSpecCasePayload) : mergeCase(c.cases.value, d)
    const ok = await c.saveCases(payload, op)
    if (!ok || !current() || state.value?.caseId !== d.original.case_id) return
    delete c.drafts[`t2:case:${d.original.case_id}`]
    close()
    notifySaved(t(k(deleting ? 'deleted' : 'saved'), { id: d.original.case_id }))
  } finally { if (current()) checking.value = false }
}
const save = () => commit(false)
const remove = () => { if (confirming.value) return commit(true) }
function resetDraft() { if (card.value && draft.value) { delete card.value.drafts[`t2:case:${draft.value.original.case_id}`]; confirming.value = false } }
</script>
<style scoped>
.case { display: flex; flex-direction: column; gap: 12px; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
label { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 12px; align-items: start; } label > span { color: var(--fg2-muted); padding-top: 7px; }
.mark { color: var(--fg2-status-info-fg); margin-right: 8px; } small { color: var(--fg2-muted); } .mono { font-family: monospace; }
footer, .confirm { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-top: 8px; } .grow { flex: 1; }
.confirm { border: 1px solid var(--fg2-status-danger-border); padding: 10px; border-radius: var(--fg2-control-radius); } .errors { color: var(--fg2-status-danger-fg); } .placeholder { color: var(--fg2-muted); }
@media (max-width: 520px) { label { grid-template-columns: 1fr; gap: 4px; } }
</style>
