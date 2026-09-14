<template>
  <!-- CH (conversation) body — lifted out of MainPanel's template in group 0566 T0007 §3.
       The card markup and the Teleport that carries the chat into the full-view dialog live
       here; MainPanel keeps the dialog itself, the full-view flag and the mention /
       manual-copy orchestration, and hands each of them over as an explicit prop or event.
       This component owns rendering only — it decides nothing about the chat. -->
  <div class="card md-preview-card conv-card">
    <div class="card-hd">
      <span class="card-title">
        <span class="doc-tag c-CH" style="font-size:.68rem; padding:2px 5px; margin-right:4px;">CH</span>
        {{ t('main.conversation_view.title') }}
      </span>
      <div class="card-actions">
        <button class="btn btn-secondary btn-sm" type="button" @click="emit('open-full-view')">
          <AppIcon name="corners-out" /> {{ t('main.document_preview.full_view') }}
        </button>
      </div>
    </div>
    <div class="card-bd conv-card-bd">
      <!-- Exactly one ConversationView exists per CH tab, for the lifetime of that tab. The
           full view MOVES this very instance instead of mounting a second one, so the unsent
           draft, an in-flight AI call with its poll loop and spinner, and the inline
           manual-copy panel all survive the trip in both directions.
           `to` swings null <-> selector rather than staying a constant selector: Teleport
           resolves the target when it mounts and only re-resolves when `to` itself changes,
           and this card mounts long before any dialog exists. Both props read the same flag
           (MainPanel's convFullViewOn / convFullViewHost), so an enabled teleport can never
           be pointed at a target that is not there. -->
      <Teleport :to="fullViewHost" :disabled="!fullViewOn">
        <ConversationView
          :ref="(el) => emit('bind-conversation-view', el)"
          :doc-id="tab.id"
          :project-id="tab.projectId ?? null"
          :manual-copy-text="manualCopyText"
          :read-only="readOnly"
          @copy-mention="emit('copy-mention', $event)"
          @manual-copy-dismiss="emit('manual-copy-dismiss')"
        />
      </Teleport>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import type { Tab } from '../../stores/tabs'
import ConversationView from '../ConversationView.vue'

defineProps<{
  tab: Tab
  /** The chat's own read-only line, decided by MainPanel: the AI-run document lock EXCEPT
   *  for a run this chat itself started, which keeps its existing STOP-button path. */
  readOnly: boolean
  /** Text of a mention copy that failed to reach the clipboard; MainPanel owns it. */
  manualCopyText: string | null
  /** Teleport target selector while the full view is open, null otherwise. */
  fullViewHost: string | null
  fullViewOn: boolean
}>()

const emit = defineEmits<{
  'open-full-view': []
  'copy-mention': [opts?: { auto?: boolean }]
  'manual-copy-dismiss': []
  /** Relays the live ConversationView instance up so MainPanel's convViewRefs registry
   *  keeps working (jumpToSeq / refreshChatSettings / scrollToBottom). */
  'bind-conversation-view': [instance: unknown]
}>()
const { t } = useI18n()
</script>

<style scoped>
/* .card (global) has overflow:hidden, which clips anything a card head pops out of it. */
.md-preview-card { overflow: visible; }
.card-actions { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }

/* The card body hosts the chat (scrolling log + pinned composer). It fills the conversation
   card, which itself flexes to fill the space between the workflow strip and the sticky
   action bar (MainPanel's .content-wrap--conversation rules), so the message list scrolls
   internally and the surface grows/shrinks with the window instead of being a fixed-height
   box that forces a page scrollbar. TR0044.0010 rev7. */
.conv-card-bd {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 0;
}
.conv-card-bd > * {
  flex: 1;
  min-height: 0;
}
</style>
