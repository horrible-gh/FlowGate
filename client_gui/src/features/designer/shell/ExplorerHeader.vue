<template>
  <!-- Left Dock title + collapse button (0003-D §3.4). The Explorers keep their own titles,
       refresh and search below it. -->
  <header class="explorer-header" data-test="designer-explorer-header">
    <span class="explorer-header-title">{{ t('gui.designer.shell.explorer_title') }}</span>
    <IconButton
      size="sm"
      :label="t('gui.designer.shell.collapse_left')"
      :title="t('gui.designer.shell.collapse_left')"
      data-test="designer-left-collapse"
      @click="emit('collapse')"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M10 4L6 8l4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </IconButton>
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'

const emit = defineEmits<{ collapse: [] }>()

const { t } = useI18n()
</script>

<style scoped>
.explorer-header {
  display: flex;
  flex: none;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 6px 6px 12px;
  border-bottom: 1px solid var(--fg2-line);
}

.explorer-header-title {
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.04em;
  color: var(--fg2-text);
}
</style>
