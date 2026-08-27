/**
 * 0449 TR0005 rev1's load-scale payload, extracted so 0454 T0004's regression suite can
 * reuse the SAME deterministic generator instead of copying a large static JSON (T0004 §
 * Vitest 회귀 검증 item 1). Default options reproduce the exact node count and field shape
 * `explorerGroupTreeInflight.0449.spec.ts` pins — 5,883 nodes, 1.25 MB serialized — so the
 * 0449 file's own `buildLoadNodes()` call keeps importing from here unchanged instead of
 * defining its own copy.
 *
 * The options below are ADDITIVE only: every one of them defaults to the pre-0454 shape
 * (no terminal groups, no nested subgroups, a single 'T' doc type), so `buildLoadNodes()`
 * with no arguments is byte-for-byte the same fixture 0449 already depends on.
 */
export interface LoadShapeOptions {
  /** Mark some groups is_final_approved / is_discarded so the hide-toggle actually
   *  changes the visible set. Off by default (0449's fixture has none). */
  markTerminal?: boolean
  /** Add one subgroup (+ 3 documents) under every 10th top-level group, for a deep
   *  (project > module > group > subgroup > document) branch. Off by default. */
  nestSubgroups?: boolean
  /** Document type codes to cycle through. Defaults to a single 'T', matching 0449. */
  docTypes?: string[]
}

export function buildLoadNodes(options: LoadShapeOptions = {}): Array<Record<string, unknown>> {
  const { markTerminal = false, nestSubgroups = false, docTypes = ['T'] } = options
  const nodes: Array<Record<string, unknown>> = [
    { id: 'project:p1', parent_id: null, node_type: 'project', label: 'P', permissions: ['read'] },
  ]
  for (let m = 0; m < 2; m += 1) {
    const moduleId = `module:p1:m${m}`
    nodes.push({ id: moduleId, parent_id: 'project:p1', node_type: 'module', label: `m${m}`, permissions: ['read'] })
    for (let g = 0; g < 84; g += 1) {
      const groupId = `p1.m${m}.${String(g).padStart(4, '0')}`
      const globalGroupIndex = m * 84 + g
      // Deterministic and disjoint: every 5th group final-approved, every 7th (that
      // isn't already final-approved) discarded — leaves most groups untouched so the
      // "in progress, stays visible" case still dominates the fixture.
      const isFinalApproved = markTerminal && globalGroupIndex % 5 === 0
      const isDiscarded = markTerminal && !isFinalApproved && globalGroupIndex % 7 === 0
      nodes.push({
        id: groupId, parent_id: moduleId, node_type: 'group', label: `Group ${groupId}`,
        is_final_approved: isFinalApproved, is_discarded: isDiscarded, permissions: ['read'],
      })
      for (let d = 0; d < 34; d += 1) {
        const typeCode = docTypes[d % docTypes.length]
        nodes.push({
          id: `${groupId}.${String(d).padStart(4, '0')}-${typeCode}`,
          parent_id: groupId,
          node_type: 'document',
          label: `[${typeCode}] document ${d} of ${groupId}`,
          type_code: typeCode,
          has_md: true,
          md_path: `work/${groupId}/${String(d).padStart(4, '0')}-${typeCode}/document.md`,
          permissions: ['read'],
        })
      }
      if (nestSubgroups && globalGroupIndex % 10 === 0) {
        const subGroupId = `${groupId}.sub`
        nodes.push({
          id: subGroupId, parent_id: groupId, node_type: 'group', label: `Sub ${subGroupId}`,
          is_final_approved: false, is_discarded: false, permissions: ['read'],
        })
        for (let d = 0; d < 3; d += 1) {
          const typeCode = docTypes[d % docTypes.length]
          nodes.push({
            id: `${subGroupId}.${String(d).padStart(4, '0')}-${typeCode}`,
            parent_id: subGroupId,
            node_type: 'document',
            label: `[${typeCode}] sub document ${d} of ${subGroupId}`,
            type_code: typeCode,
            has_md: true,
            md_path: `work/${subGroupId}/${String(d).padStart(4, '0')}-${typeCode}/document.md`,
            permissions: ['read'],
          })
        }
      }
    }
  }
  return nodes
}
