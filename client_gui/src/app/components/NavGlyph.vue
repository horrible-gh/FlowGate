<template>
  <svg
    class="nav-glyph"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    aria-hidden="true"
    focusable="false"
    v-html="NAV_GLYPHS[name]"
  />
</template>

<script setup lang="ts">
import { NAV_GLYPHS, type NavGlyphName } from './navGlyphs'

withDefaults(defineProps<{ name: NavGlyphName; strokeWidth?: number }>(), { strokeWidth: 1.8 })
</script>
