<template>
  <div class="status-bar" role="status">
    <template v-if="activeTab">
      <span class="status-bar__path">{{ activeTab.path }}</span>
      <span
        class="status-bar__badge"
        :class="activeTab.readonly ? 'readonly' : 'editable'"
      >
        {{ activeTab.readonly ? t('main.nav.permission.readonly') : t('main.nav.permission.editable') }}
      </span>
      <span v-if="activeTab.modifiedBy" class="status-bar__modified">
        {{ t('main.status.modified_by') }}: {{ activeTab.modifiedBy }}
        <template v-if="activeTab.modifiedAt"> · {{ activeTab.modifiedAt }}</template>
      </span>
    </template>
    <span v-else class="status-bar__empty">FlowGate</span>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTabsStore } from '../stores/tabs'

const { t } = useI18n()
const tabsStore = useTabsStore()
const activeTab = computed(() => tabsStore.activeTab)
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 0 12px;
  height: 24px;
  background: var(--color-statusbar-bg, #11111b);
  border-top: 1px solid var(--color-border, #313244);
  font-size: 0.75rem;
  overflow: hidden;
}

.status-bar__path {
  opacity: 0.7;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}

.status-bar__badge {
  padding: 1px 6px;
  border-radius: 3px;
  font-size: 0.7rem;
  white-space: nowrap;
}

.status-bar__badge.readonly {
  background: var(--color-warn, #f9e2af);
  color: #1e1e2e;
}

.status-bar__badge.editable {
  background: var(--color-success, #a6e3a1);
  color: #1e1e2e;
}

.status-bar__modified {
  opacity: 0.6;
  white-space: nowrap;
}

.status-bar__empty {
  opacity: 0.4;
}
</style>
