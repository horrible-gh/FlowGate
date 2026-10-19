<template>
  <ModelessShell ref="shell" :open="true" :initial-x="win.x" :initial-y="win.y" :initial-width="600" :initial-height="580"
    :min-width="380" :min-height="300" :minimized="win.minimized" @update:minimized="win.minimized = $event" @close="requestClose">
    <template #header><span>{{ t('title') }} · {{ target?.stepKey ?? '—' }} <span v-if="dirty" :title="t('dirty')">●</span></span></template>
    <div class="step-window" data-test="wp-step-window" :data-row="target?.rowId" :data-window="win.id">
      <div class="step-scroll">
        <p v-if="target" class="step-caption" data-test="wp-step-heading">{{ t('step', { type: typeLabel, key: target.stepKey, n: target.stepNo }) }}</p>
        <p v-if="!target" class="step-warning" role="alert" data-test="wp-step-gone">{{ t('gone') }}</p>
        <p v-else-if="win.session.locked()" class="step-warning" role="status" data-test="wp-step-lock" :data-reason="win.session.lockReason()">
          {{ win.session.lockReason() ? pt(`lock.${win.session.lockReason()}`) : t('busy') }}
        </p>
        <div class="step-review">
          <label>{{ t('review') }}<Select data-test="wp-step-review" :model-value="String(win.draft.reviewCount)" :options="reviewOptions" :disabled="blocked"
            @update:model-value="setValue('reviewCount', Number($event))" /></label>
          <label>{{ t('reviewer') }}<Select data-test="wp-step-reviewer" :model-value="win.draft.reviewerProviderId ?? ''" :options="reviewerOptions"
            :disabled="blocked || win.draft.reviewCount === 0" @update:model-value="setValue('reviewerProviderId', $event || null)" /></label>
        </div>
        <p v-if="target?.pairRole === 'result'" class="step-caption" data-test="wp-step-result">{{ t('result') }}</p>
        <template v-else>
          <label>{{ t('instruction') }}<Textarea data-test="wp-step-text" :model-value="win.draft.preInstructionText ?? ''" :rows="8" :disabled="blocked"
            :invalid="validation === 'text_limit'" @update:model-value="setValue('preInstructionText', $event)" /></label>
          <p class="step-caption" data-test="wp-step-counter">{{ t('counter', { n: charCount, max: win.session.preInstructionMaxChars() }) }}</p>
          <div class="step-attachment">
            <strong>{{ t('attachment') }}</strong>
            <span v-if="win.draft.preInstructionAttachment" class="step-filename" data-test="wp-step-file-name">{{ win.draft.preInstructionAttachment.original_filename }}</span>
            <div class="step-actions">
              <input ref="fileInput" type="file" hidden data-test="wp-step-file" :disabled="blocked || !win.session.attachmentStepKey()" @change="selected" />
              <Button size="sm" :disabled="blocked || !win.session.attachmentStepKey()" data-test="wp-step-choose" @click="fileInput?.click()">{{ win.uploading ? t('uploading') : t('choose') }}</Button>
              <Button v-if="win.draft.preInstructionAttachment" size="sm" variant="ghost" :disabled="blocked" data-test="wp-step-remove" @click="setValue('preInstructionAttachment', null)">{{ t('remove') }}</Button>
            </div>
            <p v-if="!win.session.attachmentStepKey() && target" class="step-caption" data-test="wp-step-save-first">{{ t('save_parent_first') }}</p>
            <p v-if="win.session.attachmentMaxBytes() > 0" class="step-caption">{{ t('file_limit_label', { n: win.session.attachmentMaxBytes() }) }}</p>
            <p class="step-caption">{{ t('attachment_help') }}</p>
          </div>
        </template>
        <p v-if="win.issue || validation" class="step-warning" role="alert" data-test="wp-step-error">{{ t(`issue.${win.issue ?? validation}`) }} <span v-if="win.detail">{{ win.detail }}</span></p>
      </div>
      <div class="step-footer">
        <p class="step-caption">{{ t('apply_help') }}</p>
        <div class="step-actions">
          <Button size="sm" variant="ghost" data-test="wp-step-cancel" @click="requestClose">{{ t('cancel') }}</Button>
          <Button size="sm" variant="primary" data-test="wp-step-save" :disabled="blocked || !!validation" @click="saveWorkPlanStep(win)">{{ t('save') }}</Button>
        </div>
      </div>
    </div>
  </ModelessShell>
  <ConfirmDialog :open="confirming" :title="t('discard')" :confirm-label="t('discard_ok')" :cancel-label="t('keep')" danger
    @confirm="closeWorkPlanStepWindow(win)" @cancel="confirming = false" />
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import Button from '@/components/ui/Button.vue'
import Select from '@/components/ui/Select.vue'
import Textarea from '@/components/ui/Textarea.vue'
import type { WorkPlanStepDraft } from './workPlanEditModel'
import { workPlanStepMessages } from './stepMessages'
import { workPlanWindowMessages } from './messages'
import { closeWorkPlanStepWindow, saveWorkPlanStep, setWorkPlanStepValue, syncWorkPlanStep,
  uploadWorkPlanStepFile, workPlanStepBlocked, workPlanStepDirty, workPlanStepValidation, type WorkPlanStepWindow } from './workPlanStepEditWindows'

