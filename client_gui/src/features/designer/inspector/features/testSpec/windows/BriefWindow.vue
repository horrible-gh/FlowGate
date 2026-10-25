<template>
  <!-- flowgate.v02.0094 — the 지시서 editor on the requirement document editor's layout (DocumentWindow in its editor): the
       title, the text filling the window, and the editor bar at the bottom — a failed save on its left, [저장] ·
       [닫기] at its right end. Why it cannot be saved is the muted line above the text; a failed read replaces the text
       with its retry. It only saves: the scenario's AI writing is the 시나리오 line's [AI 호출]. -->
  <ModelessShell v-if="card && state" ref="shell" :open="state.open" :minimized="state.minimized" :title="t(k('title'))"
    :initial-x="geometry('x', 180)" :initial-y="geometry('y', 150)" :initial-width="geometry('width', 680)" :initial-height="geometry('height', 560)"
    :min-width="360" :min-height="240"
    @update:minimized="state.minimized = $event" @close="close">
    <div class="brief" data-test="brief-window">
      <p v-if="reason" class="brief-note" data-test="brief-reason">{{ reason }}</p>
      <div v-if="loadError" class="brief-note brief-note--danger" data-test="brief-load-error">
        <span>{{ t('gui.inspector.testSpec.window.scenario.read_failed') }}</span>
        <IconButton :label="t('gui.document_window.viewer.retry')" :title="t('gui.document_window.viewer.retry')" variant="secondary" size="sm" data-test="brief-reload" @click="load">↻</IconButton>
      </div>
      <textarea v-else v-model="draft.text" class="brief-textarea" spellcheck="false" :aria-label="t(k('additional'))" :maxlength="20000"
        :disabled="!savable" :placeholder="t(k('placeholder'))" data-test="brief-text"
        @keydown.ctrl.s.prevent="save" @keydown.meta.s.prevent="save" />
      <div class="brief-bar">
        <span v-if="error" class="brief-error" :title="error" data-test="brief-error">{{ error }}</span>
        <Button size="sm" variant="primary" :disabled="!savable" :loading="saving" :title="reason || undefined" data-test="brief-save" @click="save">{{ t(k('save')) }}</Button>
        <Button size="sm" variant="secondary" :disabled="saving" data-test="brief-close" @click="close">{{ t(k('close')) }}</Button>
      </div>
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { testBriefRead, testBriefSave } from '@/features/document'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import type { InspectorTarget } from '../../../inspectorTarget'
import { sessionDraft, useScenarioWindow } from './scenarioWindowModel'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.testSpec.window.brief.${key}`
const { card, shell, state, close, geometry, current } = useScenarioWindow(() => props.target, 'brief')
// Last read or saved text; closing retains the session draft.
const draft = computed(() => sessionDraft(card.value!.drafts, 't2:brief', () => ({ text: '', content: '', base: '', loaded: false, docId: '' })))
const reason = computed(() => { const key = card.value?.can('briefSave').reason; return key ? t(`gui.inspector.testSpec.${key}`) : '' })
const saving = computed(() => card.value?.busy.value === 'briefSave')
const savable = computed(() => draft.value.loaded && !saving.value && !!card.value?.can('briefSave').enabled)
const loadError = ref(false), error = ref('')
async function load() {
  const id = card.value?.view.value?.tsDocId
  if (!id) return
  loadError.value = false; error.value = ''
  try {
    const data = await testBriefRead(id)
    if (current()) Object.assign(draft.value, data, { base: data.text, loaded: true, docId: id })
  } catch { if (current()) loadError.value = true }
}
onMounted(() => { if (!draft.value.loaded || draft.value.docId !== card.value?.view.value?.tsDocId) void load() })
async function save() {
  const c = card.value, d = draft.value
  if (!c || !savable.value || !current()) return
  error.value = ''
  const ok = await c.changeDocument('briefSave', async () => {
    const saved = await testBriefSave(d.docId, d.text, d.content)
    Object.assign(d, saved, { base: saved.text })
  })
  if (!current()) return
  if (!ok) { error.value = c.refusal.value?.message || t('gui.inspector.testSpec.window.scenario.conflict'); return }
  close()
}
</script>
<style scoped>
/* The shell body pads and scrolls; the window fills it so the text owns its scroll (DocumentWindow's editor: the height
   adds back the body's padding the margins cancel, so the bar ends at the shell's bottom edge). */
.brief { display: flex; flex-direction: column; height: calc(100% + 24px); min-height: 0; margin: -12px -14px; color: var(--fg2-text); }
.brief-note { display: flex; align-items: center; gap: 8px; margin: 0; padding: 10px 18px; border-bottom: 1px solid var(--fg2-line); color: var(--fg2-muted); font-size: 12.5px; }
.brief-note--danger { flex: 1; align-items: flex-start; border-bottom: none; color: var(--fg2-status-danger-fg); }
.brief-textarea { flex: 1; min-height: 0; margin: 0; padding: 14px 18px; border: none; outline: none; resize: none;
  background: var(--fg2-bg); color: var(--fg2-text); font-family: 'JetBrains Mono', ui-monospace, monospace; font-size: 12.5px; line-height: 1.6; tab-size: 2; }
.brief-textarea:focus-visible { box-shadow: inset 0 0 0 1px var(--fg2-focus); }
/* DocumentWindow's editor bar; at the narrowest window whole buttons wrap, still at the right end. */
.brief-bar { display: flex; flex: none; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; padding: 10px 12px; border-top: 1px solid var(--fg2-line); }
.brief-error { flex: 1; min-width: 0; overflow: hidden; color: var(--fg2-status-danger-fg); font-size: 12px; text-overflow: ellipsis; white-space: nowrap; }
</style>
