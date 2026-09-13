<template>
  <!--
    Document full view — flowgate.default.0560 T0018 (4순위, NR0011 원장 ID 40).

    D0008 §4 asked for this: `MainPanel.vue` held four dialogs in one file, and each one
    becomes an independent dialog component while the parent keeps only the open state and
    the data it passes down. D0008 maps this instance to `readonly`.

    Two things about this dialog are load-bearing and are NOT the common layer's defaults:

    `below-header` — 0269 D0002 fixed that this reading surface dims from the app header
    DOWN, so the AI run monitor chip stays visible and clickable while a document is read.
    The common overlay is `inset: 0`; without this the migration would have covered the
    header and undone that decision (T0018 §2.4 / `dialog.css`).

    `.document-modal__body--conversation` — a CH tab does not mount a second
    `ConversationView` here. The card teleports its live instance INTO this element, so the
    dialog shows the same component, in-flight AI poll and unsent draft included (0263
    R0001). The class name is the selector `MainPanel.vue` hands to that Teleport, so it is
    a contract between the two files, not decoration. The shell's own body wrapper never
    replaces it: this element is rendered by the default slot and survives as long as the
    dialog is open, which is what keeps the teleport chain unbroken (T0018 §2.3-2).

    `:close-on-backdrop="false"` is explicit (T0018 §2.2-3): `readonly`'s variant default is
    `true`, but 0412 T0004 removed backdrop-close here and NR0011 records BD=X.
  -->
  <DialogShell
    :open="visible && tab != null"
    variant="readonly"
    size="xl"
    surface="sheet"
    below-header
    surface-class="document-full-view-dialog"
    :close-on-backdrop="false"
    @request-close="emit('close')"
  >
    <template #header>
      <DialogHeader :title="tab?.title ?? ''" :icon="icon" @close="emit('close')">
        <template #actions>
          <!-- CH has no [edit]: a chat is written through its composer, not by hand-editing
               the transcript, and its card offers no [edit] either. `canEdit` is decided by
               `canEditTab()` in MainPanel, the one place that gate lives
               (0432.0003-NR §7-2). -->
          <button
            v-if="canEdit"
            class="btn btn-outline btn-sm"
            type="button"
            @click="emit('edit')"
          >
            <AppIcon name="pencil-simple" /> {{ t('main.document_preview.edit') }}
          </button>
        </template>
      </DialogHeader>
    </template>

    <template #default>
      <div
        class="document-modal__body"
        :class="{ 'document-modal__body--conversation': tab?.typeCode === 'CH' }"
      >
        <template v-if="tab && tab.typeCode !== 'CH'">
          <TextViewer
            v-if="tab.type === 'text'"
            :path="tab.path"
            :project-id="tab.projectId ?? null"
            :wrap-lines="wrapLines"
            :git-group-id="tab.gitGroupId ?? null"
            :git-commit="tab.gitCommit ?? null"
          />
          <!-- 0310 TR: not-a-file-tab → load by doc-id (mirrors the preview-card MdViewer);
               see the comment there and flowgate.default.0310.0003-NR. -->
          <MdViewer
            v-else
            :path="tab.mdPath ?? tab.path"
            :doc-id="isFileTab(tab) ? null : tab.id"
            :project-id="tab.projectId ?? null"
            :git-group-id="tab.gitGroupId ?? null"
            :git-commit="tab.gitCommit ?? null"
          />
        </template>
      </div>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import AppIcon from '@shared/AppIcon.vue'

import MdViewer from './MdViewer.vue'
import TextViewer from './TextViewer.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogShell from './dialogs/DialogShell.vue'
import { isFileTab, type Tab } from '../stores/tabs'

defineProps<{
  visible: boolean
  tab: Tab | null
  icon: string
  canEdit: boolean
  wrapLines: boolean
}>()

const emit = defineEmits<{ close: []; edit: [] }>()

const { t } = useI18n()
</script>

<style scoped>
.document-modal__body {
  flex: 1;
  min-height: 0;
  padding: 0;
  overflow: hidden;
}

.document-modal__body :deep(.text-viewer),
.document-modal__body :deep(.md-viewer) {
  height: 100%;
}

/* CH full view keeps the same single-scroll flex chain as the inline card. */
.document-modal__body--conversation {
  display: flex;
}

.document-modal__body--conversation :deep(.conv-view) {
  flex: 1;
  min-width: 0;
  min-height: 0;
}
</style>

<!--
  Unscoped: `surface-class` lands on the dialog SURFACE, which DialogShell renders and
  teleports out of this component's subtree, so a scoped rule could never reach it.

  Narrow windows give the chat the whole overlay instead of the centred 1180px box. The
  height is taken from the overlay (`%`), never from the viewport: `100dvh - 16px` is
  taller than the below-header container at *every* window height, so it clipped the
  composer by a constant 18px on every screen under 820px wide. The container cap from
  `.fg-dialog-overlay--below-header .fg-dialog-surface--sheet` is left in place —
  `max-height: none` here is what let the box outgrow its track in the first place.
-->
<style>
@media (max-width: 820px) {
  .fg-dialog-surface.document-full-view-dialog:has(.document-modal__body--conversation) {
    width: calc(100vw - 16px);
    height: calc(100% - 16px);
    max-width: none;
  }
}
</style>
