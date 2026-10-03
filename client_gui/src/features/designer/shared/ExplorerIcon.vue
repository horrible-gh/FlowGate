<template>
  <svg
    class="dx-icon"
    viewBox="0 0 16 16"
    width="14"
    height="14"
    fill="none"
    stroke="currentColor"
    stroke-width="1.3"
    stroke-linejoin="round"
    aria-hidden="true"
    focusable="false"
    :data-icon="name"
  >
    <template v-if="name === 'project'">
      <rect x="2" y="2.5" width="12" height="11" rx="2" />
      <path d="M2 6h12" />
    </template>
    <path v-else-if="name === 'folder'" d="M1.5 3.5h4l1.5 1.5h7.5v7.5h-13z" />
    <path v-else-if="name === 'folder-open'" d="M1.5 4.5v8h11l2-6H4l-2 6" />
    <template v-else>
      <circle cx="3.5" cy="8" r="1.8" />
      <circle cx="12.5" cy="4" r="1.8" />
      <circle cx="12.5" cy="12" r="1.8" />
      <path d="M5.2 7.2l5.6-2.4M5.2 8.8l5.6 2.4" />
    </template>
  </svg>
</template>

<script setup lang="ts">
// flowgate.v02.0025 T0006 §4 — the one set of Designer Explorer glyphs, so the same meaning
// is never drawn twice: Project = workspace window, Group and file folders = the folder pair
// (closed / open), Flow = node graph. Shape only; the owning row picks the colour through
// `color` (stroke = currentColor).
defineProps<{ name: 'project' | 'folder' | 'folder-open' | 'flow' }>()
</script>

<style scoped>
.dx-icon {
  flex: none;
}
</style>
