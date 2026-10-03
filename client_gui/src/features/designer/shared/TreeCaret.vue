<template>
  <span
    class="dx-caret"
    :class="{ 'dx-caret--open': expanded, 'dx-caret--empty': !hasChildren }"
    aria-hidden="true"
    @click.stop="$emit('toggle')"
  >
    <svg viewBox="0 0 16 16" width="10" height="10"><path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" /></svg>
  </span>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — expand/collapse caret primitive (0024-D §8.2). Rows with
// children use it; a leaf row renders a spacer instead of a dead caret.
defineProps<{ expanded: boolean; hasChildren: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<style scoped>
.dx-caret {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 12px;
  height: 12px;
  color: var(--fg2-muted);
  transition: transform 0.12s ease;
}

.dx-caret--open {
  transform: rotate(90deg);
}

.dx-caret--empty {
  visibility: hidden;
}
</style>
