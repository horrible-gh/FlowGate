<template>
  <div v-if="tab.type === 'md'" class="card md-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="markdown-logo" style="color:var(--text-m);" />
        {{ t('main.document_preview.title') }}
      </span>
      <div class="card-actions">
        <button
          v-if="downloadAvailable"
          class="btn btn-secondary btn-sm doc-markdown-download"
          type="button"
          :disabled="downloadBusy"
          :title="t('main.doc_info_panel.markdown_download')"
          @click="emit('download-markdown')"
        >
          <AppIcon name="download-simple" /> {{ t('main.doc_info_panel.markdown_download') }}
        </button>
        <button
          v-if="canUploadMarkdown"
          class="btn btn-secondary btn-sm doc-markdown-upload"
          type="button"
          :disabled="uploadBusy"
          :title="t('main.document_preview.upload')"
          @click="triggerUpload"
        >
          <AppIcon name="upload-simple" /> {{ uploadBusy ? t('main.document_preview.uploading') : t('main.document_preview.upload') }}
        </button>
        <input
          v-if="canUploadMarkdown"
          ref="uploadFileInput"
          type="file"
          class="doc-markdown-upload-input"
          accept=".md,text/markdown,text/plain"
          hidden
          @change="onUploadFileSelected"
        />
        <div v-if="!readOnly && canEdit" class="edit-dropdown-wrap">
          <button class="btn btn-outline btn-sm" type="button" @click.stop="emit('toggle-edit-dropdown')">
            <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
            <AppIcon name="caret-down" class="edit-caret" />
          </button>
          <transition name="edit-dropdown">
            <div v-if="editDropdownOpen" class="edit-dropdown-menu" @click.stop>
              <button class="edit-dropdown-item" type="button" @click="emit('edit-direct')">
                <AppIcon name="note-pencil" /> {{ t('main.main_panel.edit_direct') }}
              </button>
              <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="emit('edit-mention')">
                <AppIcon name="copy" /> {{ t('main.main_panel.copy_mention') }}
              </button>
              <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="emit('invoke-command')">
                <AppIcon name="terminal" /> {{ t('main.main_panel.invoke_command') }}
              </button>
              <button v-if="tab.typeCode" class="edit-dropdown-item" type="button" @click="emit('invoke-ai')">
                <AppIcon name="robot" /> {{ t('main.main_panel.invoke_ai') }}
              </button>
            </div>
          </transition>
        </div>
        <button v-if="!readOnly" class="btn btn-secondary btn-sm" type="button" @click="emit('open-full-view')">
          <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
        </button>
        <span v-else class="ro-badge ro-badge-sm">
          <AppIcon name="lock-simple" /> {{ t('main.document_preview.edit_locked') }}
        </span>
      </div>
    </div>
    <div class="card-bd">
      <MdViewer
        :ref="(el) => emit('bind-md-viewer', el)"
        :path="tab.mdPath ?? tab.path"
        :doc-id="isFileTab(tab) ? null : tab.id"
        :project-id="tab.projectId ?? null"
        :git-group-id="tab.gitGroupId ?? null"
        :git-branch="tab.gitBranch ?? null"
        :git-commit="tab.gitCommit ?? null"
        :read-only="readOnly"
      />
    </div>
  </div>
  <div v-else-if="tab.type === 'text'" class="card text-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="file-text" style="color:var(--text-m);" />
        {{ t('main.document_preview.text_title') }}
      </span>
      <div class="card-actions">
        <label class="text-wrap-toggle">
          <input :checked="textWrapEnabled" type="checkbox" @change="onTextWrapChange" />
          <span>{{ t('main.document_preview.wrap_lines') }}</span>
        </label>
        <button v-if="!readOnly && canEdit" class="btn btn-outline btn-sm" type="button" @click="emit('edit-direct')">
          <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
        </button>
        <button class="btn btn-secondary btn-sm" type="button" @click="emit('open-full-view')">
          <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
        </button>
      </div>
    </div>
    <div class="card-bd text-preview-body">
      <TextViewer
        :ref="(el) => emit('bind-text-viewer', el)"
        :path="tab.path"
        :project-id="tab.projectId ?? null"
        :wrap-lines="textWrapEnabled"
        :git-group-id="tab.gitGroupId ?? null"
        :git-branch="tab.gitBranch ?? null"
        :git-commit="tab.gitCommit ?? null"
      />
    </div>
  </div>
  <div v-else-if="tab.type === 'diff'" class="card text-preview-card">
    <div class="card-hd">
      <span class="card-title">
        <AppIcon name="git-diff" style="color:var(--text-m);" />
        {{ t('main.file_diff.title') }}
      </span>
    </div>
    <div class="card-bd text-preview-body">
      <FileDiffViewer
        :path="tab.path"
        :project-id="tab.projectId ?? null"
        :git-group-id="tab.gitGroupId ?? null"
        :git-commit="tab.gitCommit ?? null"
      />
    </div>
  </div>
  <div v-else-if="tab.type === 'too_large'" class="unsupported-view">
    <span>⚠️ {{ t('main.error.file_too_large') }}</span>
    <button @click="emit('close')">{{ t('common.close') }}</button>
  </div>
  <div v-else class="unsupported-view">
    <span>⚠️ {{ t('main.main_panel.text_22') }}</span>
    <button @click="emit('close')">{{ t('common.close') }}</button>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { isFileTab, type Tab } from '../../stores/tabs'
