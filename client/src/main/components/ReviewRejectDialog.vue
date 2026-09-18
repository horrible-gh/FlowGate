<template>
  <!-- flowgate.default.0560 T0016 §2.2 (2단계) — migrated onto the common dialog layer.
       D0008 "기존 instance 이관 목적지" maps this instance to `form-actions`.
       `size="lg"`: the variant default (md, 520px) would undo 0419 T0006, which widened
       this box from 480px to 620px precisely because a long rejection reason did not fit;
       lg (720px) is the nearest size that never re-narrows it. -->
  <DialogShell
    ref="shellRef"
    :open="visible"
    variant="form-actions"
    size="lg"
    @request-close="onClose"
    @opened="onOpened"
  >
    <template #header>
      <DialogHeader :title="t('main.review_reject_dialog.title')" @close="onHeaderClose">
        <template #icon>
          <AppIcon name="x-circle" style="color:var(--danger);" />
        </template>
      </DialogHeader>
    </template>

    <template #default="{ descriptionId }">
      <div class="rrd-body">
        <div :id="descriptionId" class="rrd-doc-info">
          <span class="rrd-doc-label">{{ t('main.review_reject_dialog.target_doc') }}</span>
          <span class="rrd-doc-name">{{ displayDocName }}</span>
        </div>

        <div class="rrd-field">
          <label class="rrd-field-label" for="rrd-reason">{{ t('main.review_reject_dialog.reason_label') }}</label>
          <textarea
            id="rrd-reason"
            ref="textareaRef"
            v-model="reason"
            class="rrd-textarea"
            rows="5"
            :placeholder="t('main.review_reject_dialog.reason_placeholder')"
            :disabled="saved"
          ></textarea>
        </div>
      </div>
    </template>

    <template #footer>
      <!-- The 반려 ▼ control is a compound control — its drop-up panel owns buttons of
           its own, and `DialogFooter`'s `action-*` slot injects content INSIDE the
           `<button>` it renders, so the panel cannot live there without nesting
           interactive elements. The trigger itself, though, is an ordinary `DialogAction`
           (id `reject-menu`, role `aux`) like every other footer button: it goes through
           `footerRolePriority` ordering, the duplicate-role check, disabled/busy and the
           single execution path exactly like 닫기/저장. Only the panel is special-cased,
           via DialogFooter's `popover-{id}` slot, which renders it as that action's
           sibling inside `.fg-dialog-footer__action` (`position: relative`) so it can be
           anchored with plain `position: absolute` instead of nesting.
           0419 T0006: edit mode only corrects existing wording, it doesn't re-copy a
           mention or re-invoke AI (that stays scoped to the original reject action;
           NR0003 §risk 5) — editMode leaves `reject-menu` out of `actions` entirely. -->
      <DialogFooter :actions="actions">
        <template #action-reject-menu>
          {{ t('main.review_reject_dialog.reject') }} <AppIcon name="caret-down" />
        </template>
        <template #popover-reject-menu>
          <div v-if="dropdownOpen" ref="dropdownPanelRef" class="rrd-dropdown">
            <button
              type="button"
              class="rrd-dropdown-item"
              @click="onCopyMention"
            >
              <AppIcon name="copy" /> {{ t('main.review_reject_dialog.copy_mention') }}
            </button>
            <!-- Group 0223: in-app invoke beside every copy-mention (side by side, not either/or). -->
            <button
              type="button"
              class="rrd-dropdown-item"
              @click="onInvokeAi"
            >
              <AppIcon name="robot" /> {{ t('main.review_reject_dialog.invoke_ai') }}
            </button>
            <button
              type="button"
              class="rrd-dropdown-item"
              disabled
              :title="t('main.review_reject_dialog.coming_soon')"
            >
              <AppIcon name="terminal" /> {{ t('main.review_reject_dialog.invoke_command') }}
            </button>
          </div>
        </template>
        <template #action-save>
          <template v-if="saving">
            <AppIcon name="spinner" spin /> {{ t('main.review_reject_dialog.saving') }}
          </template>
          <template v-else-if="saved">
            <AppIcon name="check" /> {{ t('main.review_reject_dialog.saved') }}
          </template>
          <template v-else>
            <AppIcon name="floppy-disk" /> {{ t('main.review_reject_dialog.save_message') }}
          </template>
        </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocTypeStore } from '../stores/docTypeStore'

