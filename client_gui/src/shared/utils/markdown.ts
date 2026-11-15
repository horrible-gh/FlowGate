export function stripFrontmatter(md: string): string {
  if (!md) return md
  const m = md.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/)
  return m ? md.slice(m[0].length) : md
}

// T0004 task 2.4 (group 0460) — the leading "next-document header".
//
// A FlowGate document body can open with a plain 7-line
// "next_type -> ... -> target_id" block (not YAML frontmatter -- no ---
// delimiters, see stripFrontmatter above). It is transport metadata: the mention
// tells a worker to put it at the top of the document it creates so the chain
// records what comes next. Nothing in the server reads it back out of the stored
// body -- there is not a single `next_type` reference in
// server/modules/flow_gate/api/inbox_routes.py, and the workflow's own next step
// comes from the sequence table (_next_workflow_type in documents.py).
//
// rev5: it is therefore never document *content*, and it is removed before the
// body is handed to the Markdown renderer, exactly the way stripFrontmatter()
// removes the YAML header above it. rev1-rev4 wrapped it in a fenced `text`
// block instead, which kept the seven fields on screen as a code box at the top
// of every document -- rejected with "당장 이 문서만 봐도 헤더가 문서에 그대로
// 드러나잖아".
//
// This is display-only: `content` itself, "Markdown 복사" and "헤더 포함 복사"
// still hand back the stored bytes. Only a *complete*, in-order 7-field block at
// the very start of the document is removed; anything else (ordinary prose, a
// partial or reordered block, the same fields quoted further down a document) is
// returned untouched.
const NEXT_HEADER_KEY_ORDER = [
  'next_type',
  'next_type_detail',
  'project',
  'module',
  'group',
  'title',
  'target_id',
] as const

const NEXT_HEADER_LINE_RE = /^(next_type|next_type_detail|project|module|group_id|group|title|target_id):\s*(.*)$/

function headerLineKey(line: string): string | null {
  // R0001 rev4: split('\n') leaves the CR of a CRLF document at the end of every
  // line, and JS `.` never matches CR while a non-multiline `$` only matches the
  // very end of the string -- so `(.*)$` failed on every CRLF header line and no
  // CRLF document was ever recognised. Drop the CR before matching.
  const m = (line.endsWith('\r') ? line.slice(0, -1) : line).match(NEXT_HEADER_LINE_RE)
  if (!m) return null
  return m[1] === 'group_id' ? 'group' : m[1]
}

// R0001 rev4 -- what counts as "the very start" of the document. stripFrontmatter()
// above consumes the closing `---` plus ONE line ending, so the blank line that
// separates a frontmatter block from the body is still present when this function
// runs, and every stored FlowGate document has one. A literal
// md.startsWith('next_type:') therefore answered "not a header" for the real files
// served at flowgate.test and nothing was ever recognised. A BOM and blank
// (whitespace-only) lines are not content: skip them before deciding.
const LEADING_BLANKS_RE = /^\uFEFF?(?:[^\S\n]*\n)*/

function isBlankLine(line: string): boolean {
  return line.trim() === ''
}

export function stripLeadingNextHeader(md: string): string {
  if (!md) return md
  const lead = LEADING_BLANKS_RE.exec(md)
  const body = md.slice(lead ? lead[0].length : 0)
  if (!body.startsWith('next_type:')) return md

  const lines = body.split('\n')
  let matched = 0
  while (
    matched < NEXT_HEADER_KEY_ORDER.length &&
    matched < lines.length &&
    headerLineKey(lines[matched]) === NEXT_HEADER_KEY_ORDER[matched]
  ) {
    matched++
  }
  if (matched !== NEXT_HEADER_KEY_ORDER.length) return md

  // Drop the seven fields and the blank lines that separated them from the real
  // body, so the rendered document starts at its first heading/paragraph. The
  // remainder is put back with split('\n')/join('\n'), which is lossless: a CRLF
  // document keeps the CR that sits at the end of each of its lines.
  let start = NEXT_HEADER_KEY_ORDER.length
  while (start < lines.length && isBlankLine(lines[start])) start++
  return lines.slice(start).join('\n')
}
