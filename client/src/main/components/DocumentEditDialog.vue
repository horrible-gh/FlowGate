<template>
  <!--
    Document edit — flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 41).

    D0008 §4 split this out of `MainPanel.vue` along with the other three instances there;
    D0008 maps it to `form`, whose `closeOnBackdrop` default is already `false`, so there is
    no override to make here (T0018 §2.2-3).

    T0018 §2.3-3 — the unsaved-edit guard. Before the migration `@keydown.escape` was in the
    markup but nothing inside the dialog took focus, so ESC only fired if the user had
    clicked into a textarea first (NR0011 §9-5). On the common layer the stack's single
    document listener judges ESC regardless of focus, so ESC would now discard an unsaved
    edit every time — a loss path that did not exist before. Option (a) of §2.3-3 is taken:
    every close request (X, ESC, backdrop, 취소) is gated on a dirty check against the values
    loaded into the editor, and a dirty editor asks through the common async
    `confirm()` (the 2순위 T0014/TR0015 ConfirmDialog) before it throws the edit away.
  -->
  <DialogShell
    :open="visible && tab != null"
    variant="form"
    size="xl"
    surface="sheet"
    surface-class="document-edit-dialog"
    :busy="saving"
    @request-close="requestClose"
  >
    <template #header>
      <DialogHeader
        :title="t('main.document_preview.edit_title', { title: tab?.title ?? '' })"
        icon="pencil-simple"
        @close="requestClose"
      >
        <template #actions>
          <button
            class="btn btn-outline btn-sm"
            type="button"
            :disabled="saving"
            @click="urlMode = !urlMode"
          >
            {{ t('main.document_preview.url_import') }}
          </button>
          <button
            class="btn btn-outline btn-sm"
            type="button"
            :disabled="saving"
            :title="headerVisible ? t('main.main_panel.header_hide') : t('main.main_panel.header_show')"
            @click="emit('toggle-header')"
          >
            <AppIcon :name="headerVisible ? 'eye' : 'eye-slash'" />
            {{ headerVisible ? t('main.main_panel.header_hide') : t('main.main_panel.header_edit') }}
          </button>
        </template>
      </DialogHeader>
    </template>

    <template #default>
      <div v-if="urlMode" class="document-url-import">
        <div class="document-url-import__row">
          <input
            v-model="urlInput"
            class="document-url-import__input"
            type="url"
            :placeholder="t('main.document_preview.url_import_placeholder')"
            :disabled="urlLoading || saving"
            @keydown.enter.prevent="fetchUrlPreview"
          />
          <button class="btn btn-outline btn-sm" type="button" :disabled="urlLoading || saving || !urlInput.trim()" @click="fetchUrlPreview">
            {{ urlLoading ? t('main.document_preview.url_import_loading') : t('main.document_preview.url_import_fetch') }}
          </button>
          <button class="btn btn-primary btn-sm" type="button" :disabled="urlLoading || saving || !urlPreview" @click="applyUrlPreview">
            {{ t('main.document_preview.url_import_apply') }}
          </button>
        </div>
        <div v-if="urlError" class="document-url-import__error" role="alert">{{ urlError }}</div>
        <div v-if="urlPreview" class="document-url-import__preview-wrap">
          <div class="document-url-import__preview-label">{{ t('main.document_preview.url_import_preview') }}</div>
          <textarea class="document-url-import__preview" :value="urlPreview" readonly></textarea>
        </div>
      </div>
      <div class="document-editor">
        <div v-if="loading" class="document-editor__state">
          {{ t('common.loading') }}
        </div>
        <div v-else-if="loadError" class="document-editor__state document-editor__state--error">
          {{ loadError }}
        </div>
        <template v-else>
          <div v-if="saveError" class="document-editor__save-error" role="alert">
            {{ saveError }}
          </div>
          <LineNumberedTextarea
            v-if="headerVisible"
            :model-value="fullContent"
            class="document-editor__textarea"
            :wrap-off="true"
            :spellcheck="false"
            dialog-autofocus
            @update:model-value="onFullContentInput"
          />
          <LineNumberedTextarea
            v-else
            :model-value="body"
            class="document-editor__textarea"
            :wrap-off="true"
            :spellcheck="false"
            dialog-autofocus
            @update:model-value="onBodyInput"
          />
        </template>
      </div>
    </template>

    <template #footer>
      <DialogFooter :actions="actions" />
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import AppIcon from '@shared/AppIcon.vue'
import { postRequest } from '@shared/api'

