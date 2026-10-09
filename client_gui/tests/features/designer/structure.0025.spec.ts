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
      'DesignerResizeHandle.vue',
      'DesignerRightDock.vue',
      'DesignerShell.vue',
      'DesignerTop.vue',
      'DesignerWorkspace.vue',
      'ExplorerModeTabs.vue',
      'ShellPlaceholder.vue',
      'designerLayout.ts',
    ].sort())
  })

  it('imports only vue / vue-i18n, the features/project surface, ui primitives, its own files, the two Explorer entry points and the two Center entry points', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^@\/features\/project$/,
      /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/projectExplorer\/ProjectExplorer\.vue$/,
      /^\.\.\/projectExplorer\/projectExplorerTree$/,
      /^\.\.\/fileExplorer\/DesignerFileExplorer\.vue$/,
      // flowgate.v02.0026 — Designer Center (0003-D §2.3): the screen and the session only.
      /^\.\.\/center\/DesignerCenter\.vue$/,
      /^\.\.\/center\/designerCenterSession$/,
      // flowgate.v02.0045 — the server Graph source the Shell hands to the Center session.
      /^\.\.\/center\/graph\/serverFlowGraphSource$/,
      // flowgate.v02.0036 — bottom Inspector: the Frame and the Graph Node type it is given.
      /^\.\.\/inspector\/DesignerInspector\.vue$/,
      /^\.\.\/center\/graph\/flowGraph$/,
      // flowgate.v02.0049 — bottom Minimap: the part and the Canvas view types it is given.
      /^\.\.\/minimap\/DesignerMiniMap\.vue$/,
      /^\.\.\/center\/canvasView$/,
      // flowgate.v02.0037 — right Tool Panel: the Palette part and the public surfaces of the
      // Conversation and Git parts.
      /^\.\.\/palette\/PalettePanel\.vue$/,
      // flowgate.v02.0045 S3 — the Shell reads a Palette drag dropped on the Canvas (D0004 §2.3).
      /^\.\.\/palette\/paletteCatalog$/,
      /^@\/features\/conversation$/,
      /^@\/features\/git$/,
      // flowgate.v02.0038 — top band: the Header / Menu Bar / Toolbar parts and their model.
      /^\.\.\/top\/Designer(Header|MenuBar|Toolbar)\.vue$/,
      /^\.\.\/top\/designerTop$/,
      // flowgate.v02.0043 — File menu dialogs (Group / Flow 열기, 새 Group / Flow).
      /^\.\.\/fileMenu\/Designer[A-Za-z]+Dialog\.vue$/,
      // flowgate.v02.0046 — Dock resize handles: the shared single-axis drag interaction.
      /^\.\.\/\.\.\/\.\.\/core\/interaction\/axisResize$/,
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
        // flowgate.v02.0045 S3: the Palette catalog's drag reader is the one Palette module the
        // Shell may use besides the panel (the Palette -> Canvas drop bridge, D0004 §2.3).
        expect(spec, name).not.toMatch(/palette\/(?!PalettePanel\.vue$|paletteCatalog$)|features\/(conversation|git)\//)
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

  it.each(['ko', 'en', 'ja'])('i18n %s has exactly one top-level designer key holding all its blocks', async (locale) => {
    const file = join(SRC, `app/i18n/${locale}.ts`)
    expect(read(file).match(/^ {2}designer:\s*\{/gm)).toHaveLength(1)
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, Record<string, unknown>>
    // flowgate.v02.0030 added the Palette block, flowgate.v02.0036 the Inspector block,
    // flowgate.v02.0038 the top band block, flowgate.v02.0043 the File menu dialogs block.
    expect(Object.keys(messages.designer).sort()).toEqual([
      'center', 'file_explorer', 'file_menu', 'inspector', 'palette', 'project_explorer', 'shell', 'top',
    ])
  })

  // flowgate.v02.0046 TR0003 rev1 — jsdom lays nothing out, so the DOM tests cannot see where
  // the Body's handles land. An absolutely placed grid item with only a start line spans to the
  // container's far edge (`grid-column: 1` = the whole Body), which put the left handle on the
  // Body's right edge in a real browser; each handle must name both lines of its Dock column.
  it('pins each Body resize handle to its own Dock column with both grid lines', () => {
    const body = read(join(SHELL, 'DesignerBody.vue'))
    const rule = (side: string) =>
      body.match(new RegExp(String.raw`\.designer-body-resize\.is-${side}\s*\{([^}]*)\}`))?.[1] ?? ''
    expect(rule('left')).toMatch(/grid-column:\s*1\s*\/\s*2\s*;/)
    expect(rule('right')).toMatch(/grid-column:\s*3\s*\/\s*4\s*;/)
  })
})
