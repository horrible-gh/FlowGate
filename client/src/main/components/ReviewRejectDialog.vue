<template>
  <teleport to="body">
    <div
      v-if="visible"
      ref="overlayRef"
      class="modal-bg"
      tabindex="-1"
      @keydown.escape.prevent="onClose"
    >
      <div class="modal-box modal-rrd" role="dialog" aria-modal="true" aria-labelledby="rrd-title">

        <!-- Header -->
        <div class="modal-hd">
          <div class="modal-title" id="rrd-title">
            <AppIcon name="x-circle" style="color:var(--danger); margin-right:6px;" />{{ t('main.review_reject_dialog.title') }}
          </div>
          <button type="button" class="modal-close" @click="onClose">
            <AppIcon name="x" />
          </button>
        </div>

        <!-- Body -->
        <div class="modal-bd rrd-body">
          <div class="rrd-doc-info">
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

        <!-- Footer -->
        <div class="modal-ft rrd-footer">
          <!-- Save message button -->
          <button
            type="button"
            class="btn btn-danger btn-sm"
            :disabled="saved || saving || !reason.trim()"
            @click="onSaveReason"
          >
            <template v-if="saving">
              <AppIcon name="spinner" spin /> {{ t('main.review_reject_dialog.saving') }}
            </template>
            <template v-else-if="saved">
              <AppIcon name="check" /> {{ t('main.review_reject_dialog.saved') }}
            </template>
            <template v-else>
              <AppIcon name="floppy-disk" /> {{ t('main.review_reject_dialog.save_message') }}
            </template>
          </button>

          <!-- Reject ▼ dropdown — 0419 T0006: edit mode only corrects existing
               wording, it doesn't re-copy a mention or re-invoke AI (that stays
               scoped to the original reject action; NR0003 §risk 5). -->
          <div v-if="!editMode" class="rrd-split-wrap">
            <button
              type="button"
              class="btn btn-secondary btn-sm rrd-split-caret"
              @click.stop="toggleDropdown"
            >
              {{ t('main.review_reject_dialog.reject') }} <AppIcon name="caret-down" />
            </button>
            <div v-if="dropdownOpen" class="rrd-dropdown">
              <button
                type="button"
                class="rrd-dropdown-item"
                @click="onCopyMention"
              >
                <AppIcon name="copy" /> {{ t('main.review_reject_dialog.copy_mention') }}
              </button>
              <!-- Group 0223: in-app invoke beside every copy-mention (병행, not either/or). -->
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
          </div>

          <button type="button" class="btn btn-outline btn-sm rrd-close-btn" @click="onClose">
            {{ t('common.close') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDocTypeStore } from '../stores/docTypeStore'

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
const overlayRef = ref<HTMLElement | null>(null)
const textareaRef = ref<HTMLTextAreaElement | null>(null)
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

watch(
  () => props.visible,
  (v) => {
    if (v) {
      reason.value = props.existingReason ?? ''
      saved.value = false
      saving.value = false
      dropdownOpen.value = false
      nextTick(() => {
        overlayRef.value?.focus()
        if (!reason.value) textareaRef.value?.focus()
      })
    }
  },
)

watch(
  () => props.existingReason,
  (v) => {
    if (!saved.value) {
      reason.value = v ?? ''
    }
  },
)

function onClose() {
  dropdownOpen.value = false
  emit('update:visible', false)
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

function onOutsideDropdownClick() {
  if (dropdownOpen.value) dropdownOpen.value = false
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
.modal-bg {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1200;
}

.modal-box {
  background: var(--bg-card, #fff);
  border-radius: 10px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
  overflow: hidden;
}

.modal-rrd {
  /* 0419 T0006 (NR0003 후속 T 권고 1 / TR0005 rev2 시안): 480px 고정폭은 장문
     반려 사유를 담기엔 너무 좁았다. .modal-box 자체는 이미 고정 height 없이
     내용 기준이므로, 여기서는 폭을 넓히고 뷰포트 상한만 함께 걸어 둔다 —
     WorkflowDecisionModal.vue의 height:85vh 고정 관용구는 반려 사유 길이와
     무관하게 항상 최대치로 보여 "태평양" 반려를 낳았으므로 재사용하지 않았다. */
  width: 620px;
  max-width: 96vw;
  max-height: 78vh;
}

.modal-hd {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border, #e2e8f0);
}

.modal-title {
  font-size: 1rem;
  font-weight: 600;
  color: var(--text, #1e293b);
}

.modal-close {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1rem;
  color: var(--text-m, #64748b);
  padding: 4px 6px;
  border-radius: 4px;
  transition: background 0.1s;
}
.modal-close:hover {
  background: var(--bg-hover, #f1f5f9);
}

.modal-bd {
  padding: 20px;
  overflow-y: auto;
}

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

.modal-ft {
  padding: 14px 20px;
  border-top: 1px solid var(--border, #e2e8f0);
  display: flex;
  align-items: center;
  gap: 8px;
}

.rrd-footer {
  justify-content: flex-start;
}

.rrd-close-btn {
  margin-left: auto;
}

.rrd-split-wrap {
  position: relative;
  display: inline-flex;
}

.rrd-split-caret {
  border-radius: 6px;
  padding-inline: 10px;
}

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