import LineNumberedTextarea from './common/LineNumberedTextarea.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import type { DialogAction } from './dialogs/dialogTypes'
import { confirm as dialogConfirm } from '../composables/useDialogStack'
import type { Tab } from '../stores/tabs'

const props = defineProps<{
  visible: boolean
  tab: Tab | null
  body: string
  fullContent: string
  /** The two values as they were when the document finished loading — the dirty baseline. */
  loadedBody: string
  loadedFullContent: string
  headerVisible: boolean
  loading: boolean
  saving: boolean
  loadError: string
  saveError: string
}>()

const emit = defineEmits<{
  'update:body': [value: string]
  'update:fullContent': [value: string]
  'toggle-header': []
  close: []
  save: []
}>()

const { t } = useI18n()
const urlMode = ref(false)
const urlInput = ref('')
const urlLoading = ref(false)
const urlError = ref('')
const urlPreview = ref('')

function stripImportedFrontmatter(content: string): string {
  const normalized = content.replace(/\r\n/g, '\n')
  if (!normalized.startsWith('---\n')) return normalized
  const end = normalized.indexOf('\n---\n', 4)
  if (end < 0) return normalized
  return normalized.slice(end + 5)
}

function replaceBodyKeepingFrontmatter(fullContent: string, body: string): string {
  const normalized = fullContent.replace(/\r\n/g, '\n')
  if (!normalized.startsWith('---\n')) return body
  const end = normalized.indexOf('\n---\n', 4)
  if (end < 0) return body
  return normalized.slice(0, end + 5) + body
}

async function fetchUrlPreview() {
  if (urlLoading.value || !props.tab || !urlInput.value.trim()) return
  urlLoading.value = true
  urlError.value = ''
  urlPreview.value = ''
  try {
    const res = await postRequest<any>('/api/v1/documents/import-url', {
      doc_id: props.tab.id,
      url: urlInput.value.trim(),
    })
    const data = (res.data as any)?.data ?? res.data
    urlPreview.value = stripImportedFrontmatter(String(data?.content ?? ''))
  } catch (error: any) {
    const detail = error?.response?.data?.detail
    urlError.value = typeof detail === 'string'
      ? detail
      : String(detail?.message ?? t('main.document_preview.url_import_failed'))
  } finally {
    urlLoading.value = false
  }
}

function applyUrlPreview() {
  if (!urlPreview.value) return
  emit('update:body', urlPreview.value)
  emit('update:fullContent', replaceBodyKeepingFrontmatter(props.fullContent, urlPreview.value))
  urlMode.value = false
}

watch(
  () => [props.visible, props.tab?.id] as const,
  () => {
    urlMode.value = false
    urlInput.value = ''
    urlLoading.value = false
    urlError.value = ''
    urlPreview.value = ''
  },
)

function onBodyInput(value: string) {
  emit('update:body', value)
}

function onFullContentInput(value: string) {
  emit('update:fullContent', value)
}

/**
 * Whichever textarea is on screen is the one that can hold an unsaved edit: the two are
 * kept in sync by MainPanel's `toggleHeaderEditMode`, so comparing only the visible one
 * against its loaded baseline is both sufficient and free of false positives from the
 * frontmatter re-join.
 */
const isDirty = computed(() => (props.headerVisible
  ? props.fullContent !== props.loadedFullContent
  : props.body !== props.loadedBody))

/**
 * T0018 §2.3-3 (a). Every user-driven close path funnels through here, so ESC, the header
 * X, a backdrop click and [취소] all get the same answer — the R0001 symptom was exactly
 * one screen answering differently depending on which control was used.
 */
async function requestClose() {
  if (props.saving) return
  if (isDirty.value) {
    const discard = await dialogConfirm({
      title: t('main.document_preview.discard_confirm_title'),
      message: t('main.document_preview.discard_confirm_message'),
      confirmLabel: t('main.document_preview.discard_confirm_ok'),
      danger: true,
    })
    if (!discard) return
  }
  emit('close')
}

