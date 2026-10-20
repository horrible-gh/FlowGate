<template>
  <!-- flowgate.v02.0068 T#1 — the [업로드][다운로드] group (0068 D §2.1 / §6): display only. It is handed whether each
       button is usable, why not (the tooltip) and whether it runs (a spinner, no second press), and tells the
       presses. The existing icon button look (IconButton secondary, sm), an accessible name each, and a line that
       parts the group from the buttons after it. A disabled button stays (its reason is the tooltip, on the
       wrapper too: a disabled button hears no pointer). Presses do not reach a draggable title bar. -->
  <span class="document-io-group" data-test="document-io" @mousedown.stop>
    <span class="document-io-slot" :title="uploadTip">
      <IconButton
        :label="t(k('upload'))"
        :title="uploadTip"
        variant="secondary"
        size="sm"
        class="document-io-button"
        :disabled="!upload.enabled || upload.busy"
        :aria-busy="upload.busy ? 'true' : undefined"
        data-test="document-io-upload"
        :data-reason="upload.reason ?? undefined"
        @click="emit('upload')"
      >
        <span v-if="upload.busy" class="document-io-spinner" aria-hidden="true" />
        <svg v-else viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path :d="UPLOAD_PATH" /></svg>
      </IconButton>
    </span>
    <span class="document-io-slot" :title="downloadTitle">
      <IconButton
        :label="t(k('download'))"
        :title="downloadTitle"
        variant="secondary"
        size="sm"
        class="document-io-button"
        :disabled="!download.enabled || download.busy"
        :aria-busy="download.busy ? 'true' : undefined"
        data-test="document-io-download"
        :data-reason="download.reason ?? undefined"
        @click="emit('download')"
      >
        <span v-if="download.busy" class="document-io-spinner" aria-hidden="true" />
        <svg v-else viewBox="0 0 256 256" fill="currentColor" aria-hidden="true" focusable="false"><path :d="DOWNLOAD_PATH" /></svg>
      </IconButton>
    </span>
    <span class="document-io-divider" role="separator" aria-orientation="vertical" data-test="document-io-divider" />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '@/components/ui/IconButton.vue'
import type { DocumentIoButtonView } from './documentIoModel'

/** Phosphor Icons `upload-simple` / `download-simple` (regular, 256 viewBox; MIT — client/shared/iconData). */
const UPLOAD_PATH =
  'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0ZM93.66,77.66,120,51.31V144a8,8,0,0,0,16,0V51.31l26.34,26.35a8,8,0,0,0,11.32-11.32l-40-40a8,8,0,0,0-11.32,0l-40,40A8,8,0,0,0,93.66,77.66Z'
const DOWNLOAD_PATH =
  'M224,144v64a8,8,0,0,1-8,8H40a8,8,0,0,1-8-8V144a8,8,0,0,1,16,0v56H208V144a8,8,0,0,1,16,0Zm-101.66,5.66a8,8,0,0,0,11.32,0l40-40a8,8,0,0,0-11.32-11.32L136,124.69V32a8,8,0,0,0-16,0v92.69L93.66,98.34a8,8,0,0,0-11.32,11.32Z'

const props = defineProps<{
  upload: DocumentIoButtonView
  download: DocumentIoButtonView
  /** flowgate.v02.0074 T#2: the upload's tooltip as given (a work plan's, translated by the group); else the hint / reason. */
  uploadTitle?: string | null
}>()

const emit = defineEmits<{ upload: []; download: [] }>()

const { t } = useI18n()
const k = (key: string) => `gui.document_io.${key}`

/** Usable: what the press does; disabled: why not; running: that it runs. */
function titleOf(view: DocumentIoButtonView, hint: string): string {
  return view.reason ? t(k(`disabled.${view.reason}`)) : t(k(hint))
}

const uploadTip = computed(() => props.uploadTitle ?? titleOf(props.upload, 'upload_hint'))
const downloadTitle = computed(() => titleOf(props.download, 'download_hint'))
</script>

<style scoped>
.document-io-group {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

.document-io-slot {
  display: inline-flex;
}

.document-io-button :deep(svg) {
  width: 15px;
  height: 15px;
}

/* The line between the group and the existing buttons after it. */
.document-io-divider {
  align-self: center;
  width: 1px;
  height: 18px;
  margin: 0 1px 0 2px;
  background: var(--fg2-line-strong);
}

.document-io-spinner {
  width: 12px;
  height: 12px;
  box-sizing: border-box;
  border: 2px solid rgba(140, 192, 255, 0.3);
  border-top-color: currentColor;
  border-radius: 50%;
  animation: document-io-spin 0.7s linear infinite;
}

@keyframes document-io-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
