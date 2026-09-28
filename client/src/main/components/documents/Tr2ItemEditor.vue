<template>
  <form class="tr2-item-editor" data-testid="tr2-item-editor" @submit.prevent="submit">
    <h4>{{ originalId ? t('main.tr2_body.editor.item_edit', { id: originalId }) : t('main.tr2_body.editor.item_new') }}</h4>
    <label>{{ t('main.tr2_body.editor.collection') }}
      <select v-model="op" name="op" :disabled="disabled">
        <option value="edit">{{ t('main.tr2_body.files.op_edit') }}</option>
        <option value="create_file">{{ t('main.tr2_body.files.op_create') }}</option>
        <option value="deferred">{{ t('main.tr2_body.files.op_deferred') }}</option>
      </select>
    </label>
    <label>{{ t('main.tr2_body.editor.id') }}<input v-model="draft.id" name="id" :disabled="disabled" required></label>
    <label>{{ t('main.tr2_body.editor.file') }}<input v-model="draft.file" name="file" :disabled="disabled" :required="op !== 'deferred'"></label>
    <template v-if="op === 'edit'">
      <label>{{ t('main.tr2_body.editor.anchor_old') }}<textarea v-model="draft.anchor_old" name="anchor_old" rows="5" :disabled="disabled" required /></label>
      <label>{{ t('main.tr2_body.editor.replacement_new') }}<textarea v-model="draft.replacement_new" name="replacement_new" rows="5" :disabled="disabled" /></label>
    </template>
    <label v-else-if="op === 'create_file'">{{ t('main.tr2_body.editor.content') }}<textarea v-model="draft.content" name="content" rows="8" :disabled="disabled" required /></label>
    <label v-else>{{ t('main.tr2_body.editor.reason') }}
      <select v-model="draft.reason" name="reason" :disabled="disabled">
        <option v-for="reason in DEFERRED_REASONS" :key="reason" :value="reason">{{ t(`main.tr2_body.deferred_reason.${reason}`) }}</option>
      </select>
    </label>
    <label v-if="op !== 'deferred'">{{ t('main.tr2_body.editor.confidence') }}
      <select v-model="draft.confidence" name="confidence" :disabled="disabled">
        <option value="high">high</option><option value="medium">medium</option><option value="low">low</option>
      </select>
    </label>
    <label>{{ t('main.tr2_body.editor.rationale') }}<textarea v-model="draft.rationale" name="rationale" rows="3" :disabled="disabled" required /></label>
    <div class="tr2-item-editor__actions">
      <button type="submit" class="btn btn-primary btn-sm" :disabled="disabled">{{ t('main.tr2_body.actions.save') }}</button>
      <button type="button" class="btn btn-outline btn-sm" @click="emit('cancel')">{{ t('main.tr2_body.actions.cancel') }}</button>
    </div>
  </form>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { itemFromDraft, type ItemCollection, type ItemOp, type Row } from './tr2State'

const DEFERRED_REASONS = ['not_expressible_as_edit', 'needs_runtime', 'policy_direction', 'multi_file_design', 'anchor_not_grounded'] as const

const props = defineProps<{ initialOp: ItemOp; item?: Row | null; disabled?: boolean }>()
const emit = defineEmits<{ (e: 'save', value: { collection: ItemCollection; item: Row; originalId: string | null }): void; (e: 'cancel'): void }>()
const { t } = useI18n()

const original = props.item ?? {}
const originalId = (original.id as string | undefined) ?? null
const op = ref<ItemOp>(props.initialOp)
const draft = reactive<Row>({
  id: original.id ?? '', file: original.file ?? '', anchor_old: original.anchor_old ?? '',
  replacement_new: original.replacement_new ?? '', content: original.content ?? '',
  rationale: original.rationale ?? '', confidence: original.confidence ?? 'medium',
  reason: original.reason ?? 'needs_runtime',
})

function submit() {
  if (props.disabled) return
  emit('save', { ...itemFromDraft(op.value, draft, original), originalId })
}
</script>

<style scoped>
.tr2-item-editor { display: grid; gap: 8px; border: 1px solid var(--border, #d9e1e8); border-radius: 8px; padding: 12px; }
.tr2-item-editor h4 { margin: 0; }
.tr2-item-editor label { display: grid; gap: 4px; font-size: .85rem; }
.tr2-item-editor textarea, .tr2-item-editor input, .tr2-item-editor select { box-sizing: border-box; width: 100%; font-family: inherit; }
.tr2-item-editor textarea { font-family: monospace; }
.tr2-item-editor__actions { display: flex; gap: 8px; }
</style>
