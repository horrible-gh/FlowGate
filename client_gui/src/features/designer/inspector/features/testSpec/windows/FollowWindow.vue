<template>
  <ModelessShell v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 300)" :initial-y="geometry('y', 110)" :initial-width="geometry('width', 760)" :initial-height="geometry('height', 560)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header><span class="mark">→</span> {{ t(k('title')) }} <small>{{ t(k('to_next')) }}</small></template>
    <div class="follow" data-test="follow-window">
      <section>
        <h3>{{ t(k('scope')) }}
          <span class="scope">
            <InspectorSegment :model-value="scope" :options="scopeOptions" :disabled="!scopeAvailability.enabled" :aria-label="t(k('scope'))" data-test="follow-scope" @update:model-value="change" />
            <InspectorFailureIcon v-if="failure" :name="t(k('scope_failed'))" :reason="failure" data-test="follow-scope-failure" />
          </span>
        </h3>
        <p class="line" data-test="follow-result">{{ resultLine }}<template v-if="card.presentation.value.resultState.key === 'unchecked'"> · {{ t('gui.inspector.testSpec.window.report.unchecked') }}</template></p>
        <InspectorReason v-if="scopeReason" :text="scopeReason" data-test="follow-scope-reason" />
      </section>
      <section v-if="groups.defects.length" data-test="follow-defects">
        <h3><i class="mk defect" />{{ t(k('defects'), { n: groups.defects.length }) }}</h3>
        <article v-for="item in groups.defects" :key="item.caseId" class="item" :data-case="item.caseId">
          <span class="mono id">{{ item.caseId }}</span><b>{{ item.title }}</b>
          <div class="sub"><span class="mute">{{ t(k('requirement'), { value: item.requirement ?? '—' }) }}</span><span>{{ t(k('expected'), { value: item.expected ?? '—' }) }}</span><span>{{ t(k('actual'), { value: item.actual ?? '—' }) }}</span>
            <span v-if="scope === 'full' && item.evidence.length" class="evidence-list" data-test="follow-evidence"><span v-for="(evidence, i) in item.evidence" :key="i" class="evidence">▤ {{ evidence.label ?? evidence.value }}</span></span></div>
        </article>
      </section>
      <section v-if="groups.others.length" data-test="follow-others">
        <h3><i class="mk other" />{{ t(k('others'), { n: groups.others.length }) }}</h3>
        <article v-for="item in groups.others" :key="item.caseId" class="item" :data-case="item.caseId">
          <span class="mono id">{{ item.caseId }}</span><b>{{ item.title }}</b>
          <div class="sub"><span class="mute">{{ [decisionLabel(item), item.decision?.reason].filter(Boolean).join(' · ') }}</span></div>
        </article>
      </section>
      <InspectorReason v-if="!groups.defects.length && !groups.others.length" :text="t(k('no_records'))" data-test="follow-empty" />
      <footer><Button size="sm" variant="secondary" data-test="follow-close" @click="close">{{ t(k('close')) }}</Button></footer>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import { documentHistoryWhen } from '@/features/documentWindow'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorSegment from '../../../parts/InspectorSegment.vue'
import InspectorFailureIcon from '../../../parts/InspectorFailureIcon.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { useScenarioWindow } from './scenarioWindowModel'
import { reportDefects, reportItems, type ReportDecision, type ReportItem } from './reportWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.follow.${key}`
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'follow')
// Q-8: no case decision record on the server yet; Q-9: the next card receives the scope when it runs.
const decisions: readonly ReportDecision[] = []
const groups = computed(() => reportDefects(card.value ? reportItems(card.value.view.value, card.value.cases.value, card.value.rows.value, card.value.presentation.value, decisions) : []))
// The settings tab and this window read and write the session's one settings state.
const scope = computed(() => {
  const settings = card.value?.settings
  const value = settings?.pending.value.follow_scope ?? settings?.node.value?.fields.follow_scope?.value
  return value === 'full' ? 'full' : 'summary'
})
const scopeOptions = computed(() => [{ value: 'summary', label: t(k('summary')) }, { value: 'full', label: t(k('full')) }])
const scopeAvailability = computed(() => card.value?.can('settingChange', undefined, 'follow_scope') ?? { enabled: false, reason: 'reason.busy' })
const scopeReason = computed(() => {
  const key = scopeAvailability.value.reason
  return key && key !== 'reason.busy' ? t(`gui.inspector.testSpec.${key}`) : ''
})
const failure = computed(() => {
  const last = card.value?.settings.failure.value
  return last?.field === 'follow_scope' ? [t(k('scope_failed')), last.message].filter(Boolean).join(' · ') : ''
})
const resultLine = computed(() => {
  const counts = card.value?.test.counts.value, latest = card.value?.view.value?.latest
  return t(k('result_line'), { at: documentHistoryWhen(latest?.finishedAt ?? latest?.startedAt ?? null), pass: counts?.pass ?? 0, fail: counts?.fail ?? 0, blocked: counts?.blocked ?? 0, notRun: counts?.notRun ?? 0 })
})
async function change(value: string) {
  if (!current() || !scopeAvailability.value.enabled || value === scope.value) return
  await card.value?.changeSetting('follow_scope', value)
}
function decisionLabel(item: ReportItem) {
  const cause = item.decision?.cause
  return cause ? t(`gui.inspector.testSpec.window.report.${cause === 'manual' || cause === 'manualF' ? `decided_${cause}` : `cause_${cause}`}`) : ''
}
</script>
<style scoped>
.follow { display: flex; flex-direction: column; gap: 12px; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.mark { color: var(--fg2-status-info-fg); margin-right: 6px; } small { color: var(--fg2-muted); font-weight: normal; }
section { display: flex; flex-direction: column; gap: 8px; padding: 10px 12px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); }
h3 { display: flex; align-items: center; gap: 8px; margin: 0; font-size: 13px; } .scope { display: inline-flex; align-items: center; gap: 6px; margin-left: auto; }
.line { margin: 0; color: var(--fg2-muted); font-size: 12px; }
.mk { width: 8px; height: 8px; border-radius: 2px; } .mk.defect { background: var(--fg2-status-danger-fg); } .mk.other { background: var(--fg2-status-success-fg); }
.item { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; padding: 8px 0; border-top: 1px solid var(--fg2-line); }
.id { color: var(--fg2-muted); font-family: monospace; font-size: 11px; }
.sub { display: flex; flex-direction: column; gap: 3px; width: 100%; font-size: 12px; } .mute { color: var(--fg2-muted); }
.evidence-list { display: flex; flex-wrap: wrap; gap: 5px; } .evidence { padding: 1px 7px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); font-size: 11px; }
footer { display: flex; justify-content: flex-end; }
</style>
