<template>
  <span
    class="dfx-caret"
    :class="{ 'dfx-caret--open': expanded, 'dfx-caret--empty': !hasChildren }"
    aria-hidden="true"
    @click.stop="$emit('toggle')"
  ><AppIcon name="caret-right" /></span>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — expand/collapse caret primitive (0024-D §8.2). Used by
// FolderRow only; a file has no children, so FileRow renders a spacer instead.
import AppIcon from '@shared/AppIcon.vue'

defineProps<{ expanded: boolean; hasChildren: boolean }>()
defineEmits<{ toggle: [] }>()
</script>

<style scoped>
.dfx-caret {
  display: inline-flex;
  width: 12px;
  font-size: 0.7rem;
  color: var(--text-m, rgba(255, 255, 255, 0.55));
  transition: transform 0.12s ease;
}
.dfx-caret--open { transform: rotate(90deg); }
.dfx-caret--empty { visibility: hidden; }
</style>
