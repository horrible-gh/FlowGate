import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'
import { INSPECTOR_TABS } from '@/features/designer/inspector/inspectorTabs'

// flowgate.v02.0036 — source-level guards for the bottom Inspector Frame (0036-R §2.2 / §2.4 /
// §5.2) that a DOM test cannot see: a frame only, with no feature, API or state machine behind it.
const SRC = resolve(__dirname, '../../../src')
const INSPECTOR = join(SRC, 'features/designer/inspector')
const INSPECTOR_FILES = readdirSync(INSPECTOR).sort()

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

describe('features/designer/inspector structure (flowgate.v02.0036)', () => {
  it('holds exactly the Frame, its header and the tab rules', () => {
    expect(INSPECTOR_FILES).toEqual(['DesignerInspector.vue', 'InspectorHeader.vue', 'inspectorTabs.ts'].sort())
  })

  it('imports only vue / vue-i18n, ui primitives, its own files and the Canvas Node kind / Graph Model contracts', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/center\/canvas\/flowNodeKinds$/,
      /^\.\.\/center\/graph\/flowGraph$/,
    ]
    for (const name of INSPECTOR_FILES) {
      for (const spec of importsOf(read(join(INSPECTOR, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('keeps the tab rules free of screens', () => {
    expect(importsOf(read(join(INSPECTOR, 'inspectorTabs.ts')))).toEqual([])
  })

  it('owns no store, API, persistence, router, Vue Flow, Center session or feature part', () => {
    for (const name of INSPECTOR_FILES) {
      const src = read(join(INSPECTOR, name))
      for (const spec of importsOf(src)) {
        expect(spec, name).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\/|@vue-flow|\/shell\/|designerCenterSession|@\/features\//)
      }
      expect(src, name).not.toMatch(/defineStore|localStorage|sessionStorage|fetch\(|ModelessShell/)
      expect(src, name).not.toMatch(/GitPanel|ConversationPanel|PalettePanel/)
    }
  })

  it('keeps every tab on screen at a narrow width: tabs shrink to the minimum, the strip never wraps', () => {
    const style = read(join(INSPECTOR, 'DesignerInspector.vue')).split('<style scoped>')[1] ?? ''
    const rule = (selector: string) => {
      const at = style.indexOf(`${selector} {`)
      expect(at, selector).toBeGreaterThanOrEqual(0)
      return style.slice(at, style.indexOf('}', at))
    }
    const strip = rule('.designer-inspector-tabs')
    expect(strip).toMatch(/flex-wrap:\s*nowrap;/)
    expect(strip).toMatch(/min-width:\s*0;/)
    // Gap and side padding come from tabStripStyle, not from a CSS literal that could drift.
    expect(strip).not.toMatch(/\bgap:|\bpadding:/)
    const tab = rule('.designer-inspector-tabs :deep(.ui-tab)')
    expect(tab).toMatch(/flex:\s*0 1 auto;/)
    expect(tab).toMatch(/min-width:\s*var\(--inspector-tab-min-width\);/)
    expect(tab).toMatch(/box-sizing:\s*border-box;/)
    expect(tab).toMatch(/text-overflow:\s*ellipsis;/)
    expect(tab).toMatch(/white-space:\s*nowrap;/)
  })

  it('is not part of the features/designer public surface', () => {
    expect(Object.keys(DesignerFeature)).not.toContain('DesignerInspector')
  })

  it.each(['ko', 'en', 'ja'])('i18n %s Inspector keys match the tab list and the Bottom placeholder strings are gone', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const inspector = messages.designer.inspector
    expect(Object.keys(inspector).sort()).toEqual(['label', 'pane_placeholder', 'tabs', 'tabs_label'].sort())
    expect(Object.keys(inspector.tabs)).toEqual([...INSPECTOR_TABS])
    expect(inspector.pane_placeholder).toContain('{tab}')
    expect(messages.designer.shell).not.toHaveProperty('bottom_label')
    expect(messages.designer.shell).not.toHaveProperty('bottom_parts')
    expect(messages.designer.shell.minimap_label).toBe('MINIMAP')
  })
})
