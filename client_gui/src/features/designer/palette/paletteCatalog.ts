// flowgate.v02.0030 — Palette model (R0001 §2): the categories, the default items of
// MirageGlass 03-designer-shell, the add-dialog choices, user-defined item creation and the
// select / drag intents the Palette hands to the Designer. Pure TypeScript, no screen.
//
// Reuse, not a parallel structure (R0001 §3): an item's `nodeKind` is the open Node kind key
// of the Graph model (center/graph/flowGraph.ts `FlowGraphNode.kind`); the default items use
// the known keys of center/canvas/flowNodeKinds.ts. `docType` is the FlowGate document type
// code (D / P / L / DB / N / WP / T / TS) a default item stands for, so the four 설계 items
// that share the 'design' kind stay distinguishable. User-defined items have no document type
// and use their own id as an open kind key — the Canvas draws an unknown kind with the default
// look until the Designer gives it one.
//
// Persistence of user-defined items is not decided here: no existing settings / Node
// definition contract holds them (the per-user UI settings are a closed field list), so the
// Palette only emits them and whoever mounts it may keep them (PalettePanel v-model).

import { flowNodeKindDisplay, type FlowNodeKindKey } from '../center/canvas/flowNodeKinds'
import { DEFAULT_PALETTE_ICON, isPaletteIconChoice, type PaletteIconKey } from './paletteIcons'

export const PALETTE_CATEGORIES = ['design', 'workflow', 'control'] as const
export type PaletteCategoryKey = (typeof PALETTE_CATEGORIES)[number]

/** Category heading glyph (03-designer-shell `.pal-title`). */
export const PALETTE_CATEGORY_GLYPHS: Record<PaletteCategoryKey, string> = {
  design: '📐',
  workflow: '⚡',
  control: '⚙',
}

/** Add-dialog colors, in the deck's order. The key names the i18n label. */
export const PALETTE_COLOR_CHOICES = [
  { key: 'blue', value: '#5b8def' },
  { key: 'green', value: '#34c8a2' },
  { key: 'purple', value: '#9b72ff' },
  { key: 'orange', value: '#ff9d51' },
  { key: 'pink', value: '#ec4899' },
  { key: 'teal', value: '#15c9d4' },
] as const

export const DEFAULT_PALETTE_COLOR = PALETTE_COLOR_CHOICES[0].value

export const CUSTOM_PALETTE_ID_PREFIX = 'custom.'

interface PaletteItemBase {
  /** Unique within the Palette. */
  readonly id: string
  readonly category: PaletteCategoryKey
  /** Open Node kind key (FlowGraphNode.kind). */
  readonly nodeKind: string
  /** FlowGate document type code of a default item; null for a user-defined item. */
  readonly docType: string | null
  readonly icon: PaletteIconKey | string
  /** Card accent (03-designer-shell `--c`). */
  readonly accent: string
}

export interface BuiltinPaletteItem extends PaletteItemBase {
  readonly custom: false
  /** Key under gui.designer.palette.items. */
  readonly labelKey: string
}

export interface CustomPaletteItem extends PaletteItemBase {
  readonly custom: true
  /** User-entered name, shown as is. */
  readonly label: string
}

export type PaletteItem = BuiltinPaletteItem | CustomPaletteItem

type KnownNodeKind = Exclude<FlowNodeKindKey, 'unknown'>

function builtin(
  id: string,
  category: PaletteCategoryKey,
  nodeKind: KnownNodeKind,
  docType: string | null,
  icon: PaletteIconKey,
  accent: string,
): BuiltinPaletteItem {
  return { id, category, nodeKind, docType, icon, accent, custom: false, labelKey: id }
}

/** flowgate.v02.0045 T0019: every 설계 card is purple — the Canvas color of the 'design' kind. */
const DESIGN_ACCENT = flowNodeKindDisplay('design').accent

/** The default Palette of 03-designer-shell, in display order. */
export const DEFAULT_PALETTE_ITEMS: readonly BuiltinPaletteItem[] = [
  builtin('basic_design', 'design', 'design', 'D', 'monitor', DESIGN_ACCENT),
  builtin('protocol', 'design', 'design', 'P', 'flow', DESIGN_ACCENT),
  builtin('logic', 'design', 'design', 'L', 'logic', DESIGN_ACCENT),
  builtin('db_design', 'design', 'design', 'DB', 'database', DESIGN_ACCENT),
  builtin('research', 'workflow', 'research', 'N', 'search', '#22c98c'),
  builtin('plan', 'workflow', 'plan', 'WP', 'clipboard', '#15c9d4'),
  builtin('task', 'workflow', 'task', 'T', 'pencil', '#f59e0b'),
  builtin('test', 'workflow', 'test', 'TS', 'test', '#ec4899'),
  builtin('confirm', 'control', 'approval', null, 'user', '#22c98c'),
]

export function isPaletteCategory(value: string): value is PaletteCategoryKey {
  return (PALETTE_CATEGORIES as readonly string[]).includes(value)
}

export interface PaletteSection {
  category: PaletteCategoryKey
  items: PaletteItem[]
}

/** Every category in its fixed order — also an empty one — each with its items in order. */
export function groupPaletteItems(items: readonly PaletteItem[]): PaletteSection[] {
  return PALETTE_CATEGORIES.map((category) => ({
    category,
    items: items.filter((item) => item.category === category),
  }))
}

export interface CustomPaletteInput {
  category: string
  name: string
  icon: string
  color: string
}

