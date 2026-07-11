<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box" style="max-width:420px;">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon :name="type === 'folder' ? 'folder-simple-plus' : 'file-plus'" />
            {{ type === 'folder' ? t('main.create_file_folder_modal.title_folder') : t('main.create_file_folder_modal.title_file') }}
          </span>
          <button class="modal-close" type="button" @click="onCancel">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
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
        <div class="modal-ft">
          <div v-if="errorMessage" class="alert alert-danger" style="width:100%; margin-bottom:12px;">
            <AppIcon name="warning" />
            <span>{{ errorMessage }}</span>
          </div>
          <button class="btn btn-secondary" type="button" @click="onCancel">
            {{ t('common.cancel') }}
          </button>
          <button class="btn btn-primary" type="button" :disabled="submitting" @click="submit">
            <AppIcon v-if="submitting" name="spinner" spin />
            <AppIcon v-else name="floppy-disk" />
            {{ t('common.save') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import { ref, watch, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  visible: boolean
  type: 'folder' | 'file'
  projectId: string
  parentPath: string
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
    })
    const data = res.data as any
    if (data.status === 'error') {
      errorMessage.value = data.message || t('main.create_file_folder_modal.error_save_failed')
      return
    }
    emit('saved', { name: trimmed, type: props.type })
    emit('update:visible', false)
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.message || t('main.create_file_folder_modal.error_save_failed')
  } finally {
    submitting.value = false
  }
}

function onCancel() {
  emit('update:visible', false)
}
</script>
