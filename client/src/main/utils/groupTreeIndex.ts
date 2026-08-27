// 0454 T0004 — shared parent→child index for the group tree. GroupExplorer.vue builds
// this once per node-array change and passes the same Map instances down through every
// recursive GroupTreeNode.vue instance, instead of each level re-filtering the full
// node array (the O(n^2) pattern this file replaces).

export interface TreeIndexNode {
  id: string
  parent_id: string | null
}

export interface TreeIndex<T extends TreeIndexNode> {
  byId: Map<string, T>
  childrenByParent: Map<string | null, T[]>
}

/** One pass over `list`: an id→node lookup and a parent_id→direct-children[] lookup.
 *  Children arrays preserve the order nodes are encountered in `list`, so sibling
 *  display order is unchanged from the previous per-node `.filter()` behavior. */
export function buildTreeIndex<T extends TreeIndexNode>(list: T[]): TreeIndex<T> {
  const byId = new Map<string, T>()
  const childrenByParent = new Map<string | null, T[]>()
  for (const node of list) {
    byId.set(node.id, node)
    const key = node.parent_id
    let bucket = childrenByParent.get(key)
    if (!bucket) {
      bucket = []
      childrenByParent.set(key, bucket)
    }
    bucket.push(node)
  }
  return { byId, childrenByParent }
}

/** BFS over `childrenByParent` starting at `roots`, returning every reachable id
 *  (roots included). A visited set stops the walk on cyclic/malformed parent_id data
 *  without changing the result for well-formed trees. */
export function collectDescendantIds<T extends TreeIndexNode>(
  childrenByParent: Map<string | null, T[]>,
  roots: Iterable<string>,
): Set<string> {
  const visited = new Set<string>(roots)
  const queue = Array.from(visited)
  while (queue.length > 0) {
    const id = queue.shift()!
    const kids = childrenByParent.get(id)
    if (!kids) continue
    for (const child of kids) {
      if (!visited.has(child.id)) {
        visited.add(child.id)
        queue.push(child.id)
      }
    }
  }
  return visited
}
