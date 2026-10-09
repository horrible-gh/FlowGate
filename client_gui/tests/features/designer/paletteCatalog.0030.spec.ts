import { describe, expect, it } from 'vitest'
import { FLOW_NODE_KINDS } from '@/features/designer/center/canvas/flowNodeKinds'
import {
  DEFAULT_PALETTE_COLOR,
  DEFAULT_PALETTE_ITEMS,
  PALETTE_CATEGORIES,
  PALETTE_DRAG_MIME,
  createCustomPaletteItem,
  groupPaletteItems,
  paletteItemDefinition,
  readPaletteDragData,
  writePaletteDragData,
  type CustomPaletteItem,
} from '@/features/designer/palette/paletteCatalog'
import { DEFAULT_PALETTE_ICON, PALETTE_ICON_CHOICES, paletteIconPath } from '@/features/designer/palette/paletteIcons'

// flowgate.v02.0030 — Palette model (R0001 §2): default items, grouping, user-defined item
// creation and the drag payload.

class FakeTransfer {
  data = new Map<string, string>()
  effectAllowed = 'none'
  setData(type: string, value: string) {
    this.data.set(type, value)
  }
  getData(type: string) {
    return this.data.get(type) ?? ''
  }
}

const custom = (n: number, category: CustomPaletteItem['category'] = 'design'): CustomPaletteItem => ({
  id: `custom.${n}`,
  category,
  nodeKind: `custom.${n}`,
  docType: null,
  icon: 'document',
  accent: DEFAULT_PALETTE_COLOR,
  custom: true,
  label: `c${n}`,
})

describe('Palette default items (03-designer-shell)', () => {
  it('lists the deck items per category in order', () => {
    expect(groupPaletteItems(DEFAULT_PALETTE_ITEMS).map((s) => [s.category, s.items.map((i) => i.id)])).toEqual([
      ['design', ['basic_design', 'protocol', 'logic', 'db_design']],
      ['workflow', ['research', 'plan', 'task', 'test']],
      ['control', ['confirm']],
    ])
  })

  it('reuses the known Node kinds of the Canvas and the FlowGate document type codes', () => {
    for (const item of DEFAULT_PALETTE_ITEMS) {
      expect(FLOW_NODE_KINDS as readonly string[]).toContain(item.nodeKind)
      expect(item.custom).toBe(false)
    }
    expect(DEFAULT_PALETTE_ITEMS.map((i) => [i.id, i.nodeKind, i.docType])).toEqual([
      ['basic_design', 'design', 'D'],
      ['protocol', 'design', 'P'],
      ['logic', 'design', 'L'],
      ['db_design', 'design', 'DB'],
      ['research', 'research', 'N'],
      ['plan', 'plan', 'WP'],
      ['task', 'task', 'T'],
      ['test', 'test', 'TS'],
      ['confirm', 'approval', null],
    ])
  })

  it('has unique ids and a drawable icon and accent on every item', () => {
    const ids = DEFAULT_PALETTE_ITEMS.map((i) => i.id)
    expect(new Set(ids).size).toBe(ids.length)
    for (const item of DEFAULT_PALETTE_ITEMS) {
      expect(paletteIconPath(item.icon)).not.toBe(paletteIconPath('__none__'))
      expect(item.accent).toMatch(/^#[0-9a-f]{6}$/)
    }
  })

  it('keeps an empty category as a section', () => {
    expect(groupPaletteItems([]).map((s) => [s.category, s.items.length])).toEqual(PALETTE_CATEGORIES.map((c) => [c, 0]))
  })
})

describe('Palette icons', () => {
  it('offers the 34 picker glyphs of the deck and falls back to document for unknown keys', () => {
    expect(PALETTE_ICON_CHOICES).toHaveLength(34)
    expect(new Set(PALETTE_ICON_CHOICES).size).toBe(34)
    for (const key of PALETTE_ICON_CHOICES) expect(paletteIconPath(key)).toMatch(/^M/)
    expect(paletteIconPath('nope')).toBe(paletteIconPath(DEFAULT_PALETTE_ICON))
    expect(paletteIconPath('constructor')).toBe(paletteIconPath(DEFAULT_PALETTE_ICON))
  })
})

describe('createCustomPaletteItem', () => {
  it('builds an item in the chosen category with a trimmed name', () => {
    const result = createCustomPaletteItem({ category: 'workflow', name: '  API 설계 ', icon: 'api', color: '#ec4899' }, DEFAULT_PALETTE_ITEMS)
    expect(result).toEqual({
      ok: true,
      item: {
        id: 'custom.1',
        category: 'workflow',
        nodeKind: 'custom.1',
        docType: null,
        icon: 'api',
        accent: '#ec4899',
        custom: true,
        label: 'API 설계',
      },
    })
  })

  it('refuses an empty or blank name and an unknown category', () => {
    expect(createCustomPaletteItem({ category: 'design', name: '   ', icon: 'api', color: '#ec4899' }, [])).toEqual({
      ok: false,
      error: 'name_required',
    })
    expect(createCustomPaletteItem({ category: 'git', name: 'x', icon: 'api', color: '#ec4899' }, [])).toEqual({
      ok: false,
      error: 'invalid_category',
    })
  })

  it('falls back to the default icon and color outside the dialog choices', () => {
    const result = createCustomPaletteItem({ category: 'control', name: 'x', icon: 'monitor', color: 'red' }, [])
    expect(result.ok && [result.item.icon, result.item.accent]).toEqual([DEFAULT_PALETTE_ICON, DEFAULT_PALETTE_COLOR])
  })

  it('takes the id above the highest custom id already taken', () => {
    const result = createCustomPaletteItem({ category: 'design', name: 'x', icon: 'api', color: DEFAULT_PALETTE_COLOR }, [
      ...DEFAULT_PALETTE_ITEMS,
      custom(1),
      custom(4),
    ])
    expect(result.ok && result.item.id).toBe('custom.5')
  })
})

describe('Palette drag payload', () => {
  it('round-trips the item definition through a DataTransfer', () => {
    const definition = paletteItemDefinition(DEFAULT_PALETTE_ITEMS[1], '프로토콜')
    const transfer = new FakeTransfer()
    writePaletteDragData(transfer as unknown as DataTransfer, definition)
    expect(transfer.effectAllowed).toBe('copy')
    expect(transfer.getData('text/plain')).toBe('프로토콜')
    expect(readPaletteDragData(transfer)).toEqual({
      itemId: 'protocol',
      category: 'design',
      nodeKind: 'design',
      docType: 'P',
      label: '프로토콜',
      icon: 'flow',
      // flowgate.v02.0045 T0019: every 설계 card is purple.
      accent: '#9b72ff',
      custom: false,
    })
  })

  it('returns null for a drag that does not carry a valid Palette definition', () => {
    const transfer = new FakeTransfer()
    expect(readPaletteDragData(null)).toBeNull()
    expect(readPaletteDragData(transfer)).toBeNull()
    transfer.setData(PALETTE_DRAG_MIME, '{not json')
    expect(readPaletteDragData(transfer)).toBeNull()
    transfer.setData(PALETTE_DRAG_MIME, JSON.stringify({ itemId: 'x', nodeKind: 'task', label: 'x', category: 'git' }))
    expect(readPaletteDragData(transfer)).toBeNull()
  })
})
