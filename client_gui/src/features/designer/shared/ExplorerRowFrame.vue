<template>
  <div
    class="dx-row"
    :class="{ 'dx-row--selected': selected, 'dx-row--muted': muted }"
    role="treeitem"
    :aria-selected="selected"
    :aria-level="depth + 1"
    tabindex="0"
    :style="{ paddingLeft: `${8 + depth * indent}px` }"
  >
    <span class="dx-row-lead"><slot name="lead" /></span>
    <span class="dx-row-main"><slot /></span>
    <span v-if="$slots.trail" class="dx-row-trail"><slot name="trail" /></span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0024 T0005 — the thin row frame shared by the Designer left-dock explorers
// (0024-D §8.1/§13): height, depth indentation, hover/selected/focus styling and the
// lead/main/trail slot layout. It knows nothing about files, folders, Groups or Flows; the
// meaning-specific rows (FolderRow/FileRow, and the Project Explorer rows) give it meaning.
// Native listeners (click, dblclick, keydown, contextmenu) and extra attributes such as
// aria-expanded fall through to the root element.
withDefaults(defineProps<{ depth: number; selected?: boolean; muted?: boolean; indent?: number }>(), {
  selected: false,
  muted: false,
  indent: 14,
})
</script>

<style scoped>
.dx-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding-right: 8px;
  border-radius: var(--fg2-control-radius-sm);
  font-size: 12.5px;
  color: var(--fg2-control-fg);
  cursor: pointer;
  user-select: none;
}

.dx-row:hover {
  background: rgba(255, 255, 255, 0.05);
  color: var(--fg2-text);
}

.dx-row:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

.dx-row--selected {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-text);
}

.dx-row--muted {
  opacity: 0.72;
}

.dx-row-lead {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
}

.dx-row-main {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: 1 1 auto;
  min-width: 0;
}

.dx-row-trail {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: none;
  margin-left: auto;
}
</style>
