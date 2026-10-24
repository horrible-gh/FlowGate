<template>
  <ModelessShell v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 180)" :initial-y="geometry('y', 150)" :initial-width="geometry('width', 680)" :initial-height="geometry('height', 440)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header><span class="mark">{{ t(k('mark')) }}</span> {{ t(k('title')) }} <small>{{ t(k('to_ai')) }} · {{ provider }}</small></template>
    <div class="brief" data-test="brief-window">
      <label for="test-brief-text">{{ t(k('additional')) }}</label>
      <Textarea id="test-brief-text" v-model="draft.text" :rows="10" :maxlength="20000" :disabled="!!card.writing.value" :placeholder="t(k('placeholder'))" data-test="brief-text" />
      <small class="counter" data-test="brief-count">{{ t(k('count'), { n: draft.text.length }) }}</small>
      <InspectorReason :text="reason" />
      <footer><Button size="sm" variant="secondary" @click="close">{{ t(k('close')) }}</Button>
        <Button size="sm" variant="secondary" disabled :title="reason" data-test="brief-save">{{ t(k('save')) }}</Button>
        <Button size="sm" disabled :title="reason" data-test="brief-rewrite">{{ t(k('save_rewrite')) }}</Button></footer>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import Textarea from '@/components/ui/Textarea.vue'
import InspectorReason from '../../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { sessionDraft, useScenarioWindow } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.brief.${key}`
const { card, shell, state, close, geometry } = useScenarioWindow(() => props.target, 'brief')
const draft = computed(() => sessionDraft(card.value!.drafts, 't2:brief', () => ({ text: '' })))
const provider = computed(() => card.value?.settings.choices.value?.providers.find((p) => p.id === card.value?.settings.node.value?.fields.run_ai?.value)?.name ?? '—')
const reason = computed(() => t(`gui.inspector.testSpec.${card.value?.can('briefSave').reason ?? 'guard.unconnected'}`))
</script>
<style scoped>
.brief { display: flex; flex-direction: column; gap: 10px; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.mark { color: var(--fg2-status-info-fg); margin-right: 8px; } small { color: var(--fg2-muted); } .counter { text-align: right; }
footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
</style>
