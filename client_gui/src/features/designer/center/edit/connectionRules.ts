// flowgate.v02.0045 S3 — connection pre-check (D0004 §2.3 "연결 규칙 미리검사"): while the user
// drags a connection, tell at once whether the draft would take it, with the server validator's
// own criteria (L0009 §2.5). The server stays the judge (validation / confirmation).
//
// - refused: an end that is not a node of the editable draft (document, slot or revision nodes
//   have no definition to connect), or a second edge between the same two nodes (always a
//   `duplicate_edge` error).
// - allowed with warnings: structures whose execution rule is still undecided (D0004 §8) — a
//   branch (Q-2), a join (Q-4) or a cycle (Q-6). They can be saved in a draft but block
//   confirmation until the rule is decided (`rule_undecided`).
import type { DraftModel } from './draftModel'

export type ConnectionRefusal = 'not_editable' | 'duplicate'
export type ConnectionWarning = 'branch' | 'join' | 'cycle'

export type ConnectionCheck =
  | { readonly ok: true; readonly warnings: readonly ConnectionWarning[] }
  | { readonly ok: false; readonly reason: ConnectionRefusal }

function reaches(model: DraftModel, from: string, to: string): boolean {
  const seen = new Set<string>([from])
  const stack = [from]
  while (stack.length) {
    const cur = stack.pop()!
    if (cur === to) return true
    for (const edge of model.edges) {
      if (edge.source === cur && !seen.has(edge.target)) {
        seen.add(edge.target)
        stack.push(edge.target)
      }
    }
  }
  return false
}

export function checkConnection(model: DraftModel, source: string, target: string): ConnectionCheck {
  const keys = new Set(model.nodes.map((n) => n.key))
  if (!keys.has(source) || !keys.has(target)) return { ok: false, reason: 'not_editable' }
  if (model.edges.some((e) => e.source === source && e.target === target)) return { ok: false, reason: 'duplicate' }
  const warnings: ConnectionWarning[] = []
  if (model.edges.some((e) => e.source === source)) warnings.push('branch')
  if (model.edges.some((e) => e.target === target)) warnings.push('join')
  if (source === target || reaches(model, target, source)) warnings.push('cycle')
  return { ok: true, warnings }
}
