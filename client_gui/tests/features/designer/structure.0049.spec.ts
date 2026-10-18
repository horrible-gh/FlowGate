import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'

// flowgate.v02.0049 — source-level guards for the bottom MiniMap (0001-R §3 / 0002-T §2): its own
// part, drawing the Center session's Graph and Viewport — no Graph state, Vue Flow, API or store.
const SRC = resolve(__dirname, '../../../src')
const MINIMAP = join(SRC, 'features/designer/minimap')
const MINIMAP_FILES = readdirSync(MINIMAP).sort()

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

describe('features/designer/minimap structure (flowgate.v02.0049)', () => {
  it('holds exactly the MiniMap part and its geometry', () => {
    expect(MINIMAP_FILES).toEqual(['DesignerMiniMap.vue', 'minimapGeometry.ts'].sort())
  })

  it('imports only vue / vue-i18n, its own files and the Canvas view / Node kind / mapping / Graph Model contracts', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/center\/canvasView$/,
      /^\.\.\/center\/canvas\/flowNodeKinds$/,
      /^\.\.\/center\/canvas\/vueFlowMapping$/,
      /^\.\.\/center\/graph\/flowGraph$/,
      // flowgate.v02.0068 T#2 (0004-D §0.1 미니맵 접속면 통일, 0005-L §2.9): the lines join the sides the Canvas
      // joins — the Canvas's own edgeAnchors (pure; edgeGeometry itself is unchanged). Nothing else is opened.
      /^\.\.\/center\/canvas\/edgeGeometry$/,
    ]
    for (const name of MINIMAP_FILES) {
      for (const spec of importsOf(read(join(MINIMAP, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('keeps the geometry free of screens', () => {
    for (const spec of importsOf(read(join(MINIMAP, 'minimapGeometry.ts')))) expect(spec).not.toMatch(/\.vue$|^vue/)
  })

  it('owns no store, API, persistence, Vue Flow instance, Center session or Shell', () => {
    for (const name of MINIMAP_FILES) {
      const src = read(join(MINIMAP, name))
      for (const spec of importsOf(src)) {
        expect(spec, name).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\/|@vue-flow|\/shell\/|designerCenterSession|@\/features\//)
      }
      expect(src, name).not.toMatch(/defineStore|localStorage|sessionStorage|fetch\(|useVueFlow/)
    }
  })

  it('is not part of the features/designer public surface', () => {
    expect(Object.keys(DesignerFeature)).not.toContain('DesignerMiniMap')
  })
})
