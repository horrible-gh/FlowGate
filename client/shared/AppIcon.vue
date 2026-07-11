<template>
  <i class="app-icon" :class="{ 'app-icon--spin': spin }" aria-hidden="true">
    <svg viewBox="0 0 256 256" width="1em" height="1em" fill="currentColor" focusable="false" v-html="markup" />
  </i>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { ICONS, type IconWeight } from './iconData'

const props = withDefaults(
  defineProps<{
    name: string
    weight?: IconWeight
    spin?: boolean
  }>(),
  {
    weight: 'regular',
    spin: false,
  },
)

const markup = computed(() => {
  const entry = ICONS[props.name]
  if (!entry) {
    if (import.meta.env.DEV) console.warn(`[AppIcon] unknown icon name: ${props.name}`)
    return ''
  }
  return entry[props.weight] ?? entry.regular
})
</script>

<style scoped>
/* Rendered as <i> so existing `.parent i { … }` / `i { … }` icon styles keep matching.
   The inner <svg> is sized in em and inherits `color` via fill:currentColor, mirroring how
   the previous icon-font glyph behaved. */
.app-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  vertical-align: -0.125em;
  font-style: normal;
  flex-shrink: 0;
  line-height: 1;
}
.app-icon > svg {
  display: block;
  width: 1em;
  height: 1em;
  fill: currentColor;
}
.app-icon--spin > svg {
  animation: app-icon-spin 1s linear infinite;
}
@keyframes app-icon-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
