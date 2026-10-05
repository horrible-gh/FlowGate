<template>
  <svg
    class="fgs-icon"
    :class="size ? `fgs-icon--${size}` : undefined"
    viewBox="0 0 24 24"
    aria-hidden="true"
    focusable="false"
    v-html="SETTINGS_ICONS[name]"
  />
</template>

<script setup lang="ts">
import { SETTINGS_ICONS, type SettingsIconName } from './icons'

defineProps<{ name: SettingsIconName; size?: 'sm' | 'lg' | 'xl' }>()
</script>

<style scoped>
.fgs-icon {
  width: 18px;
  height: 18px;
  flex: 0 0 auto;
  stroke: currentColor;
  fill: none;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
  vertical-align: middle;
}

.fgs-icon--sm {
  width: 15px;
  height: 15px;
}

.fgs-icon--lg {
  width: 24px;
  height: 24px;
}

.fgs-icon--xl {
  width: 30px;
  height: 30px;
}
</style>
