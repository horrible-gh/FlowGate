<template>
  <ModelessShell ref="shell" :open="true" :initial-x="Math.min(win.x, Math.max(0, viewportWidth - initialWidth - 16))" :initial-y="Math.min(win.y, Math.max(0, viewportHeight - initialHeight - 16))" :initial-width="initialWidth" :initial-height="initialHeight"
    :min-width="540" :min-height="350" :minimized="win.minimized" @update:minimized="win.minimized = $event" @close="requestClose">
    <template #header><span class="step-title" data-test="wp-step-title"><span class="step-tile" aria-hidden="true"><svg viewBox="0 0 24 24"><path :d="paletteIconPath('pencil')" /></svg></span><strong>{{ t('title') }}</strong><span class="step-title-context">{{ target ? t('heading', { card: cardLabel, doc: docLabel }) : '—' }}</span><span v-if="dirty" :title="t('dirty')">●</span></span></template>
    <div class="step-window" data-test="wp-step-window" :data-row="target?.rowId" :data-window="win.id">
      <div class="step-scroll">
        <div v-if="target" class="step-context" data-test="wp-step-heading">
          <span class="step-document-icon"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="paletteIconPath(target.pairRole === 'result' ? 'clipboard' : 'file')" /></svg></span>
          <strong>{{ docLabel }}</strong><span class="step-position">{{ t('document.position', { n: target.stepNo }) }}</span>
        </div>
        <p v-if="!target" class="step-warning" role="alert" data-test="wp-step-gone">{{ t('gone') }}</p>
        <p v-else-if="win.session.locked()" class="step-warning" role="status" data-test="wp-step-lock" :data-reason="win.session.lockReason()">
          {{ win.session.lockReason() ? pt(`lock.${win.session.lockReason()}`) : t('busy') }}
        </p>
        <p v-if="target && !workPlanStepHasEditor(target)" class="step-caption" data-test="wp-step-result">{{ t('result') }}</p>
        <template v-else>
          <label class="step-instruction">{{ t('instruction') }}
            <span class="step-text-wrap">
              <Textarea data-test="wp-step-text" :model-value="win.draft.preInstructionText ?? ''" :rows="6" :disabled="blocked" :placeholder="t('instruction_hint')"
                :invalid="validation === 'text_limit'" @update:model-value="setValue('preInstructionText', $event)" />
              <span class="step-counter" data-test="wp-step-counter">{{ t('counter', { n: charCount.toLocaleString(), max: win.session.preInstructionMaxChars().toLocaleString() }) }}</span>
            </span>
          </label>
          <div class="step-attachment">
            <div class="step-attachment-head">
              <strong>{{ t('attachment') }}</strong>
              <IconButton size="sm" :label="t('attachment_info')" :title="attachmentHelp" data-test="wp-step-attachment-info"><span aria-hidden="true">ⓘ</span></IconButton>
            </div>
            <div class="step-drop" :class="{ 'is-drag-over': dragOver }" data-test="wp-step-drop"
              @dragenter="onDragOver" @dragover="onDragOver" @dragleave="onDragLeave" @drop="onDrop">
              <span class="step-file-icon" aria-hidden="true">
                <svg viewBox="0 0 24 24"><path :d="fileIcon" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" /></svg>
              </span>
              <span class="step-file-copy">
                <span v-if="win.draft.preInstructionAttachment" class="step-filename" :title="attachmentName" data-test="wp-step-file-name">{{ attachmentName }}</span>
                <span v-else class="step-file-empty" data-test="wp-step-file-empty">{{ win.session.attachmentStepKey() ? t('drop_hint') : t('save_first_short') }}</span>
                <small class="step-file-sub" data-test="wp-step-file-sub">{{ attachmentSub }}</small>
              </span>
              <input ref="fileInput" type="file" hidden data-test="wp-step-file" :disabled="blocked || !win.session.attachmentStepKey()" @change="selected" />
              <Button size="sm" :disabled="blocked || !win.session.attachmentStepKey()" data-test="wp-step-choose" @click="fileInput?.click()">
                <span class="step-button-content"><svg v-if="!win.uploading" viewBox="0 0 24 24" aria-hidden="true"><path :d="win.draft.preInstructionAttachment ? REPLACE_PATH : PLUS_PATH" /></svg>{{ win.uploading ? t('uploading') : win.draft.preInstructionAttachment ? t('replace') : t('choose') }}</span>
              </Button>
              <Button v-if="win.draft.preInstructionAttachment" size="sm" variant="ghost" class="step-remove" :title="t('remove')" :disabled="blocked" data-test="wp-step-remove" @click="setValue('preInstructionAttachment', null)">
                <span class="step-button-content"><svg viewBox="0 0 24 24" aria-hidden="true"><path :d="TRASH_PATH" /></svg><span class="step-sr">{{ t('remove') }}</span></span>
              </Button>
            </div>
          </div>
        </template>
        <p v-if="win.issue || validation" class="step-warning" role="alert" data-test="wp-step-error" :data-issue="win.issue ?? validation">
          {{ t(`issue.${win.issue ?? validation}`, { max: limitLabel }) }} <span v-if="win.detail">{{ win.detail }}</span>
        </p>
      </div>
      <div class="step-footer">
        <span class="step-caption" role="status" data-test="wp-step-status">{{ dirty ? t('dirty') : t('unchanged') }}</span>
        <div class="step-actions">
          <Button size="sm" variant="ghost" data-test="wp-step-cancel" @click="requestClose">{{ t('cancel') }}</Button>
          <Button size="sm" variant="primary" data-test="wp-step-save" :title="t('apply_help')" :disabled="blocked || !!validation" @click="saveWorkPlanStep(win)">
            <span class="step-button-content"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m5 12 5 5 9-10" /></svg>{{ t('save') }}</span>
          </Button>
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
import { workPlanQuantityNameKey } from '@/features/documentWindow/specialDocumentModel'
import type { WorkPlanStepDraft } from './workPlanEditModel'
import { workPlanStepMessages } from './stepMessages'
import { workPlanDocumentNameKey } from './workPlanPresentation'
import { workPlanWindowMessages } from './messages'
import { closeWorkPlanStepWindow, dropWorkPlanStepFiles, saveWorkPlanStep, setWorkPlanStepValue, syncWorkPlanStep,
  uploadWorkPlanStepFile, workPlanStepHasEditor, workPlanStepBlocked, workPlanStepDirty, workPlanStepValidation, type WorkPlanStepWindow } from './workPlanStepEditWindows'

