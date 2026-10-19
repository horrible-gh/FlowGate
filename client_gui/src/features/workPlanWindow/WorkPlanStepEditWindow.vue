<template>
  <ModelessShell ref="shell" :open="true" :initial-x="win.x" :initial-y="win.y" :initial-width="600" :initial-height="580"
    :min-width="380" :min-height="300" :minimized="win.minimized" @update:minimized="win.minimized = $event" @close="requestClose">
    <template #header><span data-test="wp-step-title">{{ t('title') }} · {{ target ? t('heading', { card: cardLabel, doc: docLabel }) : '—' }} <span v-if="dirty" :title="t('dirty')">●</span></span></template>
    <div class="step-window" data-test="wp-step-window" :data-row="target?.rowId" :data-window="win.id">
      <div class="step-scroll">
        <p v-if="target" class="step-caption" data-test="wp-step-heading">{{ t('step', { card: cardLabel, doc: docLabel, n: target.stepNo }) }}</p>
        <p v-if="!target" class="step-warning" role="alert" data-test="wp-step-gone">{{ t('gone') }}</p>
        <p v-else-if="win.session.locked()" class="step-warning" role="status" data-test="wp-step-lock" :data-reason="win.session.lockReason()">
          {{ win.session.lockReason() ? pt(`lock.${win.session.lockReason()}`) : t('busy') }}
        </p>
        <p v-if="target?.pairRole === 'result'" class="step-caption" data-test="wp-step-result">{{ t('result') }}</p>
        <template v-else>
          <label>{{ t('instruction') }}<Textarea data-test="wp-step-text" :model-value="win.draft.preInstructionText ?? ''" :rows="8" :disabled="blocked"
            :invalid="validation === 'text_limit'" @update:model-value="setValue('preInstructionText', $event)" /></label>
          <p class="step-caption" data-test="wp-step-counter">{{ t('counter', { n: charCount, max: win.session.preInstructionMaxChars() }) }}</p>
          <div class="step-attachment">
            <div class="step-attachment-head">
              <strong>{{ t('attachment') }}</strong>
              <IconButton size="sm" :label="t('attachment_info')" :title="attachmentHelp" data-test="wp-step-attachment-info"><span aria-hidden="true">ⓘ</span></IconButton>
            </div>
            <div class="step-drop" :class="{ 'is-drag-over': dragOver }" data-test="wp-step-drop"
              @dragenter="onDragOver" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
              <span class="step-file-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path :d="fileIcon" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </span>
              <span v-if="win.draft.preInstructionAttachment" class="step-filename" data-test="wp-step-file-name">{{ win.draft.preInstructionAttachment.original_filename }}</span>
              <span v-else class="step-file-empty" data-test="wp-step-file-empty">{{ win.session.attachmentStepKey() ? t('drop_hint') : t('save_first_short') }}</span>
              <input ref="fileInput" type="file" hidden data-test="wp-step-file" :disabled="blocked || !win.session.attachmentStepKey()" @change="selected" />
              <Button size="sm" :disabled="blocked || !win.session.attachmentStepKey()" data-test="wp-step-choose" @click="fileInput?.click()">
                {{ win.uploading ? t('uploading') : win.draft.preInstructionAttachment ? t('replace') : t('choose') }}
              </Button>
              <Button v-if="win.draft.preInstructionAttachment" size="sm" variant="ghost" :disabled="blocked" data-test="wp-step-remove" @click="setValue('preInstructionAttachment', null)">{{ t('remove') }}</Button>
            </div>
          </div>
        </template>
        <p v-if="win.issue || validation" class="step-warning" role="alert" data-test="wp-step-error" :data-issue="win.issue ?? validation">
          {{ t(`issue.${win.issue ?? validation}`, { max: limitLabel }) }} <span v-if="win.detail">{{ win.detail }}</span>
        </p>
      </div>
      <div class="step-footer">
        <div class="step-actions">
          <Button size="sm" variant="ghost" data-test="wp-step-cancel" @click="requestClose">{{ t('cancel') }}</Button>
          <Button size="sm" variant="primary" data-test="wp-step-save" :title="t('apply_help')" :disabled="blocked || !!validation" @click="saveWorkPlanStep(win)">{{ t('save') }}</Button>
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
import IconButton from '@/components/ui/IconButton.vue'
import Textarea from '@/components/ui/Textarea.vue'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { documentTypeNameKey, workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import type { WorkPlanStepDraft } from './workPlanEditModel'
import { workPlanStepMessages } from './stepMessages'
import { workPlanWindowMessages } from './messages'
import { closeWorkPlanStepWindow, dropWorkPlanStepFiles, saveWorkPlanStep, setWorkPlanStepValue, syncWorkPlanStep,
  uploadWorkPlanStepFile, workPlanStepBlocked, workPlanStepDirty, workPlanStepValidation, type WorkPlanStepWindow } from './workPlanStepEditWindows'

const props = defineProps<{ win: WorkPlanStepWindow }>()
const messages = { ko: { ...workPlanWindowMessages.ko, ...workPlanStepMessages.ko }, en: { ...workPlanWindowMessages.en, ...workPlanStepMessages.en }, ja: { ...workPlanWindowMessages.ja, ...workPlanStepMessages.ja } }
const { t } = useI18n({ useScope: 'local', messages, inheritLocale: true })
const pt = t
const { t: gt } = useI18n({ useScope: 'global' })
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const confirming = ref(false)
const target = computed(() => props.win.session.target())
const dirty = computed(() => workPlanStepDirty(props.win))
const blocked = computed(() => workPlanStepBlocked(props.win))
const validation = computed(() => workPlanStepValidation(props.win))
const charCount = computed(() => Array.from(props.win.draft.preInstructionText ?? '').length)
// 0073 T#2: the row is named as the main window names it — its card (작업 1) and document (작업 지시서), never the
// Step key; the review is set in the main window's review popover, so this window has no review controls.
const cardLabel = computed(() => {
  const current = target.value
  if (!current) return ''
  return t('card.name', { name: gt(`gui.document_window.${workPlanQuantityNameKey({ type: current.cardType, unit: current.cardUnit })}`), n: current.cardOrdinal })
})
const docLabel = computed(() => gt(`gui.document_window.${documentTypeNameKey(target.value?.type ?? null)}`))
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
// 0073 T#3: the attachment area shows the file icon, its name and choose/replace/remove; the size limit, the upload
// and cleanup rule and the save-first rule are in the ⓘ tooltip, the parent-draft note is on [저장]'s tooltip, and a
// refused file says why in the error line. The checks themselves are unchanged (workPlanStepEditWindows). The tooltips
// are native titles: the Tooltip primitive's bubble sits on --fg2-overlay-z (9999), under every ModelessShell panel.
const fileIcon = paletteIconPath('file')
const limitLabel = computed(() => sizeLabel(props.win.session.attachmentMaxBytes()))
const attachmentHelp = computed(() => [
  props.win.session.attachmentStepKey() ? null : t('issue.save_parent_first'),
  props.win.session.attachmentMaxBytes() > 0 ? t('file_limit_label', { n: limitLabel.value }) : null,
  t('attachment_help'),
].filter(Boolean).join(' '))
function sizeLabel(bytes: number): string {
  const unit = (n: number, name: string) => `${Number.isInteger(n) ? n : n.toFixed(1)} ${name}`
  if (bytes >= 1024 * 1024) return unit(bytes / (1024 * 1024), 'MB')
  if (bytes >= 1024) return unit(bytes / 1024, 'KB')
  return unit(bytes, 'B')
}
// A file drop only: a card dragged in the plan window carries text/plain, not Files, so it is never taken here (and
// these handlers never reach the plan window's card drop targets — the Step window is its own shell).
const dragOver = ref(false)
const isFileDrag = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')
function onDragOver(event: DragEvent): void {
  if (!isFileDrag(event)) return
  event.preventDefault()
  event.stopPropagation()
  // blocked (locked, uploading, gone) refuses the drop; a new card's drop is taken so the error line can say why
  if (event.dataTransfer) event.dataTransfer.dropEffect = blocked.value ? 'none' : 'copy'
  dragOver.value = !blocked.value && !!props.win.session.attachmentStepKey()
}
function onDragLeave(event: DragEvent): void {
  const zone = event.currentTarget as HTMLElement
  if (!(event.relatedTarget instanceof Node) || !zone.contains(event.relatedTarget)) dragOver.value = false
}
function onDrop(event: DragEvent): void {
  if (!isFileDrag(event)) return
  event.preventDefault()
  event.stopPropagation()
  dragOver.value = false
  void dropWorkPlanStepFiles(props.win, event.dataTransfer?.files ?? [])
}
watch(() => props.win.raise, () => shell.value?.bringToFront())
watch(() => props.win.session.snapshot(), () => syncWorkPlanStep(props.win), { deep: true })
</script>

<style scoped>
.step-window { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 12px; }
.step-scroll { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 10px; }
.step-caption { margin: 0; color: var(--fg2-muted); font-size: 12px; line-height: 1.5; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.step-warning { margin: 0; padding: 8px; border: 1px solid var(--fg2-line); color: var(--fg2-status-warning-fg); font-size: 12px; overflow-wrap: anywhere; }
.step-attachment { display: flex; flex-direction: column; gap: 6px; font-size: 12px; }
.step-attachment-head { display: flex; align-items: center; gap: 6px; }
.step-drop { display: flex; align-items: center; gap: 10px; min-height: 56px; padding: 8px 10px; border: 1px dashed var(--fg2-line-strong); border-radius: var(--fg2-control-radius); }
.step-drop.is-drag-over { border-color: var(--fg2-focus); background: color-mix(in srgb, var(--fg2-focus) 10%, transparent); }
.step-file-icon { display: grid; flex: none; place-items: center; width: 32px; height: 32px; border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius-sm); color: var(--fg2-muted); }
.step-file-icon svg { width: 18px; height: 18px; }
.step-filename, .step-file-empty { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.step-file-empty { color: var(--fg2-muted); }
.step-footer { border-top: 1px solid var(--fg2-line); padding-top: 10px; display: flex; flex-direction: column; gap: 8px; }
.step-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
