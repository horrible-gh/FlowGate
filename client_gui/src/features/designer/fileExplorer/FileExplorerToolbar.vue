<template>
  <div class="dfx-toolbar-wrap">
    <div class="dfx-toolbar">
      <span class="dfx-toolbar-title">{{ t('gui.designer.file_explorer.title') }}</span>
      <div class="dfx-toolbar-acts">
        <IconButton
          size="sm"
          data-test="dfx-search-toggle"
          :class="{ 'dfx-act--on': searchOpen }"
          :aria-pressed="searchOpen"
          :label="t('gui.designer.file_explorer.search')"
          :title="t('gui.designer.file_explorer.search')"
          :disabled="disabled"
          @click="toggleSearch"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" stroke-width="1.5" />
            <path d="M10.5 10.5 14 14" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
          </svg>
        </IconButton>
        <IconButton
          size="sm"
          data-test="dfx-refresh"
          :label="t('gui.common.refresh')"
          :title="t('gui.common.refresh')"
          :disabled="disabled || refreshing"
          @click="$emit('refresh')"
        >
          <svg viewBox="0 0 16 16" aria-hidden="true" :class="{ 'dfx-spin': refreshing }">
            <path d="M13 8a5 5 0 1 1-1.46-3.54" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" />
            <path d="M13 2.5V5h-2.5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
        </IconButton>
      </div>
    </div>
    <div v-if="searchOpen" class="dfx-search">
      <Input
        ref="inputRef"
        type="search"
        data-test="dfx-search-input"
        :model-value="query"
        :placeholder="t('gui.designer.file_explorer.search_placeholder')"
        :aria-label="t('gui.designer.file_explorer.search')"
        @update:model-value="$emit('update:query', $event)"
        @keydown.esc.prevent="toggleSearch"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — Designer toolbar (0024-D §3): refresh and find only. The v0.1
// Dashboard explorer's branch/group selector and Git update row are deliberately absent.
// "찾기" filters the tree already loaded for the Flow (name match), so it needs no API.
import { nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'
import Input from '../../../components/ui/Input.vue'

const props = defineProps<{ query: string; searchOpen: boolean; refreshing: boolean; disabled?: boolean }>()
const emit = defineEmits<{
  refresh: []
  'update:query': [value: string]
  'update:searchOpen': [value: boolean]
}>()
const { t } = useI18n()
const inputRef = ref<{ $el: HTMLInputElement } | null>(null)

async function toggleSearch() {
  const next = !props.searchOpen
  emit('update:searchOpen', next)
  if (!next) {
    emit('update:query', '')
    return
  }
  await nextTick()
  inputRef.value?.$el.focus()
}
</script>

<style scoped>
.dfx-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 8px 6px 12px;
}

.dfx-toolbar-title {
  flex: 1 1 auto;
  font-size: 13px;
  font-weight: 650;
  color: var(--fg2-text);
}

.dfx-toolbar-acts {
  display: inline-flex;
  gap: 2px;
}

.dfx-act--on {
  background: rgba(255, 255, 255, 0.08);
  color: var(--fg2-text);
}

.dfx-spin {
  animation: dfx-spin 0.8s linear infinite;
}

@keyframes dfx-spin {
  to {
    transform: rotate(360deg);
  }
}

.dfx-search {
  padding: 0 8px 8px;
}

.dfx-search :deep(.ui-input) {
  height: var(--fg2-control-h-sm);
  font-size: 12px;
}
</style>
