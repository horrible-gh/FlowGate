// flowgate.v02.0032 — line diff of one run change file (D0006 §3.8.3, §6.1). The server serves
// the stored run start / run end contents of the path (the Group changes diff payload) and no
// patch text, so the lines are compared here. Pure: no Vue, no server.
import type { RunFileDiff } from './types'

export interface DiffLine {
  kind: 'context' | 'add' | 'del'
  text: string
  oldNo: number | null
  newNo: number | null
}

/** Above this many old × new lines of changed middle, the middle is shown as replaced. */
export const DIFF_MAX_CELLS = 1_000_000

function splitLines(text: string | null): string[] {
  if (!text) return []
  const lines = text.replace(/\r\n/g, '\n').split('\n')
  // A final newline ends the last line; it does not start another one.
  if (lines.length > 0 && lines[lines.length - 1] === '') lines.pop()
  return lines
}

/**
 * Old → new lines: the common head and tail are kept as context and the middle is aligned by
 * longest common subsequence. A middle too large to align (DIFF_MAX_CELLS) is all removed then
 * all added — still correct, only less compact.
 */
export function diffLines(oldText: string | null, newText: string | null, maxCells = DIFF_MAX_CELLS): DiffLine[] {
  const a = splitLines(oldText)
  const b = splitLines(newText)
  let head = 0
  while (head < a.length && head < b.length && a[head] === b[head]) head += 1
  let tail = 0
  while (tail < a.length - head && tail < b.length - head && a[a.length - 1 - tail] === b[b.length - 1 - tail]) tail += 1
  const midA = a.slice(head, a.length - tail)
  const midB = b.slice(head, b.length - tail)

  const out: DiffLine[] = []
  for (let i = 0; i < head; i++) out.push({ kind: 'context', text: a[i], oldNo: i + 1, newNo: i + 1 })
  let oldNo = head
  let newNo = head
  const del = (text: string) => out.push({ kind: 'del', text, oldNo: ++oldNo, newNo: null })
  const add = (text: string) => out.push({ kind: 'add', text, oldNo: null, newNo: ++newNo })

  if (midA.length * midB.length > maxCells) {
    midA.forEach(del)
    midB.forEach(add)
  } else {
    // lcs[i][j] = common length of midA[i..] and midB[j..], one flat row-major table.
    const w = midB.length + 1
    const lcs = new Uint32Array((midA.length + 1) * w)
    for (let i = midA.length - 1; i >= 0; i--) {
      for (let j = midB.length - 1; j >= 0; j--) {
        lcs[i * w + j] = midA[i] === midB[j] ? lcs[(i + 1) * w + j + 1] + 1 : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1])
      }
    }
    let i = 0
    let j = 0
    while (i < midA.length && j < midB.length) {
      if (midA[i] === midB[j]) {
        out.push({ kind: 'context', text: midA[i], oldNo: ++oldNo, newNo: ++newNo })
        i += 1
        j += 1
      } else if (lcs[(i + 1) * w + j] >= lcs[i * w + j + 1]) {
        del(midA[i++])
      } else {
        add(midB[j++])
      }
    }
    while (i < midA.length) del(midA[i++])
    while (j < midB.length) add(midB[j++])
  }
  for (let k = 0; k < tail; k++) {
    out.push({ kind: 'context', text: a[a.length - tail + k], oldNo: ++oldNo, newNo: ++newNo })
  }
  return out
}

/** Why a file diff has no lines to show, or null when it has. */
export function diffUnavailableReason(diff: RunFileDiff): 'binary' | 'too_large' | null {
  if (diff.old.binary || diff.new.binary) return 'binary'
  if (diff.old.truncated || diff.new.truncated) return 'too_large'
  return null
}
