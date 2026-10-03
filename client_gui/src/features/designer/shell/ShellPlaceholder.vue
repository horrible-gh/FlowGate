<template>
  <!-- flowgate.v02.0025 — content stand-in for a final Designer container (0003-D §3.6).
       It sits INSIDE the container; a later group replaces this one element with the real
       part, without touching the container around it. -->
  <div class="shell-placeholder" :data-test="`designer-placeholder-${area}`">
    <span class="shell-placeholder-area" data-test="designer-placeholder-label">{{ label }}</span>
    <span class="shell-placeholder-parts">{{ parts }}</span>
    <span v-if="$slots.default" class="shell-placeholder-detail"><slot /></span>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  /** Stable area key for tests (top / workspace / bottom / right). */
  area: string
  /** Area name, e.g. "TOP". */
  label: string
  /** The final parts this placeholder stands in for. */
  parts: string
}>()
</script>

<style scoped>
.shell-placeholder {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  min-width: 0;
  padding: 10px 12px;
  color: var(--fg2-muted);
  font-family: var(--fg2-font);
  font-size: 12px;
}

.shell-placeholder-area {
  font-weight: 700;
  letter-spacing: 0.06em;
  color: var(--fg2-text);
}

.shell-placeholder-detail {
  color: var(--fg2-muted-2);
}
</style>
