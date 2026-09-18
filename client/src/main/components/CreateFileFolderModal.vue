<template>
  <DialogShell :open="visible" variant="form" surface-class="dialog-create-file-folder-modal"  @request-close="onCancel">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="onCancel">
        <template #title>
            <AppIcon :name="type === 'folder' ? 'folder-simple-plus' : 'file-plus'" />
            {{ type === 'folder' ? t('main.create_file_folder_modal.title_folder') : t('main.create_file_folder_modal.title_file') }}
          </template>
      </DialogHeader>
    </template>

        
        <div class="dialog-feature-body">
          <form @submit.prevent="submit">
            <div class="form-group" style="margin-bottom:0;">
              <input
                ref="inputRef"
                v-model="name"
                class="form-ctrl"
                type="text"
                maxlength="200"
                :placeholder="type === 'folder' ? t('main.create_file_folder_modal.placeholder_folder') : t('main.create_file_folder_modal.placeholder_file')"
              />
            </div>
          </form>
        </div>
        
      
<div v-if="errorMessage" class="alert alert-danger" style="width:100%; margin-bottom:12px;">
            <AppIcon name="warning" />
            <span>{{ errorMessage }}</span>
          </div>
    <template #footer>
      <DialogFooter :actions="[
        { id: 'onCancel-0', role: 'cancel', label: t('common.cancel'), onSelect: () => onCancel() },
        { id: 'submit-1', role: 'primary', label: t('common.save'), onSelect: () => submit(), disabled: submitting }
      ]">
        <template #action-onCancel-0>
            {{ t('common.cancel') }}
          </template>
        <template #action-submit-1>
            <AppIcon v-if="submitting" name="spinner" spin />
            <AppIcon v-else name="floppy-disk" />
            {{ t('common.save') }}
          </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { extractApiErrorMessage, postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  visible: boolean
  type: 'folder' | 'file'
  projectId: string
  parentPath: string
  // 0327 T0004 (B0001 / NR0003 recommendation 1): when the explorer is showing a group branch
  // with a live worktree, the new file/folder belongs in THAT worktree. Absent (base
  // checkout) → unchanged behaviour. The server refuses if the worktree is gone.
  groupId?: string | null
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  saved: [payload: { name: string; type: 'file' | 'folder' }]
}>()

const { t } = useI18n()
const name = ref('')
const submitting = ref(false)
const errorMessage = ref('')
const inputRef = ref<HTMLInputElement | null>(null)

watch(() => props.visible, async (val) => {
  if (!val) return
  name.value = ''
  errorMessage.value = ''
  submitting.value = false
  await nextTick()
  inputRef.value?.focus()
})

async function submit() {
  const trimmed = name.value.trim()
  if (!trimmed) {
    errorMessage.value = t('main.create_file_folder_modal.error_name_required')
    return
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    const endpoint = props.type === 'folder'
      ? '/api/v1/storage/folder'
      : '/api/v1/storage/file'
    const res = await postRequest<any>(endpoint, {
      project_id: props.projectId,
      parent_path: props.parentPath,
      name: trimmed,
      ...(props.groupId ? { group_id: props.groupId } : {}),
    })
    const data = res.data as any
    if (data.status === 'error') {
      errorMessage.value = data.message || t('main.create_file_folder_modal.error_save_failed')
      return
    }
    emit('saved', { name: trimmed, type: props.type })
    emit('update:visible', false)
  } catch (e: any) {
    errorMessage.value = extractApiErrorMessage(
      e,
      e?.response?.data?.message || t('main.create_file_folder_modal.error_save_failed'),
    )
  } finally {
    submitting.value = false
  }
}

function onCancel() {
  emit('update:visible', false)
}
</script>

<style scoped>
:global(.fg-dialog-surface.dialog-create-file-folder-modal) { max-width:420px; }
</style>
