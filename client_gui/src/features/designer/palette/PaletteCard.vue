<template>
  <!-- flowgate.v02.0030 — one compact Palette card (03-designer-shell `.pal-card`): a top accent
       bar, the kind-colored icon box and a one-line name. A button so it is reachable by
       keyboard; draggable as the Node drag source (R0001 §2.3). -->
  <button
    type="button"
    class="palette-card"
    :class="{ 'is-selected': selected }"
    :style="{ '--palette-accent': item.accent }"
    :title="label"
    :aria-pressed="selected"
    draggable="true"
    data-test="designer-palette-item"
    :data-item-id="item.id"
    :data-node-kind="item.nodeKind"
    :data-custom="item.custom ? 'true' : 'false'"
    @click="emit('select')"
    @dragstart="emit('dragstart', $event)"
  >
    <span class="palette-card-icon" aria-hidden="true">
      <svg viewBox="0 0 24 24">
        <path :d="iconPath" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </span>
    <span class="palette-card-label" data-test="designer-palette-item-label">{{ label }}</span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { PaletteItem } from './paletteCatalog'
import { paletteIconPath } from './paletteIcons'

const props = defineProps<{
  item: PaletteItem
  label: string
  selected: boolean
}>()

const emit = defineEmits<{
  select: []
  dragstart: [event: DragEvent]
}>()

const iconPath = computed(() => paletteIconPath(props.item.icon))
</script>

<style scoped>
.palette-card {
  position: relative;
  display: flex;
  align-items: center;
  gap: 9px;
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  height: 52px;
  padding: 7px 9px;
  overflow: hidden;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  background: #102039;
  color: inherit;
  font: inherit;
  text-align: left;
  cursor: grab;
  transition:
    border-color 0.14s ease,
    transform 0.12s ease;
}

.palette-card::before {
  content: '';
  position: absolute;
  top: 0;
  right: 0;
  left: 0;
  height: 3px;
  background: var(--palette-accent);
}

.palette-card:hover {
  border-color: var(--fg2-line-strong);
  transform: translateY(-1px);
}

.palette-card:active {
  cursor: grabbing;
}

.palette-card:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.palette-card.is-selected {
  border-color: color-mix(in srgb, var(--palette-accent) 70%, transparent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--palette-accent) 40%, transparent) inset;
}

.palette-card-icon {
  display: grid;
  flex: 0 0 26px;
  place-items: center;
  width: 26px;
  height: 26px;
  border: 1px solid color-mix(in srgb, var(--palette-accent) 34%, transparent);
  border-radius: 7px;
  background: color-mix(in srgb, var(--palette-accent) 13%, transparent);
  color: var(--palette-accent);
}

.palette-card-icon svg {
  display: block;
  width: 16px;
  height: 16px;
}

.palette-card-label {
  min-width: 0;
  overflow: hidden;
  color: #dce7f3;
  font-size: 11px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