export type CustomPaletteError = 'name_required' | 'invalid_category'

/**
 * Builds a user-defined item, or the reason it cannot be built. The name is trimmed; an icon
 * outside the picker and a color outside the dialog's choices fall back to the defaults.
 * The id is `custom.<n>` with n one above the highest id already taken.
 */
export function createCustomPaletteItem(
  input: CustomPaletteInput,
  existing: readonly PaletteItem[],
): { ok: true; item: CustomPaletteItem } | { ok: false; error: CustomPaletteError } {
  const label = input.name.trim()
  if (!label) return { ok: false, error: 'name_required' }
  if (!isPaletteCategory(input.category)) return { ok: false, error: 'invalid_category' }
  const color = PALETTE_COLOR_CHOICES.some((c) => c.value === input.color) ? input.color : DEFAULT_PALETTE_COLOR
  const id = `${CUSTOM_PALETTE_ID_PREFIX}${nextCustomNumber(existing)}`
  return {
    ok: true,
    item: {
      id,
      category: input.category,
      nodeKind: id,
      docType: null,
      icon: isPaletteIconChoice(input.icon) ? input.icon : DEFAULT_PALETTE_ICON,
      accent: color,
      custom: true,
      label,
    },
  }
}

function nextCustomNumber(existing: readonly PaletteItem[]): number {
  let max = 0
  for (const item of existing) {
    if (!item.id.startsWith(CUSTOM_PALETTE_ID_PREFIX)) continue
    const n = Number(item.id.slice(CUSTOM_PALETTE_ID_PREFIX.length))
    if (Number.isInteger(n) && n > max) max = n
  }
  return max + 1
}

// ── intents handed to the Designer (R0001 §2.3) ───────────────────────────────────────────

/**
 * What the Designer receives when an item is selected or starts a drag: the item's definition
 * with its display name resolved. Canvas coordinates, the drop position and Node creation stay
 * with the Designer / Canvas.
 */
export interface PaletteItemDefinition {
  itemId: string
  category: PaletteCategoryKey
  nodeKind: string
  docType: string | null
  label: string
  icon: string
  accent: string
  custom: boolean
}

export function paletteItemDefinition(item: PaletteItem, label: string): PaletteItemDefinition {
  return {
    itemId: item.id,
    category: item.category,
    nodeKind: item.nodeKind,
    docType: item.docType,
    label,
    icon: item.icon,
    accent: item.accent,
    custom: item.custom,
  }
}

/** dataTransfer type carrying a PaletteItemDefinition as JSON. */
export const PALETTE_DRAG_MIME = 'application/x-flowgate-palette-item'

export function writePaletteDragData(transfer: DataTransfer, definition: PaletteItemDefinition): void {
  transfer.setData(PALETTE_DRAG_MIME, JSON.stringify(definition))
  transfer.setData('text/plain', definition.label)
  transfer.effectAllowed = 'copy'
}

/** The definition a Palette drag carries, or null when the drag did not come from a Palette. */
export function readPaletteDragData(transfer: Pick<DataTransfer, 'getData'> | null): PaletteItemDefinition | null {
  const raw = transfer?.getData(PALETTE_DRAG_MIME)
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<PaletteItemDefinition> | null
    if (
      !value ||
      typeof value.itemId !== 'string' ||
      typeof value.nodeKind !== 'string' ||
      typeof value.label !== 'string' ||
      typeof value.category !== 'string' ||
      !isPaletteCategory(value.category)
    ) {
      return null
    }
    return {
      itemId: value.itemId,
      category: value.category,
      nodeKind: value.nodeKind,
      docType: typeof value.docType === 'string' ? value.docType : null,
      label: value.label,
      icon: typeof value.icon === 'string' ? value.icon : DEFAULT_PALETTE_ICON,
      accent: typeof value.accent === 'string' ? value.accent : DEFAULT_PALETTE_COLOR,
      custom: value.custom === true,
    }
  } catch {
    return null
  }
}

/**
 * flowgate.v02.0088 F13: the definition a new node is made from, at the moment it is placed. A
 * selection, a drag or a context-menu entry may have been made in another language, so a default
 * item's label is built again from its item id in the language of the placing moment; a
 * user-defined item keeps its own words. Existing Graph labels are never touched by this.
 */
export function placedPaletteDefinition(
  definition: PaletteItemDefinition,
  translate: (key: string) => string,
): PaletteItemDefinition {
  if (definition.custom) return definition
  const item = DEFAULT_PALETTE_ITEMS.find((candidate) => candidate.id === definition.itemId)
  return item ? { ...definition, label: translate(`gui.designer.palette.items.${item.labelKey}`) } : definition
}

/**
 * flowgate.v02.0045 S3 (P0008 §5.1): the Graph node a dropped item stands for. A default item with
 * a document type is a document node; the `confirm` card is a gate node; a user-defined item is a
 * custom node. The node kind for drawing is the item's own kind key.
 */
export function paletteNodeSpec(definition: PaletteItemDefinition): {
  nodeType: 'document' | 'gate' | 'custom'
  docType: string | null
  paletteItemId: string
  label: string
  kind: string
} {
  const nodeType = definition.docType ? 'document' : definition.custom ? 'custom' : 'gate'
  return {
    nodeType,
    docType: nodeType === 'document' ? definition.docType : null,
    paletteItemId: definition.itemId,
    label: definition.label,
    kind: definition.nodeKind,
  }
}
