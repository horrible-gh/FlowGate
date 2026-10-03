/**
 * flowgate.v02.0023 T0003 — pure derivation of the Designer Project Explorer tree.
 *
 * The v0.1 flat group tree (project / module / group / orphan / document) is re-read as
 * v0.2 Project > Group > Flow; documents (v0.2 Nodes) and orphan buckets never appear,
 * and the search filter keeps the Project/Group context around every match.
 */
import { describe, expect, it } from 'vitest'
import type { GroupNode } from '@main/stores/explorer'
import { buildExplorerProject, filterExplorerProject } from '@main/utils/projectExplorerTree'

function n(partial: Partial<GroupNode> & Pick<GroupNode, 'id' | 'parent_id' | 'node_type' | 'label'>): GroupNode {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const NODES: GroupNode[] = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer v0.2', number: '0001' }),
  n({ id: 'p.ui.0001.0001-R', parent_id: 'p.ui.0001', node_type: 'document', label: '[요건정의]: Explorer spec', type_code: 'R' }),
  n({ id: 'p.ui.0001.sub', parent_id: 'p.ui.0001', node_type: 'group', label: 'Nested flow' }),
  n({ id: 'p.ui.0002', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0002' }),
  n({ id: 'orphan:module:p:ui:p.ui.9999', parent_id: 'module:p:ui', node_type: 'orphan', label: 'Uncategorized: p.ui.9999' }),
  n({ id: 'p.ui.9999.0001-M', parent_id: 'orphan:module:p:ui:p.ui.9999', node_type: 'document', label: '[메모]: stray' }),
  n({ id: 'module:p:core', parent_id: 'project:p', node_type: 'module', label: 'core' }),
  n({ id: 'p.core.0003', parent_id: 'module:p:core', node_type: 'group', label: 'Engine', number: '0003' }),
  n({ id: 'module:p:empty', parent_id: 'project:p', node_type: 'module', label: 'empty', title: 'Empty group' }),
]

describe('buildExplorerProject', () => {
  it('maps project/module/group to Project/Group/Flow and drops documents and orphans', () => {
    const project = buildExplorerProject(NODES, 'project:p')!
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