import FileDiffViewer from '../FileDiffViewer.vue'
import MdViewer from '../MdViewer.vue'
import TextViewer from '../TextViewer.vue'

const props = defineProps<{
  tab: Tab
  readOnly: boolean
  canEdit: boolean
  editDropdownOpen: boolean
  textWrapEnabled: boolean
  downloadAvailable: boolean
  downloadBusy: boolean
  uploadBusy: boolean
}>()

const emit = defineEmits<{
  close: []
  'edit-direct': []
  'edit-mention': []
  'invoke-command': []
  'invoke-ai': []
  'open-full-view': []
  'toggle-edit-dropdown': []
  'download-markdown': []
  'upload-markdown': [file: File]
  'update:text-wrap-enabled': [enabled: boolean]
  'bind-md-viewer': [instance: unknown]
  'bind-text-viewer': [instance: unknown]
}>()
const { t } = useI18n()

function onTextWrapChange(event: Event) {
  emit('update:text-wrap-enabled', (event.target as HTMLInputElement).checked)
}

// T0004 §5/§8 — this component only starts the file pick and relays the chosen File; the
// actual read (FileReader/BOM strip), the content PATCH and every refresh belong to
// MainPanel, which already owns that orchestration for the direct-edit save path.
//
// rev2 review fix — GenericDocumentBody also renders editable Markdown *source file* tabs
// (FileExplorer's isFileTab tabs share this same body), and MainPanel's upload relay always
// PATCHes /api/v1/documents/content with the tab id as doc_id. That only makes sense for an
// actual document tab, so upload must not appear on a file tab even when canEdit is true.
const canUploadMarkdown = computed(() => !props.readOnly && props.canEdit && !isFileTab(props.tab))
const uploadFileInput = ref<HTMLInputElement | null>(null)

function triggerUpload() {
  uploadFileInput.value?.click()
}

function onUploadFileSelected(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0] ?? null
  input.value = ''
  if (!file) return
  emit('upload-markdown', file)
}
</script>

<style scoped>
.md-preview-card { overflow: visible; }
.text-preview-card { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.text-preview-body { flex: 1; min-height: 0; padding: 0; }
.card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.text-wrap-toggle { display: inline-flex; align-items: center; gap: 6px; color: var(--text-m); font-size: .76rem; user-select: none; }
.text-wrap-toggle input { margin: 0; accent-color: var(--primary); }
.edit-dropdown-wrap { position: relative; display: inline-block; }
.edit-caret { margin-left: 4px; font-size: .7em; opacity: .7; }
.edit-dropdown-menu {
  position: absolute;
  z-index: 100;
  top: 100%;
  right: 0;
  display: flex;
  flex-direction: column;
  min-width: 160px;
  margin-top: 4px;
  padding: 4px;
  border: 1px solid var(--border, #e5e7eb);
  border-radius: 6px;
  background: var(--bg, #fff);
  box-shadow: 0 4px 12px rgba(0, 0, 0, .12);
}
.edit-dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: 0;
  border-radius: 4px;
  background: none;
  color: var(--text, #1f2937);
  cursor: pointer;
  font-size: .85rem;
  text-align: left;
  white-space: nowrap;
}
.edit-dropdown-item:hover { background: var(--bg-hover, rgba(0, 0, 0, .05)); }
.edit-dropdown-item i { width: 16px; color: var(--text-m, #6b7280); text-align: center; }
.ro-badge {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 9px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--text-m);
  background: var(--surface-h);
  font-size: .66rem;
  font-weight: 700;
}
.ro-badge-sm { margin-left: auto; }
</style>