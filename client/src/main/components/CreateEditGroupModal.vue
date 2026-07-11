<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box" style="max-width:480px;">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon :name="dialogIcon" />
            {{ modalTitle }}
          </span>
          <button class="modal-close" type="button" @click="onCancel">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <form @submit.prevent="submit">
            <div v-if="!(isModuleDialog && mode === 'create')" class="form-group">
              <label class="form-label req">{{ titleLabel }}</label>
              <input
                v-model="form.title"
                class="form-ctrl"
                type="text"
                maxlength="100"
                :placeholder="titlePlaceholder"
                required
              />
            </div>
            <template v-if="isModuleDialog && mode === 'create'">
              <div class="form-group">
                <label class="form-label req">{{ t('modules.label_display_name') }}</label>
                <input
                  v-model="newModuleDisplayName"
                  class="form-ctrl"
                  type="text"
                  maxlength="100"
                  required
                />
              </div>
              <div class="form-group">
                <label class="form-label req">{{ t('modules.label_id') }}</label>
                <div style="display:flex;gap:8px;">
                  <input
                    v-model="newModuleSlug"
                    class="form-ctrl"
                    :class="{ 'input-slug-error': newModuleSlug && !moduleSlugValid }"
                    type="text"
                    maxlength="100"
                    @input="onModuleSlugInput"
                  />
                  <button class="btn btn-secondary" type="button" @click="suggestModuleSlug">
                    {{ t('modules.button_suggest_id') }}
                  </button>
                </div>
                <p v-if="newModuleSlug && !moduleSlugValid" class="form-slug-error-text">
                  {{ t('modules.error_id_format') }}
                </p>
              </div>
            </template>
            <template v-if="dialogMode !== 'module' && dialogMode !== 'group'">
            <div class="form-group">
              <label class="form-label">{{ t('main.create_edit_group_modal.label_module') }}</label>
              <input
                v-model="form.module"
                class="form-ctrl"
                type="text"
                :placeholder="t('main.create_edit_group_modal.placeholder_module')"
              />
            </div>
            <div class="form-group" style="margin-bottom:0;">
              <label class="form-label">{{ t('main.create_edit_group_modal.label_priority') }}</label>
              <input
                v-model="form.priority"
                class="form-ctrl"
                type="text"
                :placeholder="t('main.create_edit_group_modal.placeholder_priority')"
              />
            </div>
            </template>
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
          <button class="btn btn-primary" type="button" :disabled="submitting || (isModuleDialog && mode === 'create' && !moduleSlugValid)" @click="submit">
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
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest, putRequest, patchRequest } from '@shared/api'
import { useToast } from './common/useToast'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{
  visible: boolean
  mode: 'create' | 'edit'
  dialogMode?: 'module' | 'group'
  projectId: string
  parentId?: string | null
  moduleName?: string | null
  group?: {
    group_id: string
    title: string
    module: string
    priority: string | null
  }
  editModule?: {
    module_id: string
    name: string
    title: string
  }
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  saved: [groupId: string]
}>()

const { t } = useI18n()
const { showToast } = useToast()
const submitting = ref(false)
const errorMessage = ref('')

const form = ref({
  title: '',
  module: 'none',
  priority: '',
})

const newModuleDisplayName = ref('')
const newModuleSlug = ref('')
const moduleSlugManuallyEdited = ref(false)
let _moduleSlugDebounceTimer: ReturnType<typeof setTimeout> | null = null
let _moduleAutoFilling = false

const moduleSlugValid = computed(
  () => /^[a-z0-9_\-]+$/.test(newModuleSlug.value) && newModuleSlug.value.trim() !== '',
)

const isModuleDialog = computed(() => props.dialogMode === 'module')
const isGroupDialog = computed(() => props.dialogMode === 'group')

const dialogIcon = computed(() =>
  isModuleDialog.value ? 'stack' : 'folder-simple-plus',
)

