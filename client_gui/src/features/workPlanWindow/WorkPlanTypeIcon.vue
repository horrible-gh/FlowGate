<template>
  <span class="wpe-type-icon" :style="icon.accent ? { '--wpe-type-accent': icon.accent } : undefined" aria-hidden="true" data-test="wp-edit-type-icon" :data-icon="icon.icon">
    <svg viewBox="0 0 24 24">
      <path :d="path" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    </svg>
  </span>
</template>

<script setup lang="ts">
// flowgate.v02.0073 T#1 — the icon tile of a Step type in the work plan edit window (quantity boxes, card headers):
// the Designer Palette card's 26px icon box (PaletteCard `.palette-card-icon`) with the Palette's glyph and accent for
// the type (workPlanTypeIcon over DEFAULT_PALETTE_ITEMS, paletteIconPath). No table or color of its own: a type with
// no Palette accent is drawn in the theme's muted color. Decorative — the name next to it says the type.
import { computed } from 'vue'
import { paletteIconPath } from '@/features/designer/palette/paletteIcons'
import { workPlanTypeIcon } from './workPlanEditModel'

const props = defineProps<{
  type: string
}>()

const icon = computed(() => workPlanTypeIcon(props.type))
const path = computed(() => paletteIconPath(icon.value.icon))
</script>

<style scoped>
.wpe-type-icon {
  --wpe-type-accent: var(--fg2-muted);
  display: grid;
  flex: 0 0 26px;
  place-items: center;
  box-sizing: border-box;
  width: 26px;
  height: 26px;
  border: 1px solid color-mix(in srgb, var(--wpe-type-accent) 34%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--wpe-type-accent) 13%, transparent);
  color: var(--wpe-type-accent);
}

.wpe-type-icon svg {
  display: block;
  width: 16px;
  height: 16px;
}
</style>
