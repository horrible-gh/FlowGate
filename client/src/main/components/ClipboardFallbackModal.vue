<template>
  <DialogShell :open="state.visible" variant="alert" surface-class="dialog-clipboard-fallback-modal"  @request-close="close">
    <template #header>
      <DialogHeader title="" :closeable="true" @close="close">
        <template #title>
            <AppIcon name="warning" style="color:var(--warning);" />
            {{ t('main.clipboard_fallback.title') }}
          </template>
      </DialogHeader>
    </template>

        
        <div class="dialog-feature-body">
          <p class="cfb-msg">{{ t('main.clipboard_fallback.message') }}</p>
          <textarea
            ref="textEl"
            class="cfb-text"
            readonly
            spellcheck="false"
            :value="state.text"
            @focus="selectAll"
          ></textarea>
        </div>
        
      

    <template #footer>
      <DialogFooter :actions="[
        { id: 'close-0', role: 'cancel', label: t('common.close'), onSelect: () => close() },
        { id: 'onCopyAgain-1', role: 'primary', label: t('main.clipboard_fallback.copy_btn'), onSelect: () => onCopyAgain() }
      ]">
        <template #action-close-0>
            {{ t('common.close') }}
          </template>
        <template #action-onCopyAgain-1>
            <AppIcon name="copy" />
            {{ t('main.clipboard_fallback.copy_btn') }}
          </template>
      </DialogFooter>
    </template>
  </DialogShell>
</template>

<script setup lang="ts">
import DialogShell from './dialogs/DialogShell.vue'
import DialogHeader from './dialogs/DialogHeader.vue'
import DialogFooter from './dialogs/DialogFooter.vue'
// Manual-copy fallback for failed clipboard writes (B0001 / group 0221) — see
// useClipboardFallback for why this exists on HTTP LAN deploys. Mounted once in App.vue.
import { nextTick, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'
import { copyToClipboard } from '../utils/clipboard'
import { useClipboardFallback } from '../composables/useClipboardFallback'
import { useToast } from './common/useToast'

const { state, close } = useClipboardFallback()
const { t } = useI18n()
const { showToast } = useToast()

const textEl = ref<HTMLTextAreaElement | null>(null)

// Pre-select the text on open so a bare Ctrl+C already works.
watch(
  () => state.visible,
  async (visible) => {
    if (!visible) return
    await nextTick()
    textEl.value?.focus()
  },
)

function selectAll() {
  textEl.value?.select()
}

async function onCopyAgain() {
  // This click grants a FRESH transient activation with the text already in hand — no
  // producer round-trip in between — so execCommand succeeds here even on insecure origins
  // where the original (deferred) write failed.
  const ok = await copyToClipboard(state.text)
  if (ok) {
    showToast(t('main.clipboard_fallback.toast_copied'), 'success')
    close()
  } else {
    showToast(t('main.clipboard_fallback.toast_copy_failed'), 'warning')
    textEl.value?.focus()
  }
}
</script>

<style scoped>

.cfb-msg {
  font-size: .9rem;
  color: var(--text);
  line-height: 1.5;
  margin: 0 0 12px;
}
.cfb-text {
  width: 100%;
  min-height: 220px;
  resize: vertical;
  font-family: 'JetBrains Mono', monospace;
  font-size: .78rem;
  line-height: 1.45;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--r, 6px);
  background: var(--bg);
  color: var(--text);
  white-space: pre;
}

:global(.fg-dialog-surface.dialog-clipboard-fallback-modal) { width: 640px; }
</style>
