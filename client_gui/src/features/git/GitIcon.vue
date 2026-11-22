<template>
  <svg
    class="gx-icon"
    viewBox="0 0 24 24"
    :width="size"
    :height="size"
    fill="none"
    stroke="currentColor"
    stroke-width="2"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    :data-icon="name"
  ><path :d="GIT_ICON_PATHS[name]" /></svg>
</template>

<script lang="ts">
// flowgate.v02test.0068 — the line icons of the approved Git tab mockup (v16, 9cn8mjxm), path for
// path, so the tab draws the same shapes as the mockup instead of letter glyphs (⑂ ◫ ☷ ⟳).
export const GIT_ICON_PATHS = {
  commit: 'M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z M3 12h6 M15 12h6',
  fetch: 'M12 4v12 M7 11l5 5l5-5 M5 20h14',
  merge: 'M6 3a3 3 0 1 0 0 6a3 3 0 0 0 0-6z M18 15a3 3 0 1 0 0 6a3 3 0 0 0 0-6z M6 9v12 M6 9a9 9 0 0 0 9 9',
  push: 'M12 20V8 M7 13l5-5l5 5 M5 4h14',
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7 M20 4v7h-7',
  git: 'M6 3v12 M18 9a3 3 0 1 0 0-6a3 3 0 0 0 0 6z M6 21a3 3 0 1 0 0-6a3 3 0 0 0 0 6z M18 9a9 9 0 0 1-9 9',
  diff: 'M12 3v18 M5 8h4 M7 6v4 M15 16h4',
  next: 'M9 6l6 6l-6 6',
  down: 'M6 9l6 6l6-6',
  ok: 'M5 12l5 5L20 7',
  x: 'M6 6l12 12M18 6L6 18',
  spin: 'M12 3a9 9 0 1 0 9 9',
} as const

export type GitIconName = keyof typeof GIT_ICON_PATHS
</script>

<script setup lang="ts">
withDefaults(defineProps<{ name: GitIconName; size?: number }>(), { size: 15 })
</script>

<style scoped>
.gx-icon { display:inline-block; flex:none; vertical-align:middle }
</style>
