import { describe, expect, it } from 'vitest'
import type { FlowFileNode } from '@/features/project'
import {
  changedViewNodes,
  flattenRows,
  normalizePath,
  searchNodes,
  toChangeStatus,
  toStatusMap,
} from '@/features/designer/fileExplorer/fileExplorerModel'

// flowgate.v02.0024 T0005 — the pure "전체 / 변경됨 / 찾기" rules behind the Designer File
// Explorer, pinned without Vue or a store.
function node(path: string, type: 'folder' | 'file' = 'file'): FlowFileNode {
  const segs = path.split('/')
  const parent = segs.slice(0, -1).join('/')
  return { id: `n:${path}`, parentId: parent ? `n:${parent}` : null, type, name: segs[segs.length - 1], label: segs[segs.length - 1], path, permissions: ['read'] }
}

const TREE = [node('src', 'folder'), node('src/b.ts'), node('src/a.ts'), node('src/lib', 'folder'), node('src/lib/x.ts'), node('README.md')]

describe('change status mapping', () => {
  it('folds untracked into A, keeps D, and reads any other letter as M', () => {
    expect(['M', 'A', '?', 'D', 'R100', 'T', 'U', ''].map(toChangeStatus)).toEqual(['M', 'A', 'A', 'D', 'M', 'M', 'M', 'M'])
  })

  it('keys statuses by normalized path and drops empty paths', () => {
    expect(toStatusMap([{ path: 'src\\a.ts', status: 'M' }, { path: '/docs/n.md/', status: '?' }, { path: '', status: 'M' }]))
      .toEqual({ 'src/a.ts': 'M', 'docs/n.md': 'A' })
    expect(normalizePath('\\a\\b\\')).toBe('a/b')
  })
})

describe('flattenRows', () => {
  it('lists folders first then files by name, and descends only into expanded folders', () => {
    const collapsed = flattenRows(TREE, () => false, null)
    expect(collapsed.map((r) => r.node.path)).toEqual(['src', 'README.md'])
    const open = flattenRows(TREE, (n) => n.path === 'src', null)
    expect(open.map((r) => [r.node.path, r.depth])).toEqual([
      ['src', 0], ['src/lib', 1], ['src/a.ts', 1], ['src/b.ts', 1], ['README.md', 0],
    ])
    expect(open[0]).toMatchObject({ hasChildren: true, expanded: true, status: null, changedCount: 0 })
  })

  it('carries file statuses and counts changed files under each folder, collapsed or not', () => {
    const rows = flattenRows(TREE, (n) => n.path === 'src', { 'src/a.ts': 'M', 'src/lib/x.ts': 'A' })
    const byPath = Object.fromEntries(rows.map((r) => [r.node.path, r]))
    expect(byPath['src'].changedCount).toBe(2)
    expect(byPath['src/lib'].changedCount).toBe(1)
    expect(byPath['src/a.ts'].status).toBe('M')
    expect(byPath['src/b.ts'].status).toBeNull()
  })
})

describe('changedViewNodes', () => {
  it('keeps only changed paths and their ancestors', () => {
    const view = changedViewNodes(TREE, { 'src/lib/x.ts': 'M' })
    expect(view.map((n) => n.path).sort()).toEqual(['src', 'src/lib', 'src/lib/x.ts'])
  })

  it('synthesizes read-only virtual nodes for paths no longer in the tree (committed deletes)', () => {
    const view = changedViewNodes(TREE, { 'gone/deep/old.ts': 'D' })
    const virtual = view.filter((n) => n.id.startsWith('chg:'))
    expect(virtual.map((n) => [n.path, n.type, n.parentId])).toEqual([
      ['gone', 'folder', null],
      ['gone/deep', 'folder', 'chg:gone'],
      ['gone/deep/old.ts', 'file', 'chg:gone/deep'],
    ])
    expect(virtual.every((n) => n.permissions.length === 1 && n.permissions[0] === 'read')).toBe(true)
    const rows = flattenRows(view, () => true, { 'gone/deep/old.ts': 'D' })
    expect(rows.map((r) => r.node.path)).toEqual(['gone', 'gone/deep', 'gone/deep/old.ts'])
  })
})

describe('searchNodes', () => {
  it('returns the input untouched for a blank query', () => {
    const result = searchNodes(TREE, '  ')
    expect(result.nodes).toBe(TREE)
    expect(result.reveal.size).toBe(0)
  })

  it('matches names case-insensitively and keeps + reveals every ancestor', () => {
    const result = searchNodes(TREE, 'X.T')
    expect(result.nodes.map((n) => n.path)).toEqual(['src', 'src/lib', 'src/lib/x.ts'])
    expect([...result.reveal].sort()).toEqual(['n:src', 'n:src/lib'])
  })
})
