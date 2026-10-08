<template>
  <!-- flowgate.v02.0047 — the composer's ⋯ menu (R0001 §2.3-6): mention copy and AI call, the
       same actions the composer had as standing buttons. -->
  <div ref="rootEl" class="ctm">
    <button
      type="button"
      class="ctm-btn"
      data-test="conv-more-btn"
      aria-haspopup="menu"
      :title="t('gui.conversation.composer.more')"
      :aria-label="t('gui.conversation.composer.more')"
      :aria-expanded="open"
      @click="toggle"
    >
      <span aria-hidden="true">⋯</span>
    </button>
    <div v-if="open" class="ctm-pop" role="menu" data-test="conv-more-menu" :aria-label="t('gui.conversation.composer.more')">
      <button
        type="button"
        role="menuitem"
        class="ctm-item"
        data-test="conv-copy-mention"
        :disabled="mentionDisabled"
        @click="run('copy-mention')"
      >
        <span class="ctm-icon" aria-hidden="true">⧉</span>
        <span>{{ t('gui.conversation.composer.copy_mention') }}</span>
      </button>
      <button
        v-if="hasProviders"
        type="button"
        role="menuitem"
        class="ctm-item ctm-item--ai"
        data-test="conv-invoke-ai"
        :disabled="invokeDisabled"
        @click="run('invoke-ai')"
      >
        <span class="ctm-icon" aria-hidden="true">{{ running ? '…' : '◆' }}</span>
        <span>{{ running ? t('gui.conversation.composer.ai_running') : t('gui.conversation.composer.invoke_ai') }}</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePopover } from './popover'

defineProps<{
  hasProviders: boolean
  running: boolean
  mentionDisabled: boolean
  invokeDisabled: boolean
}>()

const emit = defineEmits<{ 'copy-mention': []; 'invoke-ai': [] }>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const { open, toggle, close } = usePopover(rootEl)

function run(action: 'copy-mention' | 'invoke-ai'): void {
  close()
  if (action === 'copy-mention') emit('copy-mention')
  else emit('invoke-ai')
}
</script>

<style scoped>
.ctm {
  position: relative;
  flex: 0 0 auto;
}

.ctm-btn {
  width: 26px;
  height: 27px;
  padding: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-muted);
  display: grid;
  place-items: center;
  font-size: 13px;
  cursor: pointer;
}

.ctm-btn:hover,
.ctm-btn[aria-expanded='true'] {
  color: var(--fg2-text);
  border-color: var(--fg2-control-hover-border);
}

.ctm-pop {
  position: absolute;
  right: 0;
  bottom: calc(100% + 4px);
  z-index: 30;
  width: 154px;
  padding: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
}

.ctm-item {
  width: 100%;
  height: 30px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  text-align: left;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.ctm-item:hover:not(:disabled) {
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
}

.ctm-item:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.ctm-icon {
  flex: 0 0 14px;
  text-align: center;
}

.ctm-item--ai .ctm-icon {
  color: var(--fg2-violet);
}

.ctm-btn:focus-visible,
.ctm-item:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