import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'

const props = defineProps<{
  visible: boolean
  docId: string
  docName?: string
  docType?: string | null
  existingReason?: string | null
  // 0419 T0006: correct the latest rejection's wording instead of filing a new
  // rejection. The dialog itself doesn't call any API — this only changes what
  // it shows (no reject-dropdown); the parent decides which endpoint save-reason maps to.
  editMode?: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  'save-reason': [reason: string]
  'copy-mention': [reason: string]
  'invoke-command': [reason: string]
  'invoke-ai': [reason: string]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()
const textareaRef = ref<HTMLTextAreaElement | null>(null)
const shellRef = ref<InstanceType<typeof DialogShell> | null>(null)
const dropdownPanelRef = ref<HTMLElement | null>(null)
const reason = ref('')
const saving = ref(false)
const saved = ref(false)
const dropdownOpen = ref(false)
const displayDocName = computed(() => {
  const raw = props.docName || props.docId
  if (!props.docType) return raw
  const localizedType = docTypeStore.getLabel(props.docType)
  return raw.replace(/^\[[^\]]+\]/, `[${localizedType}]`)
})

/**
 * Role assignment (T0016 §2.2 asked the TR to fix it and record the reasoning).
 *
 * `저장` is `primary` with `tone: 'danger'`, NOT `role: 'danger'`. `dialogTypes.ts`
 * separates the two by function, not by colour: `primary` is "the one action that
 * finishes the dialog", `danger` is "a separate destructive action that is NOT the
 * primary". Saving the reason is what completes this dialog — the parent answers with
 * `notifySaved()`, which closes it — so it holds the primary position, and D0008 §6
 * "Danger Confirm"(`[취소] [위험 주버튼]`) is exactly the rule for a destructive action
 * in that position. Reading it the other way (`role: 'danger'`, no primary at all) would
 * leave 닫기 as the right-most button again, which is the very placement T0016 §2.1 #6
 * removed `margin-left: auto` to stop.
 *
 * `닫기` is `cancel` (Type A form cancel: it leaves without committing), so the rendered
 * order is `[반려 ▼ (aux)] [닫기] [저장]` — cancel immediately left of the primary, the
 * same shape the other four components in this migration now have.
 *
 * `반려 ▼` (`reject-menu`) is `aux` too, like every other footer button here — it is a
 * real `DialogAction`, not a hand-placed element wearing the `aux` attribute, so it goes
 * through the same ordering/dedup/disabled/busy path (see the footer template's
 * `popover-reject-menu` slot for why only its drop-up panel is special-cased). editMode
 * leaves it out of the array entirely, matching the old `v-if="!editMode"` wrapper.
 */
const actions = computed<DialogAction[]>(() => {
  const list: DialogAction[] = []
  if (!props.editMode) {
    list.push({
      id: 'reject-menu',
      label: t('main.review_reject_dialog.reject'),
      role: 'aux',
      onSelect: toggleDropdown,
    })
  }
  list.push(
    {
      id: 'close',
      label: t('common.close'),
      role: 'cancel',
      onSelect: onClose,
    },
    {
      id: 'save',
      label: t('main.review_reject_dialog.save_message'),
      role: 'primary',
      tone: 'danger',
      disabled: saved.value || saving.value || reason.value.trim().length === 0,
      onSelect: onSaveReason,
    },
  )
  return list
})

watch(
  () => props.visible,
  (v) => {
    if (v) {
      reason.value = props.existingReason ?? ''
      saved.value = false
      saving.value = false
      dropdownOpen.value = false
    }
  },
)

/**
 * `opened` fires after DialogShell applied its own initial focus, so putting the caret in
 * the reason field here reproduces the pre-migration behaviour deterministically instead
 * of racing the shell. As before, an existing reason is left alone — the shell's
 * fallback (the primary button) takes focus in that case.
 */
function onOpened() {
  if (reason.value) return
  void nextTick(() => textareaRef.value?.focus())
}

watch(
  () => props.existingReason,
  (v) => {
    if (!saved.value) {
      reason.value = v ?? ''
    }
  },
)

