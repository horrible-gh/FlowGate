import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'
import { DEFAULT_PALETTE_ITEMS, PALETTE_CATEGORIES, PALETTE_COLOR_CHOICES } from '@/features/designer/palette/paletteCatalog'
import { PALETTE_ICON_CHOICES } from '@/features/designer/palette/paletteIcons'

// flowgate.v02.0030 — source-level guards for the Palette part (R0001 §3, §4, §6) that a DOM
// test cannot see.
const SRC = resolve(__dirname, '../../../src')
const PALETTE = join(SRC, 'features/designer/palette')
const PALETTE_FILES = readdirSync(PALETTE).sort()

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

describe('features/designer/palette structure (flowgate.v02.0030)', () => {
  it('holds exactly the Palette part files', () => {
    expect(PALETTE_FILES).toEqual(
      ['PaletteAddDialog.vue', 'PaletteCard.vue', 'PalettePanel.vue', 'paletteCatalog.ts', 'paletteIcons.ts'].sort(),
    )
  })

  it('imports only vue / vue-i18n, ui primitives, its own files and the Canvas Node kind contract', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/center\/canvas\/flowNodeKinds$/,
    ]
    for (const name of PALETTE_FILES) {
      for (const spec of importsOf(read(join(PALETTE, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('keeps the model and the glyph table free of screens', () => {
    for (const name of ['paletteCatalog.ts', 'paletteIcons.ts']) {
      for (const spec of importsOf(read(join(PALETTE, name)))) expect(spec, name).not.toMatch(/\.vue$|^vue/)
    }
  })

  it('owns no store, API, persistence, router, Canvas drop or Tool Panel chrome', () => {
    for (const name of PALETTE_FILES) {
      const src = read(join(PALETTE, name))
      for (const spec of importsOf(src)) expect(spec, name).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\/|@vue-flow|shell|Explorer/)
      expect(src, name).not.toMatch(/defineStore|localStorage|sessionStorage|@drop\b|addEventListener\(['"]drop/)
      expect(src, name).not.toMatch(/role="tab"|ConversationPanel|GitPanel/)
    }
  })

  it('is not part of the features/designer public surface', () => {
    expect(Object.keys(DesignerFeature)).not.toContain('PalettePanel')
  })

  it.each(['ko', 'en', 'ja'])('i18n %s Palette keys match the model', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const palette = messages.designer.palette
    expect(Object.keys(palette.categories).sort()).toEqual([...PALETTE_CATEGORIES].sort())
    expect(Object.keys(palette.items).sort()).toEqual(DEFAULT_PALETTE_ITEMS.map((i) => i.labelKey).sort())
    expect(Object.keys(palette.icons).sort()).toEqual([...PALETTE_ICON_CHOICES].sort())
    expect(Object.keys(palette.colors).sort()).toEqual(PALETTE_COLOR_CHOICES.map((c) => c.key).sort())
  })
})
