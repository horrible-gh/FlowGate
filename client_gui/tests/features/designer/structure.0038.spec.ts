import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'
import { DESIGNER_DENSITIES, DESIGNER_MENUS, DESIGNER_SEARCH_EXAMPLES, DESIGNER_SEARCH_SCOPES } from '@/features/designer/top/designerTop'

// flowgate.v02.0038 — source-level guards for the Designer top band (0001-R rev2 §5.2 / §6,
// 0007-T §8) that a DOM test cannot see: UI parts with event boundaries, no feature, API or
// shortcut behind them.
const SRC = resolve(__dirname, '../../../src')
const TOP = join(SRC, 'features/designer/top')
const TOP_FILES = readdirSync(TOP).sort()

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

describe('features/designer/top structure (flowgate.v02.0038)', () => {
  it('holds exactly the Header, its search, the Menu Bar, the Toolbar and the model', () => {
    expect(TOP_FILES).toEqual(
      ['DesignerHeader.vue', 'DesignerMenuBar.vue', 'DesignerToolbar.vue', 'GlobalSearch.vue', 'designerTop.ts'].sort(),
    )
  })

  it('imports only vue / vue-i18n, its own files and the current-user read of core/auth', () => {
    const allowed = [/^vue$/, /^vue-i18n$/, /^\.\/[A-Za-z]+(\.vue)?$/, /^@\/core\/auth\/currentUser$/]
    for (const name of TOP_FILES) {
      for (const spec of importsOf(read(join(TOP, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
    expect(importsOf(read(join(TOP, 'designerTop.ts')))).toEqual([])
  })

  it('owns no store, API, router, Center session, Shell container or other feature part', () => {
    for (const name of TOP_FILES) {
      const src = read(join(TOP, name))
      for (const spec of importsOf(src)) {
        expect(spec, name).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\/|@vue-flow|\/shell\/|\/center\/|@\/features\//)
      }
      expect(src, name).not.toMatch(/defineStore|sessionStorage|fetch\(|getRequest|postRequest/)
    }
  })

  it('writes only the shared preferred_locale key, from the Header language switch', () => {
    for (const name of TOP_FILES) {
      const src = read(join(TOP, name))
      const writes = [...src.matchAll(/localStorage\.(\w+)\(([^)]*)\)/g)].map((m) => `${m[1]}(${m[2]})`)
      expect(writes, name).toEqual(name === 'DesignerHeader.vue' ? ["setItem('preferred_locale', code)"] : [])
    }
  })

  it('binds no keyboard shortcut: only Enter / Escape / arrow keys inside the open parts', () => {
    for (const name of TOP_FILES) {
      const src = read(join(TOP, name))
      expect(src, name).not.toMatch(/ctrlKey|metaKey|altKey|shiftKey|window\.addEventListener\('keydown'|document\.addEventListener\('keydown'/)
      expect(src, name).not.toMatch(/Ctrl|F5|Shift\+/)
    }
  })

  it('has no AI menu, no Save item and no select / add node / connect tool', () => {
    expect(DESIGNER_MENUS.map((menu) => menu.key)).toEqual(['file', 'edit', 'run', 'view', 'window', 'help'])
    const commands = DESIGNER_MENUS.flatMap((menu) => menu.entries).flatMap((entry) => (entry.kind === 'command' ? [entry.command] : []))
    expect(commands.filter((command) => /save|^ai\./.test(command))).toEqual([])
    // Every Toolbar label comes from `top.tools`, which has no select / add node / connect entry.
    const toolbar = read(join(TOP, 'DesignerToolbar.vue'))
    const toolKeys = [...toolbar.matchAll(/gui\.designer\.top\.tools\.(\w+)/g)].map((m) => m[1])
    expect(toolKeys.filter((key) => /select|node|connect/.test(key))).toEqual([])
    expect(toolbar).not.toMatch(/data-command="(?!edit\.|view\.)/)
  })

  // jsdom has no layout, so the Header width rules (checked in a real browser at 1024-1920px)
  // are pinned at source level: the side groups may not be squeezed under the search.
  it('keeps the Header side groups at content width and compacts them on narrow windows', () => {
    const header = read(join(TOP, 'DesignerHeader.vue'))
    expect(header).toContain(
      'grid-template-columns: minmax(max-content, 1fr) minmax(220px, 680px) minmax(max-content, 1fr);',
    )
    const compact = header.slice(header.indexOf('@media (max-width: 1199px)'))
    expect(compact).toMatch(/\.designer-header-label \{[^}]*clip: rect\(0 0 0 0\)/)
    expect(compact).not.toMatch(/display: none/)
    expect(header.match(/class="designer-header-label"/g)).toHaveLength(2)
  })

  it('is not part of the features/designer public surface', () => {
    for (const name of ['DesignerHeader', 'DesignerMenuBar', 'DesignerToolbar', 'GlobalSearch']) {
      expect(Object.keys(DesignerFeature)).not.toContain(name)
    }
  })

  it.each(['ko', 'en', 'ja'])('i18n %s top keys cover every scope, example, density and menu item', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const top = messages.designer.top
    expect(Object.keys(top.search.scopes)).toEqual([...DESIGNER_SEARCH_SCOPES])
    expect(Object.keys(top.search.placeholders)).toEqual([...DESIGNER_SEARCH_SCOPES])
    expect(Object.keys(top.search.examples)).toEqual([...DESIGNER_SEARCH_EXAMPLES])
    expect(Object.keys(top.search.example_values)).toEqual([...DESIGNER_SEARCH_EXAMPLES])
    expect(Object.keys(top.densities)).toEqual([...DESIGNER_DENSITIES])
    expect(Object.keys(top.menus)).toEqual(DESIGNER_MENUS.map((menu) => menu.key))
    for (const entry of DESIGNER_MENUS.flatMap((menu) => menu.entries)) {
      if (entry.kind === 'command' || entry.kind === 'recent' || entry.kind === 'density') {
        expect(top.items, entry.label).toHaveProperty(entry.label)
      }
    }
    expect(messages.designer.shell).not.toHaveProperty('top_label')
    expect(messages.designer.shell).not.toHaveProperty('top_parts')
  })
})
