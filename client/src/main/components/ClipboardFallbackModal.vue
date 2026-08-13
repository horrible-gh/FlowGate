<template>
  <teleport to="body">
    <div v-if="state.visible" class="modal-bg">
      <div class="modal-box cfb-box">
        <div class="modal-hd">
          <span class="modal-title">
            <AppIcon name="warning" style="color:var(--warning);" />
            {{ t('main.clipboard_fallback.title') }}
          </span>
          <button class="modal-close" type="button" @click="close">
            <AppIcon name="x" />
          </button>
        </div>
        <div class="modal-bd">
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
        <div class="modal-ft">
          <button type="button" class="btn btn-secondary" @click="close">
            {{ t('common.close') }}
          </button>
          <button type="button" class="btn btn-primary" @click="onCopyAgain">
            <AppIcon name="copy" />
            {{ t('main.clipboard_fallback.copy_btn') }}
          </button>
        </div>
      </div>
    </div>
  </teleport>
</template>

<script setup lang="ts">
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
.cfb-box {
  width: 640px;
}
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
</style>
