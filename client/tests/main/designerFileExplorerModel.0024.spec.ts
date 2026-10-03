import { describe, expect, it } from 'vitest'
import {
  changedViewNodes, flattenRows, searchNodes, toChangeStatus, toStatusMap,
} from '@main/components/designer/fileExplorer/fileExplorerModel'

// flowgate.v02.0024 T0003 — pure projection rules behind the Designer File Explorer's
// 전체 / 변경됨 / 찾기 views.

function n(id: string, parent: string | null, type: 'folder' | 'file', path: string) {
  const name = path.split('/').pop()!
  return { id, parent_id: parent, type, name, label: name, path, permissions: ['read'] }
}

const NODES = [
  n('1', null, 'folder', 'src'),
  n('2', '1', 'folder', 'src/app'),
  n('3', '2', 'file', 'src/app/main.ts'),
  n('4', '2', 'file', 'src/app/util.ts'),
  n('5', '1', 'file', 'src/index.ts'),
  n('6', null, 'folder', 'docs'),
  n('7', '6', 'file', 'docs/readme.md'),
  n('8', null, 'file', 'package.json'),
]

describe('designer file explorer model (0024 T0003)', () => {
  it('maps server change letters onto Base-relative M/A/D', () => {
    expect(toChangeStatus('M')).toBe('M')
    expect(toChangeStatus('A')).toBe('A')
    expect(toChangeStatus('?')).toBe('A')
    expect(toChangeStatus('D')).toBe('D')
    expect(toChangeStatus('T')).toBe('M')
    expect(toStatusMap([
      { path: 'src\\app\\main.ts', status: 'M' },
      { path: 'new.txt', status: '?' },
    ])).toEqual({ 'src/app/main.ts': 'M', 'new.txt': 'A' })
  })

  it('changed view keeps changed files plus ancestors only, and synthesizes committed deletions', () => {
    const out = changedViewNodes(NODES, { 'src/app/main.ts': 'M', 'gone/old.ts': 'D' })
    const paths = out.map((x) => x.path).sort()
    expect(paths).toEqual(['gone', 'gone/old.ts', 'src', 'src/app', 'src/app/main.ts'])
    const virtual = out.find((x) => x.path === 'gone/old.ts')!
    expect(virtual.id).toBe('chg:gone/old.ts')
    expect(virtual.parent_id).toBe('chg:gone')
    expect(virtual.type).toBe('file')
  })

  it('a synthesized deletion under an existing folder hangs off that real folder', () => {
    const out = changedViewNodes(NODES, { 'docs/removed.md': 'D' })
    expect(out.find((x) => x.path === 'docs/removed.md')!.parent_id).toBe('6')
  })

  it('search keeps matches and their ancestors and reports folders to reveal', () => {
    const { nodes, reveal } = searchNodes(NODES, 'UTIL')
    expect(nodes.map((x) => x.path)).toEqual(['src', 'src/app', 'src/app/util.ts'])
    expect([...reveal].sort()).toEqual(['1', '2'])
    expect(searchNodes(NODES, '  ').nodes).toBe(NODES)
  })

  it('flattens folders first with depth, honouring expansion, and counts changed descendants', () => {
    const statuses = { 'src/app/main.ts': 'M' as const, 'src/index.ts': 'A' as const }
    const collapsed = flattenRows(NODES, () => false, statuses)
    expect(collapsed.map((r) => r.node.path)).toEqual(['docs', 'src', 'package.json'])
    expect(collapsed.find((r) => r.node.path === 'src')!.changedCount).toBe(2)

    const open = flattenRows(NODES, (node) => node.path === 'src' || node.path === 'src/app', statuses)
    expect(open.map((r) => [r.node.path, r.depth])).toEqual([
      ['docs', 0], ['src', 0], ['src/app', 1], ['src/app/main.ts', 2], ['src/app/util.ts', 2],
      ['src/index.ts', 1], ['package.json', 0],
    ])
    expect(open.find((r) => r.node.path === 'src/app/main.ts')!.status).toBe('M')
    expect(open.find((r) => r.node.path === 'src/app/util.ts')!.status).toBeNull()
  })

  it('shows no status at all while statuses are not established', () => {
    const rows = flattenRows(NODES, () => true, null)
    expect(rows.every((r) => r.status === null && r.changedCount === 0)).toBe(true)
  })
})