// header X / ESC / backdrop and the 닫기 button all land here — one close meaning.
function onClose() {
  dropdownOpen.value = false
  emit('update:visible', false)
}

function onHeaderClose() {
  shellRef.value?.requestClose('header')
}

async function onSaveReason() {
  const trimmed = reason.value.trim()
  if (!trimmed || saving.value || saved.value) return
  saving.value = true
  emit('save-reason', trimmed)
}

function toggleDropdown() {
  dropdownOpen.value = !dropdownOpen.value
}

function onCopyMention() {
  dropdownOpen.value = false
  const r = reason.value.trim() || props.existingReason?.trim() || ''
  emit('copy-mention', r)
}

// Group 0223: in-app invoke with the same live reason the copy button would embed.
function onInvokeAi() {
  dropdownOpen.value = false
  const r = reason.value.trim() || props.existingReason?.trim() || ''
  emit('invoke-ai', r)
}

/*
 * The trigger button is now rendered BY `DialogFooter` (an ordinary `DialogAction`), so
 * this component can no longer put `@click.stop` on it to keep its own toggle click from
 * reaching this listener. Checking containment instead is strictly more correct than the
 * old stopPropagation did: it also covers clicks inside the drop-up panel itself, which
 * previously worked only because every panel button already set `dropdownOpen` to
 * `false` itself.
 */
function onOutsideDropdownClick(event: MouseEvent) {
  if (!dropdownOpen.value) return
  const target = event.target as Node | null
  if (target && document.querySelector('[data-dialog-action-id="reject-menu"]')?.contains(target)) return
  if (target && dropdownPanelRef.value?.contains(target)) return
  dropdownOpen.value = false
}

onMounted(() => {
  window.addEventListener('click', onOutsideDropdownClick)
})

onBeforeUnmount(() => {
  window.removeEventListener('click', onOutsideDropdownClick)
})

function notifySaved() {
  saved.value = true
  saving.value = false
  emit('update:visible', false)
}

function notifySaveFailed() {
  saving.value = false
}

defineExpose({ notifySaved, notifySaveFailed })
</script>

<style scoped>
.rrd-body {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.rrd-doc-info {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  background: var(--bg-sub, #f8fafc);
  border-radius: 6px;
  font-size: 0.875rem;
}

.rrd-doc-label {
  color: var(--text-m, #64748b);
  font-size: 0.8125rem;
  flex-shrink: 0;
}

.rrd-doc-name {
  color: var(--text, #1e293b);
  font-weight: 500;
  word-break: break-all;
}

.rrd-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rrd-field-label {
  font-size: 0.8125rem;
  font-weight: 500;
  color: var(--text, #1e293b);
}

.rrd-textarea {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  font-size: 0.875rem;
  color: var(--text, #1e293b);
  background: var(--bg-input, #fff);
  resize: vertical;
  min-height: 180px;
  box-sizing: border-box;
  transition: border-color 0.15s;
  font-family: inherit;
  line-height: 1.5;
}
.rrd-textarea:focus {
  outline: none;
  border-color: var(--primary, #2563eb);
}
.rrd-textarea:disabled {
  background: var(--bg-sub, #f8fafc);
  color: var(--text-m, #64748b);
  cursor: not-allowed;
}

/* The drop-up panel is `reject-menu`'s `popover-*` slot content: DialogFooter renders it
   as a sibling of that action's `<button>` inside `.fg-dialog-footer__action`, which is
   `position: relative`, so this only has to anchor itself inside that box (T0016 §2.2
   rework — no more component-local wrapper class needed to establish the anchor). */
.rrd-dropdown {
  position: absolute;
  bottom: calc(100% + 4px);   /* drop-up */
  top: auto;
  left: 0;
  min-width: 140px;
  background: var(--bg-card, #fff);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
  z-index: 200;
  overflow: hidden;
}

.rrd-dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 8px 12px;
  background: none;
  border: none;
  font-size: 0.8125rem;
  color: var(--text, #1e293b);
  cursor: pointer;
  text-align: left;
  transition: background 0.1s;
}
.rrd-dropdown-item:hover:not(:disabled) {
  background: var(--bg-hover, #f1f5f9);
}
.rrd-dropdown-item:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
</style>
