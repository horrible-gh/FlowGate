<template>
  <teleport to="body">
    <div class="modal-bg" role="dialog" aria-modal="true">
      <div class="modal-box">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="plus" style="color:var(--primary);" /> {{ t('main.new_related_doc_modal.title') }}
          </span>
          <button class="modal-close" type="button" @click="$emit('close')">
            <AppIcon name="x" />
          </button>
        </div>

        <div class="modal-bd">
          <form @submit.prevent="submit">
            <!-- Document type -->
            <div class="form-group">
              <label class="form-label req">{{ t('main.new_related_doc_modal.document_type') }}</label>
              <select v-model="form.typeCode" class="form-ctrl">
                <option v-for="t in docTypes" :key="t.code" :value="t.code">
                  {{ t.label }} ({{ t.code }})
                </option>
              </select>
            </div>

            <!-- Title -->
            <div class="form-group">
              <label class="form-label req">{{ t('main.new_related_doc_modal.document_title') }}</label>
              <div class="title-input-row">
                <input
                  v-model="form.title"
                  class="form-ctrl"
                  type="text"
                  maxlength="100"
                  :placeholder="t('main.new_related_doc_modal.title_placeholder')"
                  autofocus
                />
                <button
                  class="title-fill-btn"
                  type="button"
                  :aria-label="t('main.new_related_doc_modal.fill_document_type_title_tooltip')"
                  :title="t('main.new_related_doc_modal.fill_document_type_title_tooltip')"
                  @click="applyDocumentTypeTitle"
                >
                  <AppIcon name="magic-wand" />
                </button>
              </div>
            </div>

            <!-- Related group -->
            <div class="form-group">
              <label class="form-label">{{ t('main.new_related_doc_modal.related_group') }}</label>
              <select v-model="form.groupId" class="form-ctrl">
                <option v-for="group in groupOptions" :key="group.id" :value="group.id">
                  {{ group.label }}
                </option>
              </select>
            </div>

            <!-- Template -->
            <div class="form-group">
              <label class="form-label">{{ t('main.new_related_doc_modal.template') }}</label>
              <select v-model="form.template" class="form-ctrl">
                <option value="default">{{ t('main.new_related_doc_modal.default_template') }}</option>
                <option value="none">{{ t('main.new_related_doc_modal.empty_template') }}</option>
              </select>
            </div>

            <!-- Open after creation -->
            <div style="margin-top:16px; padding:10px 14px; background:var(--bg); border-radius:var(--r); display:flex; align-items:center; gap:10px;">
              <label class="toggle" style="flex-shrink:0;">
                <input v-model="form.openAfter" type="checkbox" />
                <span class="toggle-track"></span>
              </label>
              <span style="font-size:.8rem; color:var(--text-s);">{{ t('main.new_related_doc_modal.open_after') }}</span>
            </div>
          </form>
        </div>

        <div class="modal-ft">
          <div
            v-if="flashMessage"
            :class="['alert', flashOk ? 'alert-success' : 'alert-danger']"
            style="width:100%; margin-bottom:12px;"
          >
            <AppIcon :name="flashOk ? 'check' : 'warning'" />
            <span>{{ flashMessage }}</span>
          </div>
          <button class="btn btn-secondary" type="button" @click="$emit('close')">{{ t('common.cancel') }}</button>
          <button class="btn btn-primary" type="button" :disabled="submitting" @click="submit">
            <span v-if="submitting">
              <AppIcon name="spinner" spin /> {{ t('main.new_related_doc_modal.creating') }}
            </span>
            <span v-else>
              <AppIcon name="plus" /> {{ t('main.new_related_doc_modal.create') }}
            </span>
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useProjectStore } from '../stores/project'
import { useExplorerStore } from '../stores/explorer'
import type { Tab } from '../stores/tabs'

const props = defineProps<{ tab: Tab }>()

const emit = defineEmits<{
  close: []
  created: [payload: { docId: string; openAfter: boolean; projectId: string }]
}>()

const { t } = useI18n()
const projectStore = useProjectStore()
const explorerStore = useExplorerStore()

