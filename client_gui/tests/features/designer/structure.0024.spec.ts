import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'

// flowgate.v02.0024 T0005 — source-level guards for the Designer File Explorer boundary
// that a DOM test cannot see. Scope adjusted in flowgate.v02.0025 (0003-D §4.2.3): the
// public surface is the whole features/designer boundary (0023 + 0024 + 0025 Shell), the
// fixed file list covers only what 0024 owns (projectExplorer/ is pinned by the 0023 spec,
// shell/ by structure.0025), and the import / Base literal guards still cover all of
// features/designer.
const SRC = resolve(__dirname, '../../../src')
const DESIGNER = join(SRC, 'features/designer')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? filesUnder(full) : [full]
  })
}

const rel = (file: string) => relative(SRC, file).replace(/\\/g, '/')
const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])
const FILES = filesUnder(DESIGNER).filter((f) => /\.(ts|vue)$/.test(f))

describe('features/designer structure (0024 T0005)', () => {
  it('exposes only ProjectExplorer, DesignerFileExplorer and DesignerShell at runtime', () => {
    expect(Object.keys(DesignerFeature).sort()).toEqual(['DesignerFileExplorer', 'DesignerShell', 'ProjectExplorer'])
  })

  // flowgate.v02.0037: the right Tool Panel also mounts the Conversation and Git parts, through
  // their public surfaces only (each part owns its own API; the Designer still owns none).
  // flowgate.v02.0048 T#3 (D#1 §2.3, F5): the Inspector features call the document API through
  // the features/document public surface.
  it('owns no API/transport/store of its own: data comes only through the features/project public surface', () => {
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) {
        expect(spec, rel(file)).not.toMatch(/^@shared\/|^axios$|^pinia$|^vue-router$|^@\/features\/(?!(project|conversation|git|document)$)/)
        expect(spec, rel(file)).not.toMatch(/features\/project\/(api|store|types)/)
      }
    }
  })

  it('has no Base branch name literal or fallback anywhere in the Designer explorer or the Flow API wrappers', () => {
    const sources = [...FILES, join(SRC, 'features/project/api.ts')]
    for (const file of sources) {
      const src = read(file)
      expect(src, rel(file)).not.toMatch(/['"`]main['"`]/)
      expect(src, rel(file)).not.toMatch(/(\|\||\?\?)\s*['"`](master|develop|trunk|v\d[\w.]*)['"`]/)
    }
  })

  it('keeps the Toolbar / Scope / Filter / Tree / Folder / File split and shares only thin row primitives', () => {
    const OWNED = /^features\/designer\/(fileExplorer\/|shared\/|index\.ts$)/
    const names = FILES.map(rel).filter((name) => OWNED.test(name)).sort()
    expect(names).toEqual([
      'features/designer/fileExplorer/DesignerFileExplorer.vue',
      'features/designer/fileExplorer/FileExplorerToolbar.vue',
      'features/designer/fileExplorer/FileFilter.vue',
      'features/designer/fileExplorer/FileRow.vue',
      'features/designer/fileExplorer/FileScopeHeader.vue',
      'features/designer/fileExplorer/FileTree.vue',
      'features/designer/fileExplorer/FolderRow.vue',
      'features/designer/fileExplorer/StatusBadge.vue',
      'features/designer/fileExplorer/fileExplorerModel.ts',
      'features/designer/fileExplorer/useFlowFileExplorer.ts',
      'features/designer/index.ts',
      'features/designer/shared/ExplorerIcon.vue',
      'features/designer/shared/ExplorerRowFrame.vue',
      'features/designer/shared/TreeCaret.vue',
    ].sort())
    for (const file of FILES.filter((f) => rel(f).startsWith('features/designer/shared/'))) {
      expect(importsOf(read(file)), rel(file)).toEqual([])
    }
  })
})
