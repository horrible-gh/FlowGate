<template>
  <ModelessShell v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 180)" :initial-y="geometry('y', 150)" :initial-width="geometry('width', 680)" :initial-height="geometry('height', 440)"
    @update:minimized="state.minimized = $event" @close="close">
    <template #header><span class="mark">{{ t(k('mark')) }}</span> {{ t(k('title')) }} <small>{{ t(k('to_ai')) }} · {{ provider }}</small></template>
    <div class="brief" data-test="brief-window">
      <label for="test-brief-text">{{ t(k('additional')) }}</label>
      <Textarea id="test-brief-text" v-model="draft.text" :rows="10" :maxlength="20000" :disabled="!draft.loaded || !card.can('briefSave').enabled" :placeholder="t(k('placeholder'))" data-test="brief-text" />
      <small class="counter" data-test="brief-count">{{ t(k('count'), { n: draft.text.length }) }}</small>
      <InspectorReason :text="error || reason" /><Button v-if="error" size="sm" variant="secondary" @click="load">{{ t('gui.inspector.testSpec.window.scenario.reload') }}</Button>
      <footer><Button size="sm" variant="secondary" @click="close">{{ t(k('close')) }}</Button>
        <Button size="sm" variant="secondary" :disabled="!draft.loaded || !card.can('briefSave').enabled" :title="reason" data-test="brief-save" @click="save(false)">{{ t(k('save')) }}</Button>
        <Button size="sm" :disabled="!draft.loaded || !card.can('briefSave').enabled || !card.presentation.value.hasScenario || !card.can('rewriteAll').enabled" :title="reason" data-test="brief-rewrite" @click="save(true)">{{ t(k('save_rewrite')) }}</Button></footer>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { testBriefRead, testBriefSave } from '@/features/document'
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
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'brief')
const draft = computed(() => sessionDraft(card.value!.drafts, 't2:brief', () => ({ text: '', content: '', loaded: false, docId: '' })))
const provider = computed(() => card.value?.settings.choices.value?.providers.find((p) => p.id === card.value?.settings.node.value?.fields.run_ai?.value)?.name ?? '—')
const reason = computed(() => { const key = card.value?.can('briefSave').reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' })
const error = ref('')
async function load() {
  const id = card.value?.view.value?.tsDocId
  if (!id) return
  error.value = ''
  try {
    const data = await testBriefRead(id)
    if (current()) Object.assign(draft.value, data, { loaded: true, docId: id })
  } catch { if (current()) error.value = t('gui.inspector.testSpec.window.scenario.read_failed') }
}
onMounted(() => { if (!draft.value.loaded || draft.value.docId !== card.value?.view.value?.tsDocId) void load() })
async function save(rewrite: boolean) {
  const c = card.value, d = draft.value
  if (!c || !d.loaded || !current()) return
  const ok = await c.changeDocument('briefSave', async () => {
    const saved = await testBriefSave(d.docId, d.text, d.content)
    Object.assign(d, saved)
  })
  if (!current()) return
  if (!ok) { error.value = c.refusal.value?.message || t('gui.inspector.testSpec.window.scenario.conflict'); return }
  close()
  if (rewrite) { c.openWindow('scenario'); await c.rewriteAll() }
}
</script>
<style scoped>
.brief { display: flex; flex-direction: column; gap: 10px; color: var(--fg2-text); font-size: var(--fg2-control-font-size); }
.mark { color: var(--fg2-status-info-fg); margin-right: 8px; } small { color: var(--fg2-muted); } .counter { text-align: right; }
footer { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
</style>
