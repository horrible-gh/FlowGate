import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// source-level guards for the Designer Shell that a
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
      'runFailureToast.ts',
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
      // Designer Center: the screen and the session only.
      /^\.\.\/center\/DesignerCenter\.vue$/,
      /^\.\.\/center\/designerCenterSession$/,
      // the server Graph source the Shell hands to the Center session.
      /^\.\.\/center\/graph\/serverFlowGraphSource$/,
      // bottom Inspector: the Frame and the Graph Node type it is given.
      /^\.\.\/inspector\/DesignerInspector\.vue$/,
      /^\.\.\/center\/graph\/flowGraph$/,
      // bottom Minimap: the part and the Canvas view types it is given.
      /^\.\.\/minimap\/DesignerMiniMap\.vue$/,
      /^\.\.\/center\/canvasView$/,
      // right Tool Panel: the Palette part and the public surfaces of the
      // Conversation and Git parts.
      /^\.\.\/palette\/PalettePanel\.vue$/,
      // S3 — the Shell reads a Palette drag dropped on the Canvas.
      /^\.\.\/palette\/paletteCatalog$/,
      /^@\/features\/conversation$/,
      /^@\/features\/git$/,
      // the v0.1 document read (features/document public surface) the Shell hands to the
      // server Graph source, so auto-layout can follow a v0.1 sequence root's supersede chain.
      /^@\/features\/document$/,
      // top band: the Header / Menu Bar / Toolbar parts and their model.
      /^\.\.\/top\/Designer(Header|MenuBar|Toolbar)\.vue$/,
      /^\.\.\/top\/designerTop$/,
      // File menu dialogs (Group / Flow Open, New Group / Flow).
      /^\.\.\/fileMenu\/Designer[A-Za-z]+Dialog\.vue$/,
      // the Designer context menu: its view and the host / area connectors.
      /^\.\.\/contextMenu\/DesignerContextMenu\.vue$/,
      /^\.\.\/contextMenu\/attach\/designerMenuConnector$/,
      // Dock resize handles: the shared single-axis drag interaction.
      /^\.\.\/\.\.\/\.\.\/core\/interaction\/axisResize$/,
    ]
    for (const name of SHELL_FILES) {
      for (const spec of importsOf(read(join(SHELL, name)))) {
        // D: only the Shell hosts the shared first-run role surface/coordinator; the earlier implementation (D revision): and the
        // run result windows with their watch, from the same features/ai surface.
        const firstRunRoles = name === 'DesignerShell.vue' && spec === '@/features/ai'
        const runFailureParts = name === 'runFailureToast.ts' && /^\.\.\/center\/run\/(flowRunControl|runFailureDiagnostic)$/.test(spec)
        expect(firstRunRoles || runFailureParts || allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('never reaches Explorer internals (rows, trees, toolbars, adapters) or the designer index', () => {
    for (const name of SHELL_FILES) {
      for (const spec of importsOf(read(join(SHELL, name)))) {
        expect(spec, name).not.toMatch(/projectExplorer\/(?!ProjectExplorer\.vue$|projectExplorerTree$)/)
        expect(spec, name).not.toMatch(/fileExplorer\/(?!DesignerFileExplorer\.vue$)/)
        expect(spec, name).not.toMatch(/designer\/shared\//)
        // S3: the Palette catalog's drag reader is the one Palette module the
        // Shell may use besides the panel (the Palette -> Canvas drop bridge, the design).
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
    // added the Palette block, the earlier change the Inspector block,
    // the top band block, the earlier change the File menu dialogs block,
    // the card inline title edit block (`gui.designer.nodeTitle.*`).
    expect(Object.keys(messages.designer).sort()).toEqual([
      'center', 'file_explorer', 'file_menu', 'inspector', 'nodeTitle', 'palette', 'project_explorer', 'shell', 'top',
    ])
  })

  // jsdom lays nothing out, so the DOM tests cannot see where
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
