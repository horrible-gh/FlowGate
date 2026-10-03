<template>
  <!-- Final container for the center work area (Flow Tabs / Canvas). -->
  <section class="designer-workspace" data-test="designer-workspace">
    <ShellPlaceholder
      area="workspace"
      :label="t('gui.designer.shell.workspace_label')"
      :parts="t('gui.designer.shell.workspace_parts')"
    >
      <span data-test="designer-workspace-flow">
        {{ activeFlow ? t('gui.designer.shell.active_flow', { name: activeFlow.name }) : t('gui.designer.shell.no_active_flow') }}
      </span>
    </ShellPlaceholder>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { FlowRef } from '@/features/project'
import ShellPlaceholder from './ShellPlaceholder.vue'

defineProps<{
  activeFlow: FlowRef | null
}>()

const { t } = useI18n()
</script>

<style scoped>
.designer-workspace {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: auto;
}
</style>