const modalTitle = computed(() => {
  if (props.mode === 'create') {
    if (isModuleDialog.value) return t('main.create_edit_group_modal.title_create_module')
    if (isGroupDialog.value) return t('main.create_edit_group_modal.title_create_group')
    return t('main.create_edit_group_modal.title_create')
  }
  if (isModuleDialog.value) return t('main.create_edit_group_modal.title_edit_module')
  if (isGroupDialog.value) return t('main.create_edit_group_modal.title_edit_group')
  return t('main.create_edit_group_modal.title_edit')
})

const titleLabel = computed(() => {
  if (isModuleDialog.value) return t('main.create_edit_group_modal.label_title_module')
  if (isGroupDialog.value) return t('main.create_edit_group_modal.label_title_group')
  return t('main.create_edit_group_modal.label_title')
})

const titlePlaceholder = computed(() => {
  if (isGroupDialog.value) return t('main.create_edit_group_modal.placeholder_title_group')
  return t('main.create_edit_group_modal.placeholder_title')
})

watch(() => props.visible, async (val) => {
  if (!val) return
  errorMessage.value = ''
  submitting.value = false
  if (props.mode === 'edit' && isModuleDialog.value && props.editModule) {
    form.value.title = props.editModule.title
  } else if (props.mode === 'edit' && props.group) {
    form.value.title = props.group.title
    form.value.module = props.group.module || 'none'
    form.value.priority = props.group.priority ?? ''
    await fetchGroupData(props.group.group_id)
  } else if (props.mode === 'edit' && !props.group) {
    form.value.title = ''
    form.value.module = 'none'
    form.value.priority = ''
  } else {
    form.value.title = ''
    form.value.module = 'none'
    form.value.priority = ''
    newModuleDisplayName.value = ''
    newModuleSlug.value = ''
    moduleSlugManuallyEdited.value = false
    if (_moduleSlugDebounceTimer) {
      clearTimeout(_moduleSlugDebounceTimer)
      _moduleSlugDebounceTimer = null
    }
  }
}, { immediate: true })

async function fetchGroupData(groupId: string) {
  try {
    const res = await getRequest<any>(`/api/v1/groups/${encodeURIComponent(groupId)}`)
    const g = (res.data as any)?.group ?? res.data
    if (g) {
      form.value.title = g.title ?? ''
      form.value.module = g.module || 'none'
      form.value.priority = g.priority ?? ''
    }
  } catch {
    // keep current values
  }
}

watch(() => [props.visible, props.mode, props.group] as const, async ([vis, mode, group]) => {
  if (!vis) return
  if (mode === 'edit') {
    if (group) {
      form.value.title = group.title
      form.value.module = group.module || 'none'
      form.value.priority = group.priority ?? ''
      await fetchGroupData(group.group_id)
    } else {
      // no group prop — nothing to prefill
    }
  }
}, { immediate: true })

watch(newModuleDisplayName, (val) => {
  if (moduleSlugManuallyEdited.value) return
  if (_moduleSlugDebounceTimer) clearTimeout(_moduleSlugDebounceTimer)
  const text = (val || '').trim()
  if (!text) {
    _moduleAutoFilling = true
    newModuleSlug.value = ''
    _moduleAutoFilling = false
    return
  }
  _moduleSlugDebounceTimer = setTimeout(async () => {
    if (moduleSlugManuallyEdited.value) return
    try {
      const res = await postRequest<any>('/api/v1/slug/romanize', { text })
      const suggested = (res.data as any)?.suggested
      if (suggested && !moduleSlugManuallyEdited.value) {
        _moduleAutoFilling = true
        newModuleSlug.value = suggested
        _moduleAutoFilling = false
      }
    } catch { /* silently ignore auto-fill failure */ }
  }, 400)
})

function onModuleSlugInput() {
  if (_moduleAutoFilling) return
  moduleSlugManuallyEdited.value = true
}

async function suggestModuleSlug() {
  const text = newModuleDisplayName.value.trim()
  if (!text) return
  moduleSlugManuallyEdited.value = false
  try {
    const res = await postRequest<any>('/api/v1/slug/romanize', { text })
    const suggested = (res.data as any)?.suggested
    if (suggested) {
      _moduleAutoFilling = true
      newModuleSlug.value = suggested
      _moduleAutoFilling = false
    } else {
      showToast(t('modules.toast_romanize_empty'), 'warning')
    }
  } catch { showToast(t('modules.toast_romanize_empty'), 'warning') }
}

