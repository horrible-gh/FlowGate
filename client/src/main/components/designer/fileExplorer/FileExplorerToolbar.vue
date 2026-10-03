<template>
  <div class="dfx-toolbar-wrap">
  <div class="dfx-toolbar">
    <span class="dfx-toolbar-title">{{ t('main.designer_explorer.title') }}</span>
    <div class="dfx-toolbar-acts">
      <button
        type="button"
        class="dfx-act"
        data-test="dfx-search-toggle"
        :class="{ 'dfx-act--on': searchOpen }"
        :aria-pressed="searchOpen"
        :title="t('main.designer_explorer.search')"
        :aria-label="t('main.designer_explorer.search')"
        :disabled="disabled"
        @click="toggleSearch"
      ><AppIcon name="magnifying-glass" /></button>
      <button
        type="button"
        class="dfx-act"
        data-test="dfx-refresh"
        :title="t('main.designer_explorer.refresh')"
        :aria-label="t('main.designer_explorer.refresh')"
        :disabled="disabled || refreshing"
        @click="$emit('refresh')"
      ><AppIcon name="arrow-clockwise" :spin="refreshing" /></button>
    </div>
  </div>
  <div v-if="searchOpen" class="dfx-search">
    <input
      ref="inputRef"
      class="dfx-search-input"
      data-test="dfx-search-input"
      type="search"
      :value="query"
      :placeholder="t('main.designer_explorer.search_placeholder')"
      :aria-label="t('main.designer_explorer.search')"
      @input="$emit('update:query', ($event.target as HTMLInputElement).value)"
      @keydown.esc.prevent="toggleSearch"
    />
  </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — Designer toolbar (0024-D §3): refresh and find only. The
// Dashboard explorer's branch/group selector and Git update row are deliberately absent.
// "찾기" filters the tree currently loaded for the Flow (name match), so it needs no API.
import { nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import AppIcon from '@shared/AppIcon.vue'

const props = defineProps<{ query: string; searchOpen: boolean; refreshing: boolean; disabled?: boolean }>()
const emit = defineEmits<{
  refresh: []
  'update:query': [value: string]
  'update:searchOpen': [value: boolean]
}>()
const { t } = useI18n()
const inputRef = ref<HTMLInputElement | null>(null)

async function toggleSearch() {
  const next = !props.searchOpen
  emit('update:searchOpen', next)
  if (!next) {
    emit('update:query', '')
    return
  }
  await nextTick()
  inputRef.value?.focus()
}
</script>

<style scoped>
.dfx-toolbar { display: flex; align-items: center; gap: 6px; padding: 6px 8px; }
.dfx-toolbar-title { flex: 1 1 auto; font-size: 0.78rem; font-weight: 600; color: var(--text-p, rgba(255, 255, 255, 0.85)); }
.dfx-toolbar-acts { display: inline-flex; gap: 2px; }
.dfx-act {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: 0.85rem;
  color: var(--text-m, rgba(255, 255, 255, 0.65));
  background: transparent;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
.dfx-act:not(:disabled):hover, .dfx-act--on { background: rgba(255, 255, 255, 0.1); color: #fff; }
.dfx-act:disabled { opacity: 0.45; cursor: not-allowed; }
.dfx-search { padding: 0 8px 6px; }
.dfx-search-input {
  width: 100%;
  padding: 3px 6px;
  font-size: 0.76rem;
  color: inherit;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 4px;
}
</style>