interface DocDetail {
  doc_id: string
  group_id?: string | null
  project_id?: string | null
  module?: string | null
}

// group 0022 §3.6: Q/A/V retired as document types — queries are document-bound data
// (the [Q&A] panel), not Q documents, so Q is removed from the create dialog.
const DOC_TYPE_CODES = ['DS', 'N', 'T', 'TS', 'M'] as const
const docTypes = computed(() => DOC_TYPE_CODES.map((code) => ({
  code,
  label: t(`doc.type.${code}`),
})))

const form = ref({
  typeCode: 'DS',
  title: '',
  groupId: '',
  template: 'default',
  openAfter: true,
})

const submitting = ref(false)
const flashMessage = ref('')
const flashOk = ref(false)
const currentDoc = ref<DocDetail | null>(null)

const groupOptions = computed(() => {
  const pid = projectStore.currentProjectId
  // 0454 T0006 §4.2 — full variant: this dropdown's meaning ("every group of the project")
  // must not narrow because the sidebar happens to be hiding completed ones. Paired with the
  // full fetch in onMounted below, so the read hits a cache the same variant filled.
  const nodes = pid ? explorerStore.getCachedGroupTree(pid, true) ?? [] : []
  const groups = nodes.filter((n) => n.node_type === 'group')
  return groups.map((g) => ({
    id: g.id,
    label: g.number ? `${g.number}: ${g.label}` : g.label,
  }))
})

// group 0369 rejection rework: use the current locale's label for the document type
// selected in this dialog. Repeated clicks stay idempotent and do not change locale.
function applyDocumentTypeTitle() {
  const selectedType = docTypes.value.find((item) => item.code === form.value.typeCode)
  form.value.title = selectedType?.label ?? form.value.typeCode
}

onMounted(async () => {
  // Load current document info
  try {
    const res = await getRequest<any>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(props.tab.id)}`)
    currentDoc.value = (res.data as any)?.data ?? res.data
  } catch {
    currentDoc.value = null
  }

  // Load group tree (for selector)
  const pid = projectStore.currentProjectId ?? currentDoc.value?.project_id ?? ''
  if (pid) {
    try {
      await explorerStore.fetchGroupTree(pid, false, true)
    } catch {
      /* Ignore on group list load failure */
    }
  }

  // Set current document's group as default
  if (currentDoc.value?.group_id) {
    form.value.groupId = currentDoc.value.group_id
  } else if (groupOptions.value.length > 0) {
    form.value.groupId = groupOptions.value[0].id
  }
})

async function submit() {
  flashMessage.value = ''

  const title = form.value.title.trim()
  if (!title) {
    flashOk.value = false
    flashMessage.value = t('main.new_related_doc_modal.error_title_required')
    return
  }
  if (!form.value.groupId) {
    flashOk.value = false
    flashMessage.value = t('main.new_related_doc_modal.error_group_required')
    return
  }

  const projectId = projectStore.currentProjectId ?? currentDoc.value?.project_id ?? ''
  if (!projectId) {
    flashOk.value = false
    flashMessage.value = t('main.new_related_doc_modal.error_project_unavailable')
    return
  }

  submitting.value = true
  try {
    const res = await postRequest<any>('/api/v1/documents/related', {
      project_id: projectId,
      type_code: form.value.typeCode,
      title,
      group_id: form.value.groupId,
      target_id: props.tab.id,
      template: form.value.template,
      module: currentDoc.value?.module ?? 'none',
    })

    const docId: string = (res.data as any)?.doc_id ?? ''
    flashOk.value = true
    flashMessage.value = t('main.new_related_doc_modal.created')

    emit('created', { docId, openAfter: form.value.openAfter, projectId })
  } catch (e: any) {
    flashOk.value = false
    const detail = e?.response?.data?.detail
    flashMessage.value = Array.isArray(detail)
      ? detail.map((d: any) => d.msg ?? d).join(', ')
      : (detail ?? t('main.new_related_doc_modal.create_failed'))
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
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
</style>