const props = defineProps<{ win: WorkPlanStepWindow }>()
const messages = { ko: { ...workPlanWindowMessages.ko, ...workPlanStepMessages.ko }, en: { ...workPlanWindowMessages.en, ...workPlanStepMessages.en }, ja: { ...workPlanWindowMessages.ja, ...workPlanStepMessages.ja } }
const { t } = useI18n({ useScope: 'local', messages, inheritLocale: true })
const pt = t
const { t: gt } = useI18n({ useScope: 'global' })
const viewportWidth = window.innerWidth
const viewportHeight = window.innerHeight
const initialWidth = Math.min(730, Math.max(320, viewportWidth - 32))
const initialHeight = Math.min(480, Math.max(280, viewportHeight - 64))
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)
const confirming = ref(false)
const target = computed(() => props.win.session.target())
const dirty = computed(() => workPlanStepDirty(props.win))
const blocked = computed(() => workPlanStepBlocked(props.win))
const validation = computed(() => workPlanStepValidation(props.win))
const charCount = computed(() => Array.from(props.win.draft.preInstructionText ?? '').length)
const attachmentName = computed(() => String(props.win.draft.preInstructionAttachment?.original_filename ?? ''))
// 0073 T#2: the row is named as the main window names it — its card (작업 1) and document (작업 지시서), never the
// Step key; the review is set in the main window's review popover, so this window has no review controls.
// T0015: compact document identity, counter inside the text field, and no repeated card name in the body.
const cardLabel = computed(() => {
  const current = target.value
  if (!current) return ''
  return t('card.name', { name: gt(`gui.document_window.${workPlanQuantityNameKey({ type: current.cardType, unit: current.cardUnit })}`), n: current.cardOrdinal })
})
const docLabel = computed(() => target.value ? t(workPlanDocumentNameKey(target.value.type, target.value.pairRole)) : '')
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
/** Lucide `paperclip` / `plus` / `refresh-cw` / `trash-2` (ISC): the attachment card's marks, as in the confirmed mockup. */
const fileIcon = 'm21.4 11.1-9.2 9.2a6 6 0 0 1-8.5-8.5l8.6-8.6a4 4 0 0 1 5.7 5.7l-8.6 8.6a2 2 0 0 1-2.8-2.8l8-8'
const PLUS_PATH = 'M12 5v14M5 12h14'
const REPLACE_PATH = 'M3 12a9 9 0 0 1 15-6.7L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-15 6.7L3 16 M3 21v-5h5'
const TRASH_PATH = 'M3 6h18 M8 6V4h8v2 M19 6l-1 14H6L5 6 M10 11v6M14 11v6'
/** The card's second line: no file, or the file's size when the server gave one. */
const attachmentSub = computed(() => {
  const file = props.win.draft.preInstructionAttachment
  if (!file) return t('no_file')
  const size = Number(file.size_bytes ?? file.size)
  return Number.isFinite(size) && size > 0 ? t('file_one_size', { n: sizeLabel(size) }) : t('file_one')
})
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
.step-title { display: inline-flex; align-items: center; gap: 9px; max-width: 100%; }
.step-tile { display: grid; flex: none; place-items: center; width: 25px; height: 25px; border-radius: var(--fg2-control-radius-sm); background: color-mix(in srgb, var(--fg2-cyan) 22%, transparent); color: var(--fg2-cyan); }
.step-tile svg, .step-button-content svg { width: 14px; height: 14px; fill: none; stroke: currentColor; stroke-width: 1.8; stroke-linecap: round; stroke-linejoin: round; }
.step-button-content { display: inline-flex; align-items: center; gap: 6px; }
.step-remove { color: var(--fg2-status-danger-fg); }
.step-sr { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.step-title-context { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg2-muted); font-size: 11px; font-weight: 500; }
.step-window { display: flex; flex-direction: column; height: 100%; min-height: 0; gap: 10px; }
.step-scroll { flex: 1; min-height: 0; overflow: auto; display: flex; flex-direction: column; gap: 16px; }
.step-context { display: flex; align-items: center; gap: 8px; flex: none; font-size: 12px; }
.step-position { margin-left: auto; color: var(--fg2-muted); font-size: 11px; }
.step-document-icon { display: grid; place-items: center; width: 25px; height: 25px; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm); color: var(--fg2-muted); }
.step-document-icon svg { width: 16px; height: 16px; fill: none; stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
.step-caption { margin: 0; color: var(--fg2-muted); font-size: 11px; line-height: 1.5; }
.step-instruction { display: flex; flex-direction: column; gap: 6px; color: var(--fg2-muted); font-size: 11.5px; font-weight: 600; }
.step-text-wrap { position: relative; display: block; }
.step-text-wrap :deep(textarea) { display: block; height: 173px; min-height: 120px; padding: 12px 14px 28px; font-size: 12px; font-weight: 400; }
.step-counter { position: absolute; bottom: 9px; right: 12px; font-size: 10px; font-weight: 400; pointer-events: none; }
.step-warning { margin: 0; padding: 8px; border: 1px solid var(--fg2-line); color: var(--fg2-status-warning-fg); font-size: 12px; overflow-wrap: anywhere; }
.step-attachment { display: flex; flex-direction: column; gap: 7px; font-size: 12px; }
.step-attachment-head { display: flex; align-items: center; justify-content: space-between; gap: 6px; color: var(--fg2-muted); font-size: 11.5px; }
.step-drop { display: flex; align-items: center; gap: 12px; min-height: 73px; padding: 11px 13px; border: 1px dashed var(--fg2-line-strong); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface); }
.step-drop.is-drag-over { border-color: var(--fg2-focus); background: color-mix(in srgb, var(--fg2-focus) 10%, transparent); }
.step-file-icon { display: grid; flex: none; place-items: center; width: 36px; height: 36px; border-radius: 10px; background: var(--fg2-status-info-bg, var(--fg2-control-surface-strong)); color: var(--fg2-status-info-fg, var(--fg2-muted)); }
.step-file-icon svg { width: 19px; height: 19px; }
.step-file-copy { display: flex; flex: 1; flex-direction: column; gap: 2px; min-width: 0; }
.step-filename, .step-file-empty { overflow: hidden; color: var(--fg2-text); font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.step-file-sub { color: var(--fg2-muted); font-size: 10.5px; }
.step-footer { flex: none; border-top: 1px solid var(--fg2-line); padding-top: 10px; display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.step-actions { display: flex; justify-content: flex-end; gap: 8px; }
</style>
