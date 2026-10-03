<template>
  <!-- Final container for the right Dock (Palette / Git). An independent column with its
       own border even while its content is a placeholder. -->
  <aside class="designer-right-dock" data-test="designer-right-dock" :data-collapsed="collapsed ? 'true' : 'false'">
    <ShellPlaceholder
      v-show="!collapsed"
      area="right"
      :label="t('gui.designer.shell.right_label')"
      :parts="t('gui.designer.shell.right_parts')"
    />
  </aside>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ShellPlaceholder from './ShellPlaceholder.vue'

defineProps<{
  collapsed: boolean
}>()

const { t } = useI18n()
</script>

<style scoped>
.designer-right-dock {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  border-left: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}
</style>
