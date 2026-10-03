import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// flowgate.v02.0025 — source-level guards for the Designer Shell (0003-D §2.3/§7.1) that a
// DOM test cannot see.
const SRC = resolve(__dirname, '../../../src')
const SHELL = join(SRC, 'features/designer/shell')

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])
const SHELL_FILES = readdirSync(SHELL).sort()

describe('features/designer/shell structure (flowgate.v02.0025)', () => {
  it('holds exactly the Shell containers, the placeholder content part and the layout state', () => {
    expect(SHELL_FILES).toEqual([
      'DesignerBody.vue',
      'DesignerBottomDock.vue',
      'DesignerCenterColumn.vue',
      'DesignerLeftDock.vue',
      'DesignerRightDock.vue',
      'DesignerShell.vue',
      'DesignerTop.vue',
      'DesignerWorkspace.vue',
      'ExplorerHeader.vue',
      'ExplorerModeTabs.vue',
      'ShellPlaceholder.vue',
      'designerLayout.ts',
    ].sort())
  })

  it('imports only vue / vue-i18n, the features/project surface, ui primitives, its own files and the two Explorer entry points', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^@\/features\/project$/,
      /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/projectExplorer\/ProjectExplorer\.vue$/,
      /^\.\.\/projectExplorer\/projectExplorerTree$/,
      /^\.\.\/fileExplorer\/DesignerFileExplorer\.vue$/,
    ]
    for (const name of SHELL_FILES) {
      for (const spec of importsOf(read(join(SHELL, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('never reaches Explorer internals (rows, trees, toolbars, adapters) or the designer index', () => {
    for (const name of SHELL_FILES) {
      for (const spec of importsOf(read(join(SHELL, name)))) {
        expect(spec, name).not.toMatch(/projectExplorer\/(?!ProjectExplorer\.vue$|projectExplorerTree$)/)
        expect(spec, name).not.toMatch(/fileExplorer\/(?!DesignerFileExplorer\.vue$)/)
        expect(spec, name).not.toMatch(/designer\/shared\//)
        expect(spec, name).not.toMatch(/^\.\.\/index$|^\.\.$|@\/features\/designer/)
      }
    }
  })

  it('owns no router, Pinia, API or persistence and does not reuse the self-sizing split primitives', () => {
    for (const name of SHELL_FILES) {
      const src = read(join(SHELL, name))
      for (const spec of importsOf(src)) {
        expect(spec, name).not.toMatch(/^vue-router$|^pinia$|^axios$|^@shared\/|features\/project\/(api|store|types)/)
        expect(spec, name).not.toMatch(/ResizablePane|SplitPane/)
      }
      expect(src, name).not.toMatch(/localStorage|sessionStorage|defineStore/)
    }
  })

  it('the router reaches the Shell only through the features/designer public surface', () => {
    const router = read(join(SRC, 'app/router/index.ts'))
    expect(importsOf(router)).toContain('@/features/designer')
    expect(router).not.toMatch(/features\/designer\/shell/)
  })

  it.each(['ko', 'en', 'ja'])('i18n %s has exactly one top-level designer key holding all three blocks', async (locale) => {
    const file = join(SRC, `app/i18n/${locale}.ts`)
    expect(read(file).match(/^ {2}designer:\s*\{/gm)).toHaveLength(1)
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, Record<string, unknown>>
    expect(Object.keys(messages.designer).sort()).toEqual(['file_explorer', 'project_explorer', 'shell'])
  })
})
