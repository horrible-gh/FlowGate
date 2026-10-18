import { readdirSync, readFileSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESIGNER_CONTEXT_MENU, FIRST_STEP_COMMANDS } from '@/features/designer/contextMenu/attach/designerMenuConnector'
import { firstConnected } from './contextMenuFixtures.0060'

const root = resolve(__dirname, '../../../src')
const partRoot = join(root, 'features/designer/contextMenu')
const attachRoot = join(partRoot, 'attach')
const rel = (path: string) => relative(root, path).replace(/\\/g, '/')
const MENU_KEY = "Symbol.for('flowgate.designer.contextMenu')"
// flowgate.v02.0060 T#2 (0006-D §4.2): the five areas reach the menu only through inject of this key.
const AREA_FILES = [
  'features/designer/center/FlowTabStrip.vue',
  'features/designer/center/canvas/FlowCanvas.vue',
  'features/designer/fileExplorer/DesignerFileExplorer.vue',
  'features/designer/palette/PalettePanel.vue',
  'features/designer/projectExplorer/ProjectExplorer.vue',
]
const files = readdirSync(partRoot).filter((name) => /\.(ts|vue)$/.test(name))
const importsOf = (source: string) => [...source.matchAll(/(?:import|export)\s+(?:[^'";]*?\s+from\s*)?['"]([^'"]+)['"]/g)].map((m) => m[1])

describe('independent Designer context menu boundaries (0060)', () => {
  it('depends only on local parts, Vue, i18n and existing overlay helpers', () => {
    for (const file of files) {
      const source = readFileSync(join(partRoot, file), 'utf8')
      for (const specifier of importsOf(source)) expect(specifier, file).toMatch(
        /^(?:vue|vue-i18n|\.\/[A-Za-z]+(?:\.vue)?|\.\.\/\.\.\/\.\.\/core\/overlay\/(?:zIndex|focusReturn))$/,
      )
      expect(source, file).not.toMatch(/\b(?:fetch|XMLHttpRequest|WebSocket|defineStore|localStorage|sessionStorage)\b|\bimport\s*\(/)
    }
  })
  // flowgate.v02.0060 T#2: attached (2단계-A). Only the Designer Shell imports the menu (its view and the connector);
  // the five areas inject it and import nothing of it, so mounted elsewhere they keep their old behaviour.
  it('is installed by the Designer Shell only; the five areas reach it through inject and import nothing of it', () => {
    const injecting: string[] = []
    function visit(directory: string) {
      for (const item of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, item.name)
        if (path === partRoot) continue
        if (item.isDirectory()) visit(path)
        else if (/\.(vue|ts|tsx)$/.test(item.name)) {
          const source = readFileSync(path, 'utf8')
          const specifiers = importsOf(source).filter((specifier) => /(?:^|\/)contextMenu(?:\/|$)/.test(specifier))
          if (rel(path) === 'features/designer/shell/DesignerShell.vue') {
            expect(specifiers.sort()).toEqual(['../contextMenu/DesignerContextMenu.vue', '../contextMenu/attach/designerMenuConnector'])
            expect(source.match(/<DesignerContextMenu\b/g)).toHaveLength(1)
          } else {
            expect(specifiers, path).toEqual([])
            expect(source, path).not.toMatch(/<DesignerContextMenu\b/)
          }
          expect(source, path).not.toMatch(/\battachRightButtonGuard\s*\(/)
          if (source.includes(MENU_KEY)) injecting.push(rel(path))
        }
      }
    }
    visit(root)
    expect(injecting.sort()).toEqual(AREA_FILES)
    expect(DESIGNER_CONTEXT_MENU).toBe(Symbol.for('flowgate.designer.contextMenu'))
  })
  it('keeps the connectors on the Center session, Graph rules and the Palette catalog — no store, API or transport', () => {
    const files = readdirSync(attachRoot).filter((name) => /\.(ts|vue)$/.test(name))
    expect(files).toEqual(['designerMenuConnector.ts'])
    for (const file of files) {
      const source = readFileSync(join(attachRoot, file), 'utf8')
      for (const specifier of importsOf(source)) expect(specifier, file).toMatch(
        /^(?:vue|vue-i18n|\.\.\/[A-Za-z]+|\.\.\/\.\.\/center\/(?:designerCenterSession|graph\/flowGraph|graph\/edgeProgress|run\/flowRunLock|run\/flowCardMenu)|\.\.\/\.\.\/palette\/paletteCatalog)$/,
      )
      expect(source, file).not.toMatch(/\b(?:fetch|XMLHttpRequest|WebSocket|defineStore|localStorage|sessionStorage|postRequest|getRequest)\b|\bimport\s*\(/)
      // Git 되돌리기 / Flow 폐기 have no path here: no Base or dispose route is reachable from the menu.
      expect(source, file).not.toMatch(/dispose\(|base-revert|revertAll|untracked/)
    }
  })
  it('connects exactly the 11 first-step commands (§5.3 1차 연결); 2-B, DEFERRED and Git 되돌리기 stay hidden', () => {
    expect([...FIRST_STEP_COMMANDS].sort()).toEqual([...firstConnected].sort())
  })
  it('reuses overlay focus/z-index and theme variables with locally owned translations', () => {
    const host = readFileSync(join(partRoot, 'host.ts'), 'utf8')
    expect(host).toContain('core/overlay/focusReturn')
    expect(host).toContain('core/overlay/zIndex')
    const view = readFileSync(join(partRoot, 'DesignerContextMenu.vue'), 'utf8')
    expect(view).toContain("useScope: 'local'")
    expect(view).toContain('inheritLocale: true')
    const css = view.split('<style scoped>')[1]
    expect(css).toContain('var(--fg2-')
    expect(css).not.toMatch(/#[\da-f]{3,8}\b|rgba?\(|hsla?\(/i)
  })
  it('uses only theme tokens that app/styles/theme.css defines', () => {
    const theme = readFileSync(join(root, 'app/styles/theme.css'), 'utf8')
    const defined = [...theme.matchAll(/(--fg2-[\w-]+)\s*:/g)].map((m) => m[1])
    const view = readFileSync(join(partRoot, 'DesignerContextMenu.vue'), 'utf8')
    const used = [...view.matchAll(/var\((--fg2-[\w-]+)/g)].map((m) => m[1])
    expect(used.length).toBeGreaterThan(0)
    for (const token of used) expect(defined, token).toContain(token)
  })
})
