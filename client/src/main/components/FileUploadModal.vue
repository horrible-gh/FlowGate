<template>
  <teleport to="body">
    <div v-if="visible" class="modal-bg">
      <div class="modal-box">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="upload-simple" style="color:var(--primary);" /> {{ t('main.file_upload_modal.title') }}
          </span>
          <button class="modal-close" @click="close">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
          <div
            class="upload-drop-zone"
            :class="{ dragging }"
            @dragover.prevent="dragging = true"
            @dragleave.prevent="dragging = false"
            @drop.prevent="onDrop"
            @click="triggerFileInput"
          >
            <AppIcon name="cloud-arrow-up" class="drop-icon" />
            <p>{{ t('main.file_upload_modal.drop_zone') }}</p>
            <p class="upload-hint">{{ t('main.file_upload_modal.max_size') }}</p>
            <span v-if="selectedFile" class="upload-selected">{{ selectedFile.name }}</span>
          </div>
          <input ref="fileInput" type="file" style="display:none" @change="onFileChange" />

          <div class="form-group" style="margin-top:12px;">
            <label class="form-label">{{ t('main.file_upload_modal.map_to_document') }}</label>
            <select class="form-ctrl" v-model="targetDocId" :disabled="loadingDocs || uploading">
              <option v-for="d in docList" :key="d.doc_id" :value="d.doc_id">
                {{ d.doc_id }} — {{ d.title }}
              </option>
            </select>
          </div>

          <div v-if="errorMsg" class="upload-error">{{ errorMsg }}</div>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="close">{{ t('common.cancel') }}</button>
          <button class="btn btn-primary" :disabled="!selectedFile || !targetDocId || uploading" @click="doUpload">
            <AppIcon v-if="uploading" name="spinner" spin />
            <AppIcon v-else name="upload-simple" />
            {{ uploading ? t('main.file_upload_modal.uploading') : t('main.file_upload_modal.upload') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postFormRequest } from '@shared/api'
import { useToast } from './common/useToast'
import type { Tab } from '../stores/tabs'

interface DocumentItem {
  doc_id: string
  title: string
  group_id?: string | null
  project_id?: string | null
}

const props = defineProps<{
  tab: Tab
  visible: boolean
}>()

const emit = defineEmits<{
  'update:visible': [value: boolean]
  uploaded: [result: any]
}>()

const fileInput = ref<HTMLInputElement | null>(null)
const dragging = ref(false)
const uploading = ref(false)
const loadingDocs = ref(false)
const selectedFile = ref<File | null>(null)
const errorMsg = ref('')
const targetDocId = ref('')
const docList = ref<DocumentItem[]>([])
const { showToast } = useToast()
const { t } = useI18n()

function close() {
  emit('update:visible', false)
}

function resetState() {
  dragging.value = false
  uploading.value = false
  errorMsg.value = ''
  selectedFile.value = null
  if (fileInput.value) {
    fileInput.value.value = ''
  }
}

function triggerFileInput() {
  if (!uploading.value) {
    fileInput.value?.click()
  }
}

function setSelectedFile(file: File | null) {
  errorMsg.value = ''
  if (!file) {
    selectedFile.value = null
    return
  }
  // flowgate.default.0060 L0012 1-1: 시안 안내문이 '20MB' 라고 쓴 값을 상한으로 확정했고,
  // 서버의 attach_max_upload_bytes 도 같은 20 MiB 다. 화면이 10MB 로 먼저 막으면 서버가
  // 받아 주는 파일을 화면이 거절하는, 두 숫자가 어긋난 상태가 된다.
  if (file.size > 20 * 1024 * 1024) {
    selectedFile.value = null
    errorMsg.value = t('main.file_upload_modal.file_too_large')
    return
  }
  selectedFile.value = file
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  setSelectedFile(input.files?.[0] ?? null)
}

function onDrop(event: DragEvent) {
  dragging.value = false
  setSelectedFile(event.dataTransfer?.files?.[0] ?? null)
}

async function loadDocuments() {
  loadingDocs.value = true
  errorMsg.value = ''
  try {
    const currentRes = await getRequest<DocumentItem>(`/api/v1/documents/detail?doc_id=${encodeURIComponent(props.tab.id)}`)
    const currentDoc = ((currentRes.data as any)?.data ?? currentRes.data) as DocumentItem
    const projectId = (props.tab as Tab & { projectId?: string | null }).projectId ?? currentDoc?.project_id
    const groupId = currentDoc?.group_id

    if (!projectId || !groupId) {
      docList.value = currentDoc ? [currentDoc] : []
      targetDocId.value = currentDoc?.doc_id ?? props.tab.id
      return
    }

    const listRes = await getRequest<DocumentItem[] | { data?: DocumentItem[] }>(
      '/api/v1/documents',
      { project_id: projectId, group_id: groupId },
    )
    const rawList: DocumentItem[] = Array.isArray(listRes.data)
      ? listRes.data
      : Array.isArray((listRes.data as any)?.data)
        ? ((listRes.data as any).data as DocumentItem[])
        : []
    docList.value = rawList.length ? rawList : [currentDoc]
    targetDocId.value = rawList.some((d) => d.doc_id === currentDoc?.doc_id)
      ? currentDoc.doc_id
      : rawList[0]?.doc_id ?? currentDoc?.doc_id ?? props.tab.id
  } catch (e: any) {
    docList.value = []
    targetDocId.value = props.tab.id
    errorMsg.value = e?.response?.data?.detail ?? t('main.file_upload_modal.load_documents_failed')
  } finally {
    loadingDocs.value = false
  }
}

async function doUpload() {
  if (!selectedFile.value || !targetDocId.value || uploading.value) return

  uploading.value = true
  errorMsg.value = ''

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('doc_id', targetDocId.value)
    const res = await postFormRequest<any>(
      `/api/v1/documents/attachments`,
      formData,
    )
    const result = (res.data as any)?.data ?? res.data
    showToast(t('main.file_upload_modal.uploaded'), 'success')
    emit('uploaded', result)
    emit('update:visible', false)
    resetState()
  } catch (e: any) {
    // 0060 TR0017 rev2 — 서버가 저장에 실패한 5xx는 사용자가 고칠 수 있는 입력 오류와
    // 구분해서 알린다. 서버는 같은 실패를 트레이스백과 함께 로그에 남긴다.
    const status = Number(e?.response?.status ?? 0)
    const code = e?.response?.data?.error?.code ?? ''
    const serverSide =
      status >= 500 ||
      code === 'ATTACHMENT_STORE_FAILED' ||
      code === 'ATTACHMENT_METADATA_FAILED' ||
      code === 'ATTACHMENT_OPERATION_FAILED'
    errorMsg.value = serverSide
      ? t('main.file_upload_modal.upload_failed_server')
      : e?.response?.data?.detail ?? t('main.file_upload_modal.upload_failed')
  } finally {
    uploading.value = false
  }
}

watch(
  () => [props.visible, props.tab.id] as const,
  async ([visible]) => {
    if (visible) {
      resetState()
      await loadDocuments()
    }
  },
  { immediate: true },
)
</script>

<style scoped>
.upload-drop-zone {
  border: 2px dashed var(--border);
  border-radius: var(--r-lg);
  padding: 32px;
  text-align: center;
  cursor: pointer;
  transition: all var(--tr);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
}
.upload-drop-zone:hover,
.upload-drop-zone.dragging {
  border-color: var(--primary);
  background: rgba(37, 99, 235, 0.04);
}
.upload-drop-zone .drop-icon {
  font-size: 2rem;
  color: var(--text-m);
}
.upload-drop-zone p { font-weight: 500; margin: 0; }
.upload-hint { font-size: .75rem; color: var(--text-m); }
.upload-selected { font-size: .8rem; color: var(--primary); font-weight: 600; }
.upload-error {
  color: #dc2626;
  font-size: .8rem;
  margin-top: 8px;
}
</style>
