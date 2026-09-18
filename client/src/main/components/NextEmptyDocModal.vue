<template>
  <DialogShell :open="visible" variant="confirm" surface-class="dialog-next-empty-doc-modal" :busy="submitting" :closeable="!submitting" @request-close="close">
    <template #header>
      <DialogHeader title="" :closeable="!(submitting)" @close="close">
        <template #title>
            <AppIcon name="file-text" style="color:var(--primary);" />
            {{ t('main.next_empty_doc_modal.title') }}
          </template>
      </DialogHeader>
    </template>

        

        <div class="dialog-feature-body">
          <form class="next-empty-form" @submit.prevent="submit">
            <div class="doc-info-grid">
              <div>
                <label>{{ t('main.next_empty_doc_modal.label_type') }}</label>
                <span>{{ typeLabel }} ({{ docType || '-' }})</span>
              </div>
              <div>
                <label>{{ t('main.next_empty_doc_modal.label_group') }}</label>
                <span>{{ groupId || '-' }}</span>
              </div>
              <div>
                <label>{{ t('main.next_empty_doc_modal.label_target') }}</label>
                <span>{{ prevDocId || '-' }}</span>
              </div>
              <div>
                <label>{{ t('main.next_empty_doc_modal.label_module') }}</label>
                <span>{{ moduleTitle || moduleName || 'none' }}</span>
              </div>
            </div>

            <div class="form-group">
              <label class="form-label req">{{ t('main.next_empty_doc_modal.label_title') }}</label>
              <div class="title-input-row">
                <input
                  v-model="title"
                  class="form-ctrl"
                  type="text"
                  maxlength="100"
                  :placeholder="t('main.next_empty_doc_modal.placeholder_title')"
                  autofocus
                />
                <button
                  class="title-fill-btn"
                  type="button"
                  :aria-label="t('main.next_empty_doc_modal.fill_document_type_title_tooltip')"
                  :title="t('main.next_empty_doc_modal.fill_document_type_title_tooltip')"
                  @click="applyDocumentTypeTitle"
                >
                  <AppIcon name="magic-wand" />
                </button>
              </div>
            </div>

            <label class="open-after-row">
              <input v-model="openAfter" type="checkbox" />
              <span>{{ t('main.next_empty_doc_modal.open_after') }}</span>
            </label>
          </form>
        </div>

        
      
<div
            v-if="flashMessage"
            :class="['alert', flashOk ? 'alert-success' : 'alert-danger']"
            style="width:100%; margin-bottom:12px;"
          >
            <AppIcon :name="flashOk ? 'check' : 'warning'" />
            <span>{{ flashMessage }}</span>
          </div>
    <template #footer>
      <DialogFooter :actions="[
        { id: 'close-0', role: 'cancel', label: t('common.cancel'), onSelect: () => close(), disabled: submitting },
        { id: 'submit-1', role: 'primary', label: t('main.next_empty_doc_modal.create'), onSelect: () => submit(), disabled: submitting }
      ]">
        <template #action-close-0>
            {{ t('common.cancel') }}
          </template>
        <template #action-submit-1>
            <span v-if="submitting">
              <AppIcon name="spinner" spin /> {{ t('main.next_empty_doc_modal.creating') }}
            </span>
            <span v-else>
              <AppIcon name="file-text" /> {{ t('main.next_empty_doc_modal.create') }}
            </span>
          </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useDocTypeStore } from '../stores/docTypeStore'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  visible: boolean
  projectId: string
  groupId: string
  prevDocId: string
  docType: string
  moduleName?: string | null
  moduleTitle?: string | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  created: [payload: { docId: string; openAfter: boolean; projectId: string }]
}>()

const { t } = useI18n()
const docTypeStore = useDocTypeStore()

const title = ref('')
const openAfter = ref(true)
const submitting = ref(false)
const flashMessage = ref('')
const flashOk = ref(false)

const typeLabel = computed(() => {
  if (!props.docType) return t('main.next_empty_doc_modal.fallback_doc')
  const key = `doc.type.${props.docType}`
  const localized = t(key)
  return localized === key ? docTypeStore.getLabel(props.docType) : localized
})

watch(
  () => props.visible,
  (visible) => {
    if (visible) {
      title.value = ''
      openAfter.value = true
      flashMessage.value = ''
      flashOk.value = false
    }
  },
)

function close() {
  if (submitting.value) return
  emit('update:visible', false)
}

// group 0369 rejection rework: fill with the current locale's label for the document
// type being created. Repeated clicks remain idempotent and do not change the locale.
function applyDocumentTypeTitle() {
  title.value = typeLabel.value
}

async function submit() {
  flashMessage.value = ''
  const normalizedTitle = title.value.trim()
  if (!normalizedTitle) {
    flashOk.value = false
    flashMessage.value = t('main.next_empty_doc_modal.error_title_required')
    return
  }
  if (!props.projectId || !props.groupId || !props.prevDocId || !props.docType) {
    flashOk.value = false
    flashMessage.value = t('main.next_empty_doc_modal.error_next_step_unavailable')
    return
  }

  submitting.value = true
  try {
    const res = await postRequest<any>('/api/v1/documents/next-empty', {
      project_id: props.projectId,
      group_id: props.groupId,
      prev_doc_id: props.prevDocId,
      type_code: props.docType,
      title: normalizedTitle,
      module: props.moduleName || 'none',
    })
    const docId: string = (res.data as any)?.doc_id ?? ''
    flashOk.value = true
    flashMessage.value = t('main.next_empty_doc_modal.toast_created')
    emit('created', { docId, openAfter: openAfter.value, projectId: props.projectId })
    emit('update:visible', false)
  } catch (e: any) {
    flashOk.value = false
    const detail = e?.response?.data?.detail
    flashMessage.value = Array.isArray(detail)
      ? detail.map((d: any) => d.msg ?? d).join(', ')
      : (detail ?? t('main.next_empty_doc_modal.error_create_failed'))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.next-empty-form {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.doc-info-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--bg);
}

.doc-info-grid div {
  min-width: 0;
}

.doc-info-grid label {
  display: block;
  margin-bottom: 2px;
  color: var(--text-m);
  font-size: .7rem;
}

.doc-info-grid span {
  display: block;
  overflow: hidden;
  color: var(--text);
  font-size: .78rem;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.open-after-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-s);
  font-size: .8rem;
}

/* Title input + document-type fill button (group 0369 rejection rework) — reuses the
   .title-input-row / .title-fill-btn shape from NewRequirementModal.vue. */
.title-input-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.title-input-row .form-ctrl {
  flex: 1 1 auto;
  min-width: 0;
}

.title-fill-btn {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border: 1px solid var(--border);
  border-radius: var(--r);
  background: var(--surface);
  color: var(--text-m);
  font-size: 0.9rem;
  cursor: pointer;
  transition: color 0.15s, border-color 0.15s, background 0.15s;
}

.title-fill-btn:hover {
  color: var(--primary);
  border-color: #bfdbfe;
  background: var(--surface-h);
}

:global(.fg-dialog-surface.dialog-next-empty-doc-modal) { max-width:460px; }
</style>
