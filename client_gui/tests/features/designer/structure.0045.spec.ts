import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// flowgate.v02.0045 T0011 — source guards for the S1+S2 client files (L0009 §1.5
// CLIENT_NEW_FILE_MAX_LINES, D0004 §2.3 / §4.2 dependency direction).
const SRC = join(resolve(__dirname, '../../..'), 'src')
const NEW_FILES = [
  'features/project/flowCanvasApi.ts',
  'features/project/flowCanvasEvents.ts',
  'features/designer/center/graph/serverFlowGraphSource.ts',
  'features/designer/center/layoutSaveQueue.ts',
  // flowgate.v02.0045 T0013 (S3+S4)
  'features/project/flowGraphApi.ts',
  'features/designer/center/edit/DraftToolbar.vue',
  'features/designer/center/edit/connectionRules.ts',
  'features/designer/center/edit/draftController.ts',
  'features/designer/center/edit/draftEditor.ts',
  'features/designer/center/edit/draftModel.ts',
  'features/designer/center/edit/graphEditCommands.ts',
]
const EDIT_LOGIC = NEW_FILES.filter((rel) => /center\/edit\/[a-zA-Z]+\.ts$/.test(rel))
const read = (rel: string) => readFileSync(join(SRC, rel), 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[0])

describe('flowgate.v02.0045 client structure', () => {
  it('keeps every new Graph file at or under 300 lines', () => {
    for (const rel of NEW_FILES) expect(read(rel).split('\n').length, rel).toBeLessThanOrEqual(300)
  })

  it('the server Graph source takes only types from features/project and owns no transport', () => {
    const imports = importsOf(read('features/designer/center/graph/serverFlowGraphSource.ts'))
    for (const line of imports) {
      expect(line).toMatch(/^import type /)
      expect(line).not.toMatch(/@shared\/|axios|pinia/)
    }
  })

  it('the edit commands, pre-check, draft editor and controller know no Vue, no Vue Flow and no transport', () => {
    expect(EDIT_LOGIC).toHaveLength(5)
    for (const rel of EDIT_LOGIC) {
      for (const line of importsOf(read(rel))) {
        expect(line, rel).toMatch(/from '\.\.?\/[A-Za-z/]+'$/)
        expect(line, rel).not.toMatch(/'vue'|@vue-flow|@shared|axios|pinia|\.vue'/)
      }
    }
  })

  it('the Graph saver transport stays inside features/project and the public surface stays type-only', () => {
    expect(read('features/project/index.ts')).not.toMatch(/export \{[^}]*flowGraphApi|from '\.\/flowGraphApi'/)
    for (const line of importsOf(read('features/project/flowGraphApi.ts'))) {
      expect(line).toMatch(/'@shared\/api'|'\.\/flowCanvasApi'|'\.\/types'/)
    }
  })

  it('the layout save queue knows no Vue and no transport', () => {
    for (const line of importsOf(read('features/designer/center/layoutSaveQueue.ts'))) {
      expect(line).toMatch(/^import type .* from '\.\/graph\/flowGraph(Source)?'$/)
    }
  })
})
