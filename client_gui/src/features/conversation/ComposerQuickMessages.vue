<template>
  <!-- flowgate.v02test.0072 (시안 v7): one row of quick message buttons right above the chat input. Off = grey text
       and grey border; on = red (계속) or blue (1회) text and border; never a filled background, never a kind mark.
       Hidden while loading, when loading failed and without project.settings.read. The `actions` slot is the right
       end of the row where card 3 puts ＋ 추가 / ✎ 편집. -->
  <div
    v-if="visible"
    class="conv-quick"
    role="toolbar"
    :aria-label="t('gui.quickMessages.row_label')"
    data-test="conv-quick-row"
  >
    <div class="conv-quick-scroll">
      <button
        v-for="item in items"
        :key="item.id"
        type="button"
        class="conv-quick-btn"
        :class="isOn(item) ? `is-on-${item.kind}` : null"
        :data-state="isOn(item) ? 'on' : 'off'"
        :aria-pressed="isOn(item)"
        :title="tip(item)"
        :disabled="disabled"
        data-test="conv-quick-btn"
        @click="emit('toggle', item.id)"
      >{{ item.title }}</button>
      <span v-if="items.length === 0" class="conv-quick-empty" data-test="conv-quick-empty">{{ t('gui.quickMessages.empty') }}</span>
    </div>
    <div v-if="$slots.actions" class="conv-quick-actions">
      <slot name="actions"></slot>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { QuickMessage } from './quickMessages/api'
import { installQuickMessages } from './quickMessages/messages'

defineProps<{
  items: readonly QuickMessage[]
  visible: boolean
  isOn: (item: QuickMessage) => boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ toggle: [id: number] }>()

installQuickMessages(useI18n({ useScope: 'global' }))
const { t } = useI18n()

const tip = (item: QuickMessage) =>
  t(item.kind === 'sticky' ? 'gui.quickMessages.tip_sticky' : 'gui.quickMessages.tip_once', { text: item.message })
</script>

<style scoped>
.conv-quick {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  margin-bottom: 5px;
}

.conv-quick-scroll {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 5px;
  overflow-x: auto;
  scrollbar-width: thin;
}

.conv-quick-btn {
  flex: 0 0 auto;
  height: 22px;
  padding: 0 8px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: 11px/20px var(--fg2-font);
  white-space: nowrap;
  cursor: pointer;
}

.conv-quick-btn.is-on-sticky {
  border-color: var(--fg2-status-danger-fg);
  color: var(--fg2-status-danger-fg);
}

.conv-quick-btn.is-on-once {
  border-color: var(--fg2-status-info-fg);
  color: var(--fg2-status-info-fg);
}

.conv-quick-btn:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.conv-quick-btn:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.conv-quick-empty {
  font-size: 11px;
  color: var(--fg2-muted-2);
  white-space: nowrap;
}

.conv-quick-actions {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 4px;
}
</style>
