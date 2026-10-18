import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { FLOW_NODE_KINDS, FLOW_NODE_STATES } from '@/features/designer/center/canvas/flowNodeKinds'

// flowgate.v02.0026 — source-level guards for the Designer Center (0003-D §2.3 / §2.4.5 /
// §7.1) that a DOM test cannot see.
const ROOT = resolve(__dirname, '../../..')
const SRC = join(ROOT, 'src')
const CENTER = join(SRC, 'features/designer/center')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? filesUnder(full) : [full]
  })
}

const rel = (file: string) => relative(CENTER, file).replace(/\\/g, '/')
const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])
const CENTER_FILES = filesUnder(CENTER)
const ALL_SRC = filesUnder(SRC).filter((f) => /\.(ts|vue)$/.test(f))

describe('features/designer/center structure (flowgate.v02.0026)', () => {
  it('holds exactly the Center files of 0003-D §2.1', () => {
    expect(CENTER_FILES.map(rel).sort()).toEqual(
      [
        'DesignerCenter.vue',
        'FlowTabStrip.vue',
        'canvas/FlowCanvas.vue',
        'canvas/FlowEdgePath.vue',
        'canvas/FlowNodeCard.vue',
        // flowgate.v02.0045 TR0020 rev4: which sides an Edge attaches to.
        'canvas/edgeGeometry.ts',
        'canvas/flowNodeKinds.ts',
        'canvas/vueFlowMapping.ts',
        'canvasView.ts',
        'designerCenterSession.ts',
        'flowTabs.ts',
        // flowgate.v02.0045 T0019: which Edges the work already went through.
        'graph/edgeProgress.ts',
        // flowgate.v02.0056 F2-1: what a Graph card shows for its run (Canvas chip and Inspector alike).
        'graph/cardStateView.ts',
        // flowgate.v02.0064 T#4: a card's own title — its length, version order and who may edit it.
        'graph/nodeTitle.ts',
        'graph/fixtureFlowGraphSource.ts',
        'graph/flowGraph.ts',
        'graph/flowGraphSource.ts',
        // flowgate.v02.0045 S1+S2: the server Graph source and the layout save batching.
        'graph/serverFlowGraphSource.ts',
        'layoutSaveQueue.ts',
        // flowgate.v02.0045 S3+S4 (D0004 §2.3 center/edit): edit commands, connection pre-check,
        // the draft editor / controller (TR0020 rev4: no draft bar — nothing is laid over the Canvas).
        'edit/connectionRules.ts',
        'edit/draftController.ts',
        'edit/draftEditor.ts',
        'edit/draftModel.ts',
        'edit/graphEditCommands.ts',
        // flowgate.v02.0053 T#9: the top band's run buttons on the server Graph run.
        'run/flowRunControl.ts',
        // flowgate.v02.0059 T#2: one undo / redo of structure edits and card moves, the draft actions
        // moved out of the controller, and 정렬 / 분배 / 자동 배치 (the server's auto_layout ported).
        'edit/editHistory.ts',
        'edit/draftActions.ts',
        'arrange/arrangeNodes.ts',
        'arrange/autoLayout.ts',
        // flowgate.v02.0068 T#2: 가로 / 세로 자동정렬 (0005-L §2.1 ~ §2.8) in its own files next to the server port.
        'arrange/directionalLayout.ts',
        'arrange/edgeCurve.ts',
        'arrange/layoutBands.ts',
        'arrange/layoutInput.ts',
        'arrange/layoutLanes.ts',
      ].sort(),
    )
  })

  it('imports @vue-flow/* only inside center/canvas/, anywhere in src', () => {
    for (const file of ALL_SRC) {
      const inCanvas = relative(SRC, file).replace(/\\/g, '/').startsWith('features/designer/center/canvas/')
      for (const spec of importsOf(read(file))) {
        if (spec.startsWith('@vue-flow/')) expect(inCanvas, relative(SRC, file)).toBe(true)
      }
    }
  })

  it('keeps the model, rules and session free of screens and Vue Flow', () => {
    const pure = CENTER_FILES.filter((f) =>
      /^(graph\/|flowTabs\.ts$|canvasView\.ts$|designerCenterSession\.ts$|layoutSaveQueue\.ts$)/.test(rel(f)),
    )
    // flowgate.v02.0056 F2-1: graph/cardStateView.ts is the tenth; flowgate.v02.0064 T#4: graph/nodeTitle.ts the eleventh.
    expect(pure).toHaveLength(11)
    for (const file of pure) {
      for (const spec of importsOf(read(file))) {
        expect(spec, rel(file)).not.toMatch(/\.vue$|^@vue-flow\//)
      }
    }
  })

  it('imports only vue / vue-i18n, the features/project surface, ui primitives, its own files and (canvas only) @vue-flow/core', () => {
    for (const file of CENTER_FILES) {
      const name = rel(file)
      const depth = name.split('/').length - 1
      const ui = new RegExp(`^${'\\.\\./'.repeat(3 + depth)}components/ui/[A-Za-z]+\\.vue$`)
      const allowed = [/^vue$/, /^vue-i18n$/, /^@\/features\/project$/, ui, /^\.\/[A-Za-z/]+(\.vue)?$/, /^\.\.\/[A-Za-z/]+(\.vue)?$/]
      if (name.startsWith('canvas/')) allowed.push(/^@vue-flow\/core$/)
      if (name === 'canvas/FlowCanvas.vue') allowed.push(/^@vue-flow\/core\/dist\/style\.css$/)
      for (const spec of importsOf(read(file))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
        // Relative imports never leave center/ except for the ui primitives.
        if (spec.startsWith('../') && !ui.test(spec)) expect(depth, `${name}: ${spec}`).toBeGreaterThan(0)
      }
    }
  })

  it('never reaches the Explorers, the Shell or the designer index, and owns no store / API', () => {
    for (const file of CENTER_FILES) {
      const src = read(file)
      for (const spec of importsOf(src)) {
        expect(spec, rel(file)).not.toMatch(/projectExplorer|fileExplorer|\/shell\/|designer\/shared|^\.\.\/index$|@\/features\/designer/)
        expect(spec, rel(file)).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\//)
      }
      expect(src, rel(file)).not.toMatch(/defineStore|localStorage|sessionStorage/)
    }
  })

  it('never loads the Vue Flow default theme, and only FlowCanvas loads the base style', () => {
    for (const file of ALL_SRC) expect(read(file), relative(SRC, file)).not.toMatch(/theme-default\.css/)
    const withStyle = CENTER_FILES.filter((f) => importsOf(read(f)).includes('@vue-flow/core/dist/style.css')).map(rel)
    expect(withStyle).toEqual(['canvas/FlowCanvas.vue'])
  })

  it('pins @vue-flow/core to exactly 1.48.2 and adds no other @vue-flow package', () => {
    const pkg = JSON.parse(read(join(ROOT, 'package.json'))) as Record<string, Record<string, string> | undefined>
    expect(pkg.dependencies?.['@vue-flow/core']).toBe('1.48.2')
    const all = { ...pkg.dependencies, ...pkg.devDependencies }
    expect(Object.keys(all).filter((name) => name.startsWith('@vue-flow/'))).toEqual(['@vue-flow/core'])
    const lock = JSON.parse(read(join(ROOT, 'package-lock.json'))) as {
      packages: Record<string, { version?: string; dependencies?: Record<string, string> }>
    }
    expect(lock.packages[''].dependencies?.['@vue-flow/core']).toBe('1.48.2')
    expect(lock.packages['node_modules/@vue-flow/core']?.version).toBe('1.48.2')
  })

  it.each(['ko', 'en', 'ja'])('i18n %s node kinds / states match flowNodeKinds.ts', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const center = messages.designer.center
    expect(Object.keys(center.node_kinds).sort()).toEqual([...FLOW_NODE_KINDS, 'unknown'].sort())
    expect(Object.keys(center.node_states).sort()).toEqual([...FLOW_NODE_STATES].sort())
  })

  it.each(['ko', 'en', 'ja'])('i18n %s has the Center keys and no Workspace placeholder strings', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    expect(Object.keys(messages.designer.center).sort()).toEqual(
      [
        'tabs_label',
        'close_tab',
        'graph_loading',
        'graph_load_failed',
        'retry',
        'sample_data',
        'canvas_label',
        'node_kinds',
        'node_states',
        // flowgate.v02.0045 T0019: a selected Edge's remove button / why it cannot be removed.
        // (TR0020 rev4: no `edit` strings — nothing about saving / 검증 is shown on the Canvas.)
        'edge',
        // flowgate.v02.0056 F2-1: a Graph card's state (cardStateView.ts) and its [승인] with the refusals.
        'card_states',
        'approval',
      ].sort(),
    )
    // flowgate.v02.0045 T0019: no empty-Graph text and no ownership badge any more.
    expect(messages.designer.center).not.toHaveProperty('empty_graph')
    expect(messages.designer.center).not.toHaveProperty('ownership')
    expect(Object.keys(messages.designer.center.edge).sort()).toEqual(['locked', 'remove'])
    for (const key of ['workspace_label', 'workspace_parts', 'active_flow', 'no_active_flow']) {
      expect(messages.designer.shell).not.toHaveProperty(key)
    }
    // flowgate.v02.0042 — no guidance text while no Flow is open.
    expect(messages.designer.center).not.toHaveProperty('no_open_flow')
    expect(messages.designer.center).not.toHaveProperty('no_open_flow_desc')
  })

  it('ko Center strings use 플로우 and ja uses フロー', async () => {
    const strings = (value: unknown): string[] =>
      typeof value === 'string' ? [value] : value && typeof value === 'object' ? Object.values(value).flatMap(strings) : []
    const ko = (await import('@/app/i18n/ko.ts')).default as Record<string, any>
    const ja = (await import('@/app/i18n/ja.ts')).default as Record<string, any>
    for (const text of strings(ko.designer.center)) expect(text).not.toMatch(/Flow|흐름|플로(?!우)/)
    for (const text of strings(ja.designer.center)) expect(text).not.toMatch(/Flow/)
    expect(ko.designer.center.tabs_label).toContain('플로우')
    expect(ja.designer.center.tabs_label).toContain('フロー')
  })
})
