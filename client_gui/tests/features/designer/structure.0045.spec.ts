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
]
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

  it('the layout save queue knows no Vue and no transport', () => {
    for (const line of importsOf(read('features/designer/center/layoutSaveQueue.ts'))) {
      expect(line).toMatch(/^import type .* from '\.\/graph\/flowGraph(Source)?'$/)
    }
  })
})
