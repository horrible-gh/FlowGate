<template>
  <section v-if="target.nodeType === 'gate' && panel.open" class="gate-memo-panel" data-test="gate-memo-panel">
    <textarea v-model="draft" :aria-label="t(k('label'))" :placeholder="t(k('label'))" :disabled="saving || loading || !!memo.lockedReason.value" data-test="gate-memo-text" />
    <p v-if="failure" role="alert">{{ failure }}</p>
    <div class="gate-memo-actions">
      <Button size="sm" variant="secondary" :disabled="saving" data-test="gate-memo-close" @click="panel.open = false">{{ t(k('close')) }}</Button>
      <Button size="sm" :disabled="saving || loading || !!memo.lockedReason.value" data-test="gate-memo-save" @click="save">{{ t(k('save')) }}</Button>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { useCardMemo } from './useCardMemo'
import { gateMemoPanel } from './useGateMemo'
const props = defineProps<{ target: InspectorTarget }>()
const { t } = useI18n()
const k = (key: string) => `gui.inspector.cardMemo.${key}`
const panel = computed(() => gateMemoPanel(props.target))
const memo = useCardMemo(() => props.target.nodeType === 'gate' ? props.target : null)
const draft = ref('')
const saving = ref(false)
const loading = ref(false)
const failure = ref<string | null>(null)
let opening = 0
watch(() => `${props.target.flowId}\n${props.target.nodeId}\n${panel.value.open}`, async () => {
  const at = ++opening
  failure.value = null
  if (!panel.value.open || props.target.nodeType !== 'gate') return
  loading.value = true
  await memo.reload()
  if (at !== opening) return
  draft.value = memo.memos.value[0]?.body ?? ''
  loading.value = false
  if (memo.readFailure.value) failure.value = t(k(`failure.${memo.readFailure.value.key}`))
}, { immediate: true })
async function save() {
  const card = memo.card.value
  if (!card || saving.value || loading.value || memo.lockedReason.value || memo.readFailure.value) return
  const at = opening
  const pane = panel.value
  const existing = memo.memos.value[0]
  if (existing?.body === draft.value) { pane.open = false; return }
  saving.value = true
  const result = existing ? await memo.update(card, existing.memoId, { body: draft.value }) : await memo.create(card, { title: t(k('label')), body: draft.value, aiRef: false })
  saving.value = false
  if (at !== opening) return
  if (result.ok) pane.open = false
  else failure.value = t(k(`failure.${result.failure.key}`))
}
</script>
<style scoped>
.gate-memo-panel { padding: 12px 14px; background: #10243b; border-bottom: 1px solid #23405d; }
.gate-memo-panel textarea { box-sizing: border-box; width: 100%; height: 66px; resize: vertical; border: 1px solid #34516d; border-radius: 6px; padding: 7px 9px; background: #081b2e; color: #e2effe; }
.gate-memo-actions { display: flex; justify-content: flex-end; gap: 7px; margin-top: 6px; }
.gate-memo-panel p { color: #e56b82; }
</style>