const props = defineProps<{ win: WorkPlanStepWindow }>()
const messages = { ko: { ...workPlanWindowMessages.ko, ...workPlanStepMessages.ko }, en: { ...workPlanWindowMessages.en, ...workPlanStepMessages.en }, ja: { ...workPlanWindowMessages.ja, ...workPlanStepMessages.ja } }
const { t } = useI18n({ useScope: 'local', messages, inheritLocale: true })
const pt = t
const { t: gt, te: gte } = useI18n({ useScope: 'global' })
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const confirming = ref(false)
const target = computed(() => props.win.session.target())
const dirty = computed(() => workPlanStepDirty(props.win))
const blocked = computed(() => workPlanStepBlocked(props.win))
const validation = computed(() => workPlanStepValidation(props.win))
const charCount = computed(() => Array.from(props.win.draft.preInstructionText ?? '').length)
const typeLabel = computed(() => {
  const code = target.value?.type ?? ''
  const key = `gui.document_window.types.${code}`
  return gte(key) ? gt(key) : code
})
const reviewOptions = computed(() => props.win.session.reviewCountChoices().map((count) => ({ value: String(count), label: count === 0 ? t('none') : count === -1 ? t('unlimited') : t('times', { n: count }) })))
const reviewerOptions = computed(() => {
  const options = props.win.session.reviewerOptions(t('default_reviewer'), (name) => t('unavailable', { name }))
  const selected = props.win.draft.reviewerProviderId
  // A reviewer chosen only in this child may disappear from the refreshed project list.
  if (selected && !options.some((option) => option.value === selected)) options.push({ value: selected, label: t('unavailable', { name: selected }) })
  return options
})
function setValue<K extends keyof WorkPlanStepDraft>(key: K, value: WorkPlanStepDraft[K]): void { setWorkPlanStepValue(props.win, key, value) }
function requestClose(): void {
  if (dirty.value || props.win.uploading) confirming.value = true
  else closeWorkPlanStepWindow(props.win)
}
function selected(event: Event): void {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  input.value = ''
  if (file) void uploadWorkPlanStepFile(props.win, file)
}
watch(() => props.win.raise, () => shell.value?.bringToFront())
watch(() => props.win.session.snapshot(), () => syncWorkPlanStep(props.win), { deep: true })
</script>

<style scoped>
.step-window { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 12px; }
.step-scroll { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
.step-caption { margin: 0; color: var(--fg2-muted); font-size: 12px; line-height: 1.5; }
.step-review { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.step-warning { margin: 0; padding: 8px; border: 1px solid var(--fg2-line); color: var(--fg2-status-warning-fg); font-size: 12px; overflow-wrap: anywhere; }
.step-attachment { display: flex; flex-direction: column; gap: 8px; padding: 10px; border: 1px solid var(--fg2-line); font-size: 12px; }
.step-filename { overflow-wrap: anywhere; }
.step-footer { border-top: 1px solid var(--fg2-line); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.step-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
