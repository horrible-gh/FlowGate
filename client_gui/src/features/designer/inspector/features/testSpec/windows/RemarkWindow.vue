<template>
  <!-- F81: the modeless remark window. One at a time: a case's remark button shows that case's remarks here,
       and editing the case window or picking another case in the list never closes it. -->
  <ModelessShell kind-color="#f0a23a" v-if="card && state && state.caseId" ref="shell" :open="state.open" :minimized="state.minimized"
    :title="`${t(k('title'))} · ${state.caseId}`" :initial-x="geometry('x', 960)" :initial-y="geometry('y', 130)" :initial-width="geometry('width', 420)" :initial-height="geometry('height', 560)"
    :close-on-escape="false" @update:minimized="state.minimized = $event" @close="close">
    <template #header><span class="heading" data-test="remark-heading"><span class="heading-kind">{{ t(k('title')) }}</span><span class="heading-case" :title="caseLabel" data-test="remark-window-case">{{ caseLabel }}</span></span></template>
    <div class="remarks" data-test="remark-window" :data-case="state.caseId">
      <InspectorPaneState v-if="card.remarks.status.value === 'loading' && !card.remarks.data.value" state="loading" />
      <InspectorPaneState v-else-if="card.remarks.status.value === 'error'" state="error" retryable @retry="card.remarks.reload()" />
      <div class="list">
        <p v-if="!open.length && !resolved.length" class="empty" data-test="remark-empty">{{ t(k('empty')) }}</p>
        <article v-for="remark in open" :key="remark.remarkId" class="remark" data-test="remark-open" :data-remark="remark.remarkId">
          <header><b>{{ authorOf(remark) }}</b><span class="phase" data-test="remark-phase">{{ t(k(`phase_${remark.phase}`)) }}</span><time>{{ documentHistoryWhen(remark.createdAt) }}</time></header>
          <p>{{ remark.text }}</p>
          <p v-if="remark.aiReply" class="reply" data-test="remark-reply">↳ {{ remark.aiReply }}</p>
        </article>
        <details v-if="resolved.length" class="resolved" data-test="remark-resolved">
          <summary>{{ t(k('resolved'), { n: resolved.length }) }}</summary>
          <article v-for="remark in resolved" :key="remark.remarkId" class="remark remark--resolved" :data-remark="remark.remarkId">
            <header><b>{{ authorOf(remark) }}</b><span class="phase">{{ t(k(`phase_${remark.phase}`)) }}</span><span class="done">{{ t(k('state_resolved')) }}</span>
              <Button size="sm" variant="secondary" :disabled="!writable" :title="reason" data-test="remark-reopen" @click="card.setRemarkState(remark.remarkId, 'open')">{{ t(k('reopen')) }}</Button></header>
            <p>{{ remark.text }}</p>
            <p v-if="remark.aiReply" class="reply">↳ {{ remark.aiReply }}</p>
          </article>
        </details>
      </div>
      <InspectorReason v-if="card.remarkFailed.value" tone="danger" :text="t(k('failed'))" data-test="remark-failed" />
      <form class="entry" @submit.prevent="submit">
        <Textarea v-model="draft.text" :rows="3" :placeholder="t(k('placeholder'))" :disabled="!writable" data-test="remark-text" @keydown.ctrl.enter.prevent="submit" @keydown.meta.enter.prevent="submit" />
        <Button size="sm" variant="primary" type="submit" :disabled="!writable || !draft.text.trim()" :title="reason" data-test="remark-submit">{{ t(k('submit')) }}</Button>
      </form>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { testSpecCaseKey, type TestRemark } from '@/features/document'
import { documentHistoryWhen } from '@/features/documentWindow'
import InspectorReason from '../../../parts/InspectorReason.vue'
import InspectorPaneState from '../../../parts/InspectorPaneState.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { sessionDraft, useScenarioWindow } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.remark.${key}`
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'remark')
const own = computed(() => {
  const id = state.value?.caseId
  return id ? (card.value?.remarks.data.value?.remarks ?? []).filter(remark => testSpecCaseKey(remark.caseId) === testSpecCaseKey(id)) : []
})
// Open remarks first (oldest on top, as written); the resolved ones are folded under them.
const open = computed(() => own.value.filter(remark => remark.state === 'open'))
const resolved = computed(() => own.value.filter(remark => remark.state === 'resolved'))
const caseLabel = computed(() => [state.value?.caseId, card.value?.cases.value.find(item => item.caseId === state.value?.caseId)?.title?.trim()].filter(Boolean).join(' · '))
/** The text being written is kept per case, so switching the window to another case and back loses nothing. */
const draft = computed(() => sessionDraft<{ text: string }>(card.value!.drafts, `remark:${state.value?.caseId ?? ''}`, () => ({ text: '' })))
const writable = computed(() => !!card.value?.can('remark').enabled)
const reason = computed(() => { const key = card.value?.can('remark').reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' })
const authorOf = (remark: TestRemark) => remark.author.name || t(k(`author_${remark.author.kind}`))
async function submit() {
  const c = card.value, id = state.value?.caseId, d = draft.value
  if (!current() || !c || !id || !d.text.trim() || !writable.value) return
  if (await c.addRemark(id, d.text) && current()) d.text = ''
}
</script>
<style scoped>
.remarks { display: flex; flex-direction: column; gap: 10px; min-height: 100%; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.list { display: flex; flex: 1; flex-direction: column; gap: 8px; min-height: 0; overflow: auto; }
.remark { padding: 8px 10px; border: 1px solid var(--fg2-line); border-radius: 8px; }
.remark header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; font-size: 12px; }
.remark p { margin: 6px 0 0; white-space: pre-wrap; } .remark time { margin-left: auto; color: var(--fg2-muted); font-size: 11px; }
.phase { padding: 0 6px; border: 1px solid var(--fg2-line); border-radius: 999px; color: var(--fg2-muted); font-size: 10.5px; }
.reply { color: var(--fg2-muted); } .done { margin-left: auto; color: var(--fg2-muted); font-size: 11px; }
.remark--resolved { opacity: 0.8; } .resolved { display: flex; flex-direction: column; gap: 8px; } .resolved summary { color: var(--fg2-muted); font-size: 12px; cursor: pointer; }
.resolved article { margin-top: 8px; } .empty { color: var(--fg2-muted); }
.entry { display: flex; flex: none; flex-direction: column; align-items: flex-end; gap: 8px; margin-top: auto; padding-top: 10px; border-top: 1px solid var(--fg2-line); }
.entry :deep(textarea) { width: 100%; }
.heading { display: inline-flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%; } .heading-kind { flex: 0 0 auto; }
.heading-case { overflow: hidden; color: var(--fg2-muted); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
</style>
