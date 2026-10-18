import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const root = resolve(__dirname, '../../../src')
const partRoot = join(root, 'features/designer/contextMenu')
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
  it('has no production screen attachment or imports outside its own folder', () => {
    function visit(directory: string) {
      for (const item of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, item.name)
        if (path === partRoot) continue
        if (item.isDirectory()) visit(path)
        else if (/\.(vue|ts|tsx)$/.test(item.name)) {
          const source = readFileSync(path, 'utf8')
          expect(importsOf(source).filter((specifier) => /(?:^|\/)contextMenu(?:\/|$)/.test(specifier)), path).toEqual([])
          expect(source, path).not.toMatch(/<DesignerContextMenu\b|\battachRightButtonGuard\s*\(/)
        }
      }
    }
    visit(root)
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