/**
 * `[취소] [저장]` — the order is the layer's, not this file's: `footerRolePriority` puts
 * `cancel` immediately left of `primary` before rendering (D0008 §6 "Footer 규칙").
 */
const actions = computed<DialogAction[]>(() => [
  {
    id: 'cancel',
    label: t('common.cancel'),
    role: 'cancel',
    disabled: props.saving,
    onSelect: requestClose,
  },
  {
    id: 'save',
    label: props.saving ? t('main.document_preview.saving') : t('common.save'),
    role: 'primary',
    disabled: props.loading || props.saving || !!props.loadError,
    loading: props.saving,
    onSelect: () => emit('save'),
  },
])

defineExpose({ isDirty, requestClose })
</script>

<style scoped>
.document-url-import {
  flex: 0 0 auto;
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  background: var(--bg-sub, #f8fafc);
}
.document-url-import__row { display: flex; gap: 8px; align-items: center; }
.document-url-import__input {
  flex: 1;
  min-width: 0;
  height: 34px;
  padding: 0 10px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: var(--bg-card, #fff);
  color: var(--text, #1e293b);
}
.document-url-import__input:focus { outline: none; border-color: var(--primary, #2563eb); }
.document-url-import__error { margin-top: 8px; color: var(--danger); font-size: .8125rem; }
.document-url-import__preview-wrap { margin-top: 10px; }
.document-url-import__preview-label { margin-bottom: 5px; color: var(--text-m); font-size: .75rem; font-weight: 600; }
.document-url-import__preview {
  width: 100%;
  height: 180px;
  resize: vertical;
  padding: 10px 12px;
  border: 1px solid var(--border, #e2e8f0);
  border-radius: 6px;
  background: #0f172a;
  color: #e2e8f0;
  font-family: 'JetBrains Mono', monospace;
  font-size: .8125rem;
  line-height: 1.6;
}

.document-editor {
  flex: 1;
  padding: 0;
  /* The comfortable editor height comes from the dialog surface's own height, not from a
     minimum here. A vh minimum is a flex *shrink floor* decoupled from the px-capped box
     track, so past ~1187px viewport height it pushes the footer out of the surface's
     `overflow: hidden` and the save button is unreachable. */
  min-height: 0;
  display: flex;
  flex-direction: column;
  /* The inner textarea is the *sole* scroll container. With a height pinned on the textarea
     AND this body scrollable, both scrolled at once → the reported double scrollbar. */
  overflow: hidden;
}

.document-editor__textarea {
  width: 100%;
  /* Fill the editor track instead of pinning height to a viewport unit. The shared
     component root stays shrinkable and clips; its inner textarea remains the sole
     scroll owner, preserving the prior single-scrollbar contract. */
  flex: 1 1 auto;
  min-height: 0;
  --line-editor-bg: #0f172a;
  --line-editor-color: #e2e8f0;
  --line-editor-gutter-bg: #111c32;
  --line-editor-gutter-color: #64748b;
  --line-editor-gutter-border: #334155;
  --line-editor-font-family: 'JetBrains Mono', monospace;
  --line-editor-font-size: .8125rem;
  --line-editor-line-height: 1.7;
  --line-editor-padding-y: 18px;
  --line-editor-padding-x: 20px;
}

.document-editor__state {
  width: 100%;
  padding: 40px 24px;
  text-align: center;
  color: var(--text-m);
}

.document-editor__state--error {
  color: var(--danger);
}

.document-editor__save-error {
  flex: 0 1 auto;
  max-height: 32%;
  overflow-y: auto;
  padding: 12px 20px;
  border-bottom: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
  background: color-mix(in srgb, var(--danger) 12%, #0f172a);
  color: var(--danger);
  white-space: pre-wrap;
}
</style>

<!--
  Unscoped: `surface-class` lands on the dialog SURFACE, which DialogShell renders and
  teleports out of this component's subtree. The width is the same `min(1120px, 94vw)`
  track `.document-modal--edit` measured, kept rather than widened to the `xl` 1180px the
  full view uses — the editor was deliberately the narrower of the two.
-->
<style>
.fg-dialog-surface.document-edit-dialog {
  width: min(1120px, 94vw);
}
</style>
