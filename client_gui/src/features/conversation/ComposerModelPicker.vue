<template>
  <!-- flowgate.v02.0047 — the composer's compact AI picker (R0001 §2.3-7): the selected
       provider's name on a small button, the project's providers in a popover above it.
       The list is the providers call as is; nothing here is a fixed model list. -->
  <div ref="rootEl" class="cmp-model">
    <button
      type="button"
      class="cmp-model-btn"
      data-test="conv-provider-btn"
      aria-haspopup="listbox"
      :title="label"
      :aria-label="`${t('gui.conversation.composer.provider')}: ${label}`"
      :aria-expanded="open"
      :disabled="disabled || providers.length === 0"
      @click="toggle"
    >
      <span class="cmp-model-mark" aria-hidden="true">◆</span>
      <span class="cmp-model-name" data-test="conv-provider-name">{{ label }}</span>
      <span class="cmp-model-arrow" aria-hidden="true">▾</span>
    </button>
    <div v-if="open" class="cmp-model-pop" role="listbox" data-test="conv-provider-pop" :aria-label="t('gui.conversation.composer.provider')">
      <button
        v-for="p in providers"
        :key="p.id"
        type="button"
        role="option"
        class="cmp-model-opt"
        :class="{ 'is-on': p.id === selectedId }"
        :aria-selected="p.id === selectedId"
        :title="p.name"
        data-test="conv-provider-option"
        :data-provider-id="p.id"
        @click="pick(p.id)"
      >
        <span class="cmp-model-opt-name">{{ p.name }}</span>
        <span class="cmp-model-check" aria-hidden="true">✓</span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { usePopover } from './popover'
import type { ChatProvider } from './types'

const props = defineProps<{
  providers: readonly ChatProvider[]
  selectedId: string
  status: string
  disabled?: boolean
}>()

const emit = defineEmits<{ select: [providerId: string] }>()

const { t } = useI18n()

const rootEl = ref<HTMLElement | null>(null)
const { open, toggle, close } = usePopover(rootEl)

const label = computed(() => {
  const selected = props.providers.find((p) => p.id === props.selectedId)
  if (selected) return selected.name
  if (props.status === 'loading') return t('gui.conversation.composer.provider_loading')
  if (props.status === 'error') return t('gui.conversation.composer.provider_failed')
  return t('gui.conversation.composer.provider_none')
})

function pick(providerId: string): void {
  close()
  if (providerId !== props.selectedId) emit('select', providerId)
}
</script>

<style scoped>
.cmp-model {
  position: relative;
  flex: 0 1 92px;
  min-width: 52px;
}

.cmp-model-btn {
  width: 100%;
  height: 27px;
  min-width: 0;
  padding: 0 7px;
  display: flex;
  align-items: center;
  gap: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  font-size: 10px;
  font-weight: 700;
  cursor: pointer;
}

.cmp-model-btn:hover:not(:disabled),
.cmp-model-btn[aria-expanded='true'] {
  border-color: var(--fg2-control-hover-border);
}

.cmp-model-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.cmp-model-mark {
  flex: 0 0 auto;
  color: var(--fg2-violet);
  font-size: 11px;
}

.cmp-model-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
}

.cmp-model-arrow {
  flex: 0 0 auto;
  color: var(--fg2-muted-2);
  font-size: 9px;
}

.cmp-model-pop {
  position: absolute;
  left: 0;
  bottom: calc(100% + 4px);
  z-index: 30;
  min-width: 186px;
  max-width: 260px;
  max-height: 220px;
  overflow: auto;
  padding: 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius);
  background: var(--fg2-panel);
  box-shadow: var(--fg2-overlay-shadow);
}

.cmp-model-opt {
  width: 100%;
  height: 32px;
  padding: 0 8px;
  display: flex;
  align-items: center;
  gap: 7px;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  text-align: left;
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.cmp-model-opt:hover,
.cmp-model-opt.is-on {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.cmp-model-opt-name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cmp-model-check {
  flex: 0 0 auto;
  color: var(--fg2-status-info-fg);
  visibility: hidden;
}

.cmp-model-opt.is-on .cmp-model-check {
  visibility: visible;
}

.cmp-model-btn:focus-visible,
.cmp-model-opt:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}
</style>
