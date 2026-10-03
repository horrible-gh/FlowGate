/**
 * flowgate.v02.0023 T0006 — pure derivation of the Designer Project Explorer tree.
 *
 * The flat group tree (project / module / group / orphan / document) is re-read as v0.2
 * Project > Group > Flow; documents (v0.2 Nodes) and orphan buckets never appear, and the
 * search filter keeps the Project/Group context around every match.
 */
import { describe, expect, it } from 'vitest'
import type { GroupTreeNode } from '@/features/project'
import {
  buildExplorerProject,
  filterExplorerProject,
  projectNodeId,
} from '@/features/designer/projectExplorer/projectExplorerTree'

function n(partial: Partial<GroupTreeNode> & Pick<GroupTreeNode, 'id' | 'parentId' | 'kind' | 'label'>): GroupTreeNode {
  return { title: null, number: null, ...partial }
}

const NODES: GroupTreeNode[] = [
  n({ id: 'project:p', parentId: null, kind: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parentId: 'project:p', kind: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parentId: 'module:p:ui', kind: 'group', label: 'Designer v0.2', number: '0001' }),
  n({ id: 'p.ui.0001.0001-R', parentId: 'p.ui.0001', kind: 'document', label: '[요건정의]: Explorer spec' }),
  n({ id: 'p.ui.0001.sub', parentId: 'p.ui.0001', kind: 'group', label: 'Nested flow' }),
  n({ id: 'p.ui.0002', parentId: 'module:p:ui', kind: 'group', label: 'Canvas', number: '0002' }),
  n({ id: 'orphan:module:p:ui:p.ui.9999', parentId: 'module:p:ui', kind: 'orphan', label: 'Uncategorized: p.ui.9999' }),
  n({ id: 'p.ui.9999.0001-M', parentId: 'orphan:module:p:ui:p.ui.9999', kind: 'document', label: '[메모]: stray' }),
  n({ id: 'module:p:core', parentId: 'project:p', kind: 'module', label: 'core' }),
  n({ id: 'p.core.0003', parentId: 'module:p:core', kind: 'group', label: 'Engine', number: '0003' }),
  n({ id: 'module:p:empty', parentId: 'project:p', kind: 'module', label: 'empty', title: 'Empty group' }),
]

describe('buildExplorerProject', () => {
  it('maps project/module/group to Project/Group/Flow and drops documents and orphans', () => {
    const project = buildExplorerProject(NODES, projectNodeId('p'))!
    expect(project).toMatchObject({ id: 'project:p', name: 'Alpha' })
    expect(project.groups.map((g) => [g.id, g.name])).toEqual([
      ['module:p:ui', 'UI 개선'],
      ['module:p:core', 'core'],
      ['module:p:empty', 'Empty group'],
    ])
    const ui = project.groups[0]
    // The nested v0.1 group is still a Flow of the same Group — three levels, no deeper.
    expect(ui.flows.map((f) => f.id)).toEqual(['p.ui.0001', 'p.ui.0001.sub', 'p.ui.0002'])
    expect(ui.flows.every((f) => f.groupId === 'module:p:ui')).toBe(true)
    expect(ui.flows[0]).toMatchObject({ name: 'Designer v0.2', number: '0001' })
    expect(project.groups[2].flows).toEqual([])
    const ids = JSON.stringify(project)
    expect(ids).not.toContain('0001-R')
    expect(ids).not.toContain('orphan:')
  })

  it('falls back to the given project name when the payload has no project node', () => {
    expect(buildExplorerProject([], 'project:p', 'From store')).toEqual({ id: 'project:p', name: 'From store', groups: [] })
    expect(buildExplorerProject([], null)).toBeNull()
  })

  it('does not mutate the canonical node array', () => {
    const before = JSON.stringify(NODES)
    filterExplorerProject(buildExplorerProject(NODES, 'project:p'), 'designer')
    expect(JSON.stringify(NODES)).toBe(before)
  })
})

describe('filterExplorerProject', () => {
  const project = buildExplorerProject(NODES, 'project:p')

  it('returns the tree unchanged and reveals nothing without a query', () => {
    const result = filterExplorerProject(project, '   ')
    expect(result.project).toBe(project)
    expect(result.revealIds.size).toBe(0)
  })

  it('keeps Project and Group around a matching Flow and reveals both', () => {
    const result = filterExplorerProject(project, 'DESIGNER')
    expect(result.project!.groups.map((g) => g.id)).toEqual(['module:p:ui'])
    expect(result.project!.groups[0].flows.map((f) => f.id)).toEqual(['p.ui.0001'])
    expect([...result.revealIds].sort()).toEqual(['module:p:ui', 'project:p'])
    expect([...result.matchIds]).toEqual(['p.ui.0001'])
  })

  it('keeps a matching Group with all of its Flows under its Project', () => {
    const result = filterExplorerProject(project, 'core')
    expect(result.project!.groups.map((g) => g.id)).toEqual(['module:p:core'])
    expect(result.project!.groups[0].flows.map((f) => f.id)).toEqual(['p.core.0003'])
    expect(result.revealIds.has('project:p')).toBe(true)
  })

  it('keeps the whole project when the Project name matches', () => {
    const result = filterExplorerProject(project, 'alp')
    expect(result.project!.groups).toHaveLength(3)
  })

  it('never matches document (Node) titles', () => {
    expect(filterExplorerProject(project, 'Explorer spec').project).toBeNull()
    expect(filterExplorerProject(project, 'stray').project).toBeNull()
  })
})
