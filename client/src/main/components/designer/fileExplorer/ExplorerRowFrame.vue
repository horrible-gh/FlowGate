<template>
  <div
    class="dfx-row"
    :class="{ 'dfx-row--selected': selected, 'dfx-row--muted': muted }"
    role="treeitem"
    :aria-selected="selected"
    :aria-level="depth + 1"
    tabindex="0"
    :style="{ paddingLeft: `${8 + depth * indent}px` }"
  >
    <span class="dfx-row-lead"><slot name="lead" /></span>
    <span class="dfx-row-main"><slot /></span>
    <span v-if="$slots.trail" class="dfx-row-trail"><slot name="trail" /></span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0003 — the thin shared row frame (0024-D §8.1): height, depth
// indentation, hover/selected/focus styling and the lead/main/trail slot layout.
// It knows nothing about files or folders; FolderRow/FileRow give it meaning.
// Native listeners (click, dblclick, keydown, contextmenu) fall through to the root.
withDefaults(defineProps<{ depth: number; selected?: boolean; muted?: boolean; indent?: number }>(), {
  selected: false,
  muted: false,
  indent: 14,
})
</script>

<style scoped>
.dfx-row {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding-right: 8px;
  font-size: 0.8rem;
  color: var(--text-p, rgba(255, 255, 255, 0.85));
  cursor: pointer;
  user-select: none;
  outline: none;
}
.dfx-row:hover { background: rgba(255, 255, 255, 0.06); }
.dfx-row:focus-visible { box-shadow: inset 0 0 0 1px rgba(96, 165, 250, 0.7); }
.dfx-row--selected { background: rgba(59, 130, 246, 0.22); }
.dfx-row--muted { opacity: 0.72; }
.dfx-row-lead { display: inline-flex; align-items: center; gap: 4px; flex: none; }
.dfx-row-main { display: inline-flex; align-items: center; gap: 6px; flex: 1 1 auto; min-width: 0; }
.dfx-row-trail { display: inline-flex; align-items: center; gap: 4px; flex: none; margin-left: auto; }
</style>
