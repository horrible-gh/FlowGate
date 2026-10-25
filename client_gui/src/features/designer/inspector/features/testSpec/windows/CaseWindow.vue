<template>
  <ModelessShell v-if="card && state && draft" ref="shell" :open="state.open" :minimized="state.minimized"
    :title="`${t(k('name'))} · ${caseLabel}`" :initial-x="geometry('x', 220)" :initial-y="geometry('y', 130)" :initial-width="geometry('width', 720)" :initial-height="geometry('height', 660)"
    @update:minimized="state.minimized = $event" @close="close">
    <!-- flowgate.v02.0094: the kind, then — muted, as the write window names its card — which case: its id and title once. -->
    <template #header><span class="heading" data-test="case-heading"><span class="heading-kind">{{ t(k('name')) }}</span><span class="heading-case" :title="caseLabel" data-test="case-window-id">{{ caseLabel }}</span></span></template>
    <div class="case" data-test="case-window">
      <InspectorPaneState v-if="card.test.status.value === 'error'" state="error" retryable @retry="card.reload()" />
      <template v-if="card.writing.value === 'rewrite-case' && card.writingCaseId.value === state.caseId"><p class="placeholder" data-test="case-writing">{{ t(k('writing')) }}</p></template>
      <template v-else>
        <label><span>{{ t(k('title')) }}</span><Input v-model="draft.title" :disabled="locked" data-test="case-title" /></label>
        <label><span>{{ t(k('requirement')) }}</span><Select v-model="requirementChoice" :options="requirementOptions" :disabled="locked" data-test="case-requirement-select" /></label>
        <label v-if="requirementChoice === '__custom'"><span>{{ t(k('custom')) }}</span><Input v-model="draft.requirement" :disabled="locked" data-test="case-requirement" /></label>
        <label v-for="field in textFields" :key="field"><span>{{ t(k(field)) }}</span><Textarea v-model="draft[field]" :rows="field === 'precondition' ? 2 : 4" :disabled="locked" :data-test="`case-${field}`" /></label>
        <div class="field" role="group" :aria-label="t(k('mode'))"><span>{{ t(k('mode')) }}</span><div class="control"><InspectorSegment :model-value="draft.execution_mode" :options="modeOptions" :disabled="locked" :aria-label="t(k('mode'))" data-test="case-mode" @update:model-value="draft.execution_mode = $event" /></div></div>
        <!-- flowgate.v02.0094: no language or framework is assumed; the case names its test file and the command that runs it. -->
        <template v-if="draft.execution_mode === 'automated'">
          <label><span>{{ t(k('code')) }}</span><Input v-model="draft.automation_ref" :placeholder="t(k('code_placeholder'))" class="mono" :disabled="locked" data-test="case-code" /></label>
          <label><span>{{ t(k('command')) }}</span><Input v-model="draft.run_command" :placeholder="t(k('command_placeholder'))" class="mono" :disabled="locked" data-test="case-command" /></label>
          <label><span>{{ t(k('cwd')) }}</span><Input v-model="draft.run_cwd" placeholder="." class="mono" :disabled="locked" data-test="case-cwd" /></label>
        </template>
        <div class="field" role="group" :aria-label="t(k('required'))"><span>{{ t(k('required')) }}</span><div class="control"><InspectorSegment :model-value="draft.required ? 'stop' : 'ignore'" :options="requiredOptions" :disabled="locked" :aria-label="t(k('required'))" data-test="case-required" @update:model-value="draft.required = $event === 'stop'" /></div></div>
      </template>
      <template v-if="conflict"><InspectorReason tone="danger" :text="t(k('conflict'))" data-test="case-conflict" /><Button size="sm" variant="secondary" data-test="case-reload" @click="resetDraft">{{ t(k('reload')) }}</Button></template>
      <InspectorReason v-if="!conflict && saveReason" :text="saveReason" />
      <template v-if="refusal && !conflict"><InspectorReason tone="danger" :text="refusalText" data-test="case-refusal" /><ul class="errors"><li v-for="(error, i) in refusal.fieldErrors" :key="i">{{ error.caseId }} · {{ error.field }} · {{ error.message }}</li></ul></template>
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
import Select from '@/components/ui/Select.vue'
import { testSpecCasePayload } from '@/features/document'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import InspectorSegment from '../../../parts/InspectorSegment.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { caseDraft, newCaseDraft, addedCase, mergeCase, scenarioSnapshot, sessionDraft, useScenarioWindow, notifySaved, type CaseDraft } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
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
const textFields = ['precondition', 'procedure', 'expected'] as const
const conflict = computed(() => !!draft.value && !!card.value && card.value.test.status.value === 'ready' && card.value.view.value?.details?.complete === true && (draft.value.docId !== card.value.view.value?.tsDocId || draft.value.snapshot !== scenarioSnapshot(card.value.cases.value, card.value.view.value?.details?.title ?? null)))
// The server's required case fields this window shows; category and check points are hidden and defaulted.
const valid = computed(() => !!draft.value && (['title', 'requirement', 'procedure', 'expected'] as const).every(field => !!draft.value![field].trim()))
/** Preserve each case’s selected failure policy when saving or deleting. */
function payloadFor(deleting: boolean) {
  const c = card.value, d = draft.value
  if (!c || !d) return null
  return deleting ? c.cases.value.filter(item => item.caseId !== d.original.case_id).map(testSpecCasePayload) : adding.value ? addedCase(c.cases.value, d) : mergeCase(c.cases.value, d)
}
const locked = computed(() => !card.value?.can(adding.value ? 'caseAdd' : 'caseSave').enabled)
const saveReason = computed(() => conflict.value ? t(k('conflict')) : !valid.value ? t(k('required_fields')) : reason(adding.value ? 'caseAdd' : 'caseSave'))
const deleteReason = computed(() => conflict.value ? t(k('conflict')) : reason('caseDelete'))
function reason(op: 'caseSave' | 'caseAdd' | 'caseDelete' | 'rewriteCase') { const key = card.value?.can(op).reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' }
const refusal = computed(() => ['caseSave', 'caseAdd', 'caseDelete'].includes(card.value?.refusal.value?.operation ?? '') ? card.value?.refusal.value : null)
const refusalText = computed(() => refusal.value ? [t(`gui.inspector.testSpec.${refusal.value.key}`), refusal.value.message].filter(Boolean).join(' · ') : '')
const custom = ref(false)
watch(() => state.value?.caseId, () => { confirming.value = false; custom.value = false })
const requirements = computed(() => [...new Set(card.value?.cases.value.map(c => c.fields.requirement).filter((s): s is string => !!s))])
const requirementOptions = computed(() => [...requirements.value.map(value => ({ value, label: value })), { value: '__custom', label: t(k('custom')) }])
const requirementChoice = computed({ get: () => custom.value || !requirements.value.includes(draft.value?.requirement ?? '') ? '__custom' : draft.value!.requirement,
  set: (value: string) => { custom.value = value === '__custom'; if (!custom.value && draft.value) draft.value.requirement = value } })
// The v9 choices; a mode outside them (an existing external case) stays shown and kept until another is picked.
const modeOptions = computed(() => [{ value: 'automated', label: t(k('automated')) }, { value: 'ai', label: t(k('ai')) }, { value: 'manual', label: t(k('manual')) },
  ...(!['automated', 'ai', 'manual', ''].includes(draft.value?.execution_mode ?? '') ? [{ value: draft.value!.execution_mode, label: t(k('external')), disabled: true }] : [])])
const requiredOptions = computed(() => [{ value: 'ignore', label: t(k('ignore')) }, { value: 'stop', label: t(k('stop')) }])
async function commit(deleting: boolean) {
  const c = card.value, d = draft.value, op = deleting ? 'caseDelete' : adding.value ? 'caseAdd' : 'caseSave'
  if (!current() || !c || !d || checking.value || conflict.value || (!deleting && !valid.value) || !c.can(op).enabled) return
  checking.value = true
  try {
    await c.reload()
    if (!current() || draft.value !== d || conflict.value || c.test.status.value !== 'ready' || !c.can(op).enabled) return
    const payload = payloadFor(deleting)
    if (!payload) return
    const ok = await c.saveCases(payload, op)
    if (!ok || !current() || state.value?.caseId !== (adding.value ? '__new__' : d.original.case_id)) return
    delete c.drafts[`t2:case:${adding.value ? '__new__' : d.original.case_id}`]
    close()
    notifySaved(t(k(deleting ? 'deleted' : 'saved'), { id: d.original.case_id }))
  } finally { if (current()) checking.value = false }
}
const save = () => commit(false)
const remove = () => { if (confirming.value) return commit(true) }
function resetDraft() { if (card.value && draft.value) { delete card.value.drafts[`t2:case:${adding.value ? '__new__' : draft.value.original.case_id}`]; confirming.value = false } }
</script>
<style scoped>
/* The card memo window's form: the fields, then the bar at the bottom. */
.case { display: flex; flex-direction: column; gap: 12px; min-height: 100%; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
label, .field { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 12px; align-items: start; } label > span, .field > span { color: var(--fg2-muted); padding-top: 7px; }
.control { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; } .hint { color: var(--fg2-muted); font-size: 11.5px; }
.mono { font-family: 'JetBrains Mono', ui-monospace, monospace; }
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
