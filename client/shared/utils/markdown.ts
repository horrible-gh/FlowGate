export function stripFrontmatter(md: string): string {
  if (!md) return md
  const m = md.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/)
  return m ? md.slice(m[0].length) : md
}

// T0004 task 2.4 (group 0460): every FlowGate document body opens with a plain
// 7-line "next_type -> ... -> target_id" header (not YAML frontmatter -- no ---
// delimiters, see stripFrontmatter above). marked's default paragraph handling
// treats those adjacent lines as ordinary Markdown soft line breaks and joins
// them into one run-together line on screen, even though the stored file has
// real LF separators (group 0458 display bug, R0001 / NR0003 -- storage was
// fine, MdViewer's rendering was not). Only a *complete*, in-order 7-field
// block at the very start of the document is wrapped in a fenced text block;
// everything else (ordinary prose, its own soft-line-break behavior) is
// untouched.
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
  const m = line.match(NEXT_HEADER_LINE_RE)
  if (!m) return null
  return m[1] === 'group_id' ? 'group' : m[1]
}

export function fenceLeadingNextHeader(md: string): string {
  if (!md || !md.startsWith('next_type:')) return md
  const lines = md.split('\n')
  let matched = 0
  while (
    matched < NEXT_HEADER_KEY_ORDER.length &&
    matched < lines.length &&
    headerLineKey(lines[matched]) === NEXT_HEADER_KEY_ORDER[matched]
  ) {
    matched++
  }
  if (matched !== NEXT_HEADER_KEY_ORDER.length) return md

  const headerLines = lines.slice(0, NEXT_HEADER_KEY_ORDER.length)
  const rest = lines.slice(NEXT_HEADER_KEY_ORDER.length).join('\n')
  return '```text\n' + headerLines.join('\n') + '\n```' + (rest ? '\n' + rest : '')
}
