<template>
  <!-- Final container for the bottom Dock (Inspector / Minimap). Lives inside the
       CenterColumn, so it is only ever as wide as the Center. -->
  <section class="designer-bottom-dock" data-test="designer-bottom-dock" :style="{ height: `${height}px` }">
    <ShellPlaceholder
      area="bottom"
      :label="t('gui.designer.shell.bottom_label')"
      :parts="t('gui.designer.shell.bottom_parts')"
    />
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ShellPlaceholder from './ShellPlaceholder.vue'

defineProps<{
  /** bottomHeight from the Shell's layout state. */
  height: number
}>()

const { t } = useI18n()
</script>

<style scoped>
.designer-bottom-dock {
  flex: none;
  min-width: 0;
  min-height: 0;
  overflow: auto;
  border-top: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}
</style>