async function submit() {
  let title: string
  if (props.mode === 'create' && props.dialogMode === 'module') {
    title = newModuleDisplayName.value.trim()
    if (!title) {
      errorMessage.value = t('main.create_edit_group_modal.error_title_required')
      return
    }
    if (!moduleSlugValid.value) {
      errorMessage.value = t('modules.error_id_format')
      return
    }
  } else {
    title = form.value.title.trim()
    if (!title) {
      errorMessage.value = t('main.create_edit_group_modal.error_title_required')
      return
    }
  }
  submitting.value = true
  errorMessage.value = ''
  try {
    let groupId: string
    if (props.mode === 'create') {
      if (props.dialogMode === 'module') {
        // Module creation API call (T505)
        const res = await postRequest<any>(
          `/api/v1/projects/${encodeURIComponent(props.projectId)}/modules`,
          {
            name: newModuleSlug.value,
            title: newModuleDisplayName.value.trim(),
          },
        )
        const data = res.data as any
        if (data.ok === false) {
          errorMessage.value = data.error_message || t('main.create_edit_group_modal.error_save_failed')
          return
        }
        groupId = `module:${props.projectId}:${data.name}`
      } else {
        const moduleValue = isGroupDialog.value ? (props.moduleName || 'none') : (form.value.module || 'none')
        const parentId = isGroupDialog.value ? null : (props.parentId ?? null)
        const res = await postRequest<any>('/api/v1/groups', {
          project_id: props.projectId,
          title,
          module: moduleValue,
          parent_id: parentId,
          priority: isGroupDialog.value ? null : (form.value.priority || null),
        })
        const data = res.data as any
        if (data.status === 'error') {
          errorMessage.value = data.message || t('main.create_edit_group_modal.error_save_failed')
          return
        }
        groupId = data.group_id
      }
    } else {
      if (props.dialogMode === 'module' && props.editModule) {
        // Module display name update PATCH (T559)
        const res = await patchRequest<any>(
          `/api/v1/projects/${encodeURIComponent(props.projectId)}/modules/${encodeURIComponent(props.editModule.name)}`,
          { title },
        )
        const data = res.data as any
        if (data.ok === false) {
          errorMessage.value = data.error_message || t('main.create_edit_group_modal.error_save_failed')
          return
        }
        groupId = props.editModule.module_id
      } else {
        if (!props.group?.group_id) {
          errorMessage.value = t('main.create_edit_group_modal.error_save_failed')
          return
        }
        const payload: Record<string, string | null> = { title }
        if (!isModuleDialog.value && !isGroupDialog.value && form.value.module) payload.module = form.value.module
        if (!isModuleDialog.value && !isGroupDialog.value && form.value.priority !== undefined) payload.priority = form.value.priority || null
        const res = await putRequest<any>(`/api/v1/groups/${encodeURIComponent(props.group.group_id)}`, payload)
        const data = res.data as any
        if (data.status === 'error') {
          errorMessage.value = data.message || t('main.create_edit_group_modal.error_save_failed')
          return
        }
        groupId = props.group.group_id
      }
    }
    emit('saved', groupId)
    emit('update:visible', false)
  } catch (e: any) {
    errorMessage.value = e?.response?.data?.message || e?.response?.data?.detail || e?.response?.data?.error_message || t('main.create_edit_group_modal.error_save_failed')
  } finally {
    submitting.value = false
  }
}

function onCancel() {
  emit('update:visible', false)
}
</script>

<style scoped>
.input-slug-error {
  border-color: #ef4444 !important;
  box-shadow: 0 0 0 2px rgba(239, 68, 68, 0.2);
}
.form-slug-error-text {
  font-size: .75rem;
  color: #ef4444;
  margin-top: 4px;
  margin-bottom: 0;
}
</style>
