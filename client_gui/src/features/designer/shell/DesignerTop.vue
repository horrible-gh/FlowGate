<template>
  <!-- Final container for the Designer's top band (Header / MenuBar / Toolbar). -->
  <header class="designer-top" data-test="designer-top">
    <ShellPlaceholder
      area="top"
      :label="t('gui.designer.shell.top_label')"
      :parts="t('gui.designer.shell.top_parts')"
    />
  </header>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ShellPlaceholder from './ShellPlaceholder.vue'

const { t } = useI18n()
</script>

<style scoped>
.designer-top {
  flex: none;
  min-width: 0;
  border-bottom: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}
</style>
