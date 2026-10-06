<template>
  <!-- flowgate.v02.0030 — Designer Palette part (R0001, MirageGlass 03-designer-shell right
       Palette). It owns only its own content: the category sections, the cards and the add
       dialog. The surrounding Tool Panel (팔레트 / 대화 / Git tabs, header, collapse, width) mounts
       it and owns all of that chrome (R0001 §4). It never drops, places or creates a Node: it
       hands the Designer a select intent and a drag intent (R0001 §2.3). -->
  <section class="designer-palette" data-test="designer-palette" :aria-label="t('gui.designer.palette.label')">
    <div
      v-for="(section, index) in sections"
      :key="section.category"
      class="designer-palette-section"
      data-test="designer-palette-section"
      :data-category="section.category"
    >
      <div class="designer-palette-title">
        <span class="designer-palette-title-text" data-test="designer-palette-section-title">
          {{ PALETTE_CATEGORY_GLYPHS[section.category] }} {{ t(`gui.designer.palette.categories.${section.category}`) }}
        </span>
        <!-- The one add entry point sits on the first heading, as in 03-designer-shell. -->
        <button
          v-if="index === 0"
          type="button"
          class="designer-palette-add"
          :title="t('gui.designer.palette.add')"
          :aria-label="t('gui.designer.palette.add')"
          data-test="designer-palette-add"
          @click="addOpen = true"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" />
          </svg>
        </button>
      </div>
      <div class="designer-palette-grid" data-test="designer-palette-grid">
        <PaletteCard
          v-for="item in section.items"
          :key="item.id"
          :item="item"
          :label="labelOf(item)"
          :selected="selectedId === item.id"
          @select="onSelect(item)"
          @dragstart="onDragStart(item, $event)"
        />
      </div>
    </div>

    <PaletteAddDialog :open="addOpen" :existing="allItems" @close="addOpen = false" @add="onAdd" />
  </section>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import PaletteAddDialog from './PaletteAddDialog.vue'
import PaletteCard from './PaletteCard.vue'
import {
  DEFAULT_PALETTE_ITEMS,
  PALETTE_CATEGORY_GLYPHS,
  groupPaletteItems,
  paletteItemDefinition,
  writePaletteDragData,
  type BuiltinPaletteItem,
  type CustomPaletteItem,
  type PaletteItem,
  type PaletteItemDefinition,
} from './paletteCatalog'

const props = withDefaults(
  defineProps<{
    /** Default items; 03-designer-shell's Palette unless the Designer supplies its own. */
    items?: readonly BuiltinPaletteItem[]
    /**
     * User-defined items (v-model:custom-items). Left unbound, the Palette keeps the items
     * added in this session itself; bound, the parent owns — and may persist — the list.
     */
    customItems?: readonly CustomPaletteItem[]
  }>(),
  { items: () => DEFAULT_PALETTE_ITEMS, customItems: undefined },
)

const emit = defineEmits<{
  /** An item was selected (clicked or activated by keyboard). */
  select: [definition: PaletteItemDefinition]
  /** A Node drag started from an item; the DataTransfer also carries the definition. */
  'drag-start': [definition: PaletteItemDefinition, event: DragEvent]
  /** A user-defined item was added through the add dialog. */
  add: [item: CustomPaletteItem]
  'update:customItems': [items: CustomPaletteItem[]]
}>()

const { t } = useI18n()

const localCustomItems = ref<CustomPaletteItem[]>([])
const customList = computed<readonly CustomPaletteItem[]>(() => props.customItems ?? localCustomItems.value)
const allItems = computed<PaletteItem[]>(() => [...props.items, ...customList.value])
const sections = computed(() => groupPaletteItems(allItems.value))

const selectedId = ref<string | null>(null)
const addOpen = ref(false)

function labelOf(item: PaletteItem): string {
  return item.custom ? item.label : t(`gui.designer.palette.items.${item.labelKey}`)
}

function definitionOf(item: PaletteItem): PaletteItemDefinition {
  return paletteItemDefinition(item, labelOf(item))
}

function onSelect(item: PaletteItem) {
  selectedId.value = item.id
  emit('select', definitionOf(item))
}

function onDragStart(item: PaletteItem, event: DragEvent) {
  const definition = definitionOf(item)
  if (event.dataTransfer) writePaletteDragData(event.dataTransfer, definition)
  emit('drag-start', definition, event)
}

function onAdd(item: CustomPaletteItem) {
  const next = [...customList.value, item]
  if (props.customItems === undefined) localCustomItems.value = next
  addOpen.value = false
  emit('update:customItems', next)
  emit('add', item)
}
</script>

<style scoped>
.designer-palette {
  box-sizing: border-box;
  min-width: 0;
  padding: 11px;
  color: var(--fg2-text);
  font-family: var(--fg2-font);
}

.designer-palette-title {
  display: flex;
  align-items: center;
  min-height: 24px;
  margin: 14px 2px 7px;
}

.designer-palette-section:first-child .designer-palette-title {
  margin-top: 0;
}

.designer-palette-title-text {
  min-width: 0;
  overflow: hidden;
  color: #758ca6;
  font-size: 10px;
  font-weight: 800;
  letter-spacing: 0.7px;
  text-overflow: ellipsis;
  text-transform: uppercase;
  white-space: nowrap;
}

.designer-palette-add {
  display: grid;
  flex: none;
  place-items: center;
  width: 24px;
  height: 24px;
  margin-left: auto;
  padding: 0;
  border: 1px solid #2b4c6b;
  border-radius: 7px;
  background: #10233a;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.035);
  color: #79aee0;
  cursor: pointer;
  transition:
    background 0.14s ease,
    border-color 0.14s ease,
    color 0.14s ease,
    transform 0.12s ease;
}

.designer-palette-add svg {
  display: block;
  width: 13px;
  height: 13px;
}

.designer-palette-add:hover {
  border-color: #3f6f9c;
  background: #163150;
  color: #d7ebff;
}

.designer-palette-add:active {
  transform: scale(0.94);
}

.designer-palette-add:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

/* Two compact columns fit the right panel width (03-designer-shell `.pal-grid`). */
.designer-palette-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  grid-auto-rows: 52px;
  gap: 7px;
}
</style>
