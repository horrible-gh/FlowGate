// flowgate.v02test.0107 F107-14: the last message (다잉메세지) of a run that ended without a reply turn.
// A remote CLI run keeps it on the run record only; the conversation shows a short preview under the
// `run_no_reply` line and opens the whole text in the existing AI notification detail window.
// The preview is display-only: the original message is never changed here.

/** Screen lines the box shows (CSS line-clamp); the "… 외 n줄" count is measured against them. */
export const DYING_PREVIEW_LINES = 3
/** Characters of the (already masked) text placed in the DOM. Far more than 3 screen lines hold. */
const PREVIEW_MAX_CHARS = 600
export const DYING_SECRET_MASK = '••••'

export interface DyingMessagePreview {
  /**
   * markdown-free, masked content lines in message order, as many as fit in PREVIEW_MAX_CHARS.
   * The box shows them joined with spaces (one paragraph) and clamps that to 3 screen lines.
   */
  lines: string[]
  /** `lines` joined with a space: the paragraph the box shows. */
  text: string
  /** true when the last entry of `lines` was cut by the character cap (it is never shown whole). */
  lastLineCut: boolean
  /** content lines of the original (blank lines, fences, rules and mark-only lines not counted). */
  totalLines: number
}

const FENCE = /^\s*(?:```|~~~)/
const RULE = /^\s*(?:[-*_]\s*){3,}$/
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(?:\|\s*:?-{2,}:?\s*)*\|?\s*$/

/** one line without its markdown marks, or '' when nothing readable is left. */
function plainLine(line: string): string {
  if (FENCE.test(line) || RULE.test(line) || TABLE_RULE.test(line)) return ''
  return line
    .replace(/^\s{0,3}#{1,6}(?:\s+|$)/, '')
    .replace(/^\s*(?:>\s?)+/, '')
    .replace(/^\s*(?:[-*+]|\d+[.)])\s+/, '')
    .replace(/^\s*\[[ xX]\]\s+/, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<\/?[A-Za-z][^>]*>/g, ' ')
    // emphasis: `**x**` `__x__` `~~x~~` first, then a single `*x*` / `_x_` around a word run.
    // `snake_case` and `2*3*4` stay: a single mark must not touch a letter or digit on its outer side.
    .replace(/\*\*|__|~~/g, '')
    .replace(/(^|[^\p{L}\p{N}*\\])\*(?=\S)(.*?\S)\*(?![\p{L}\p{N}*])/gu, '$1$2')
    .replace(/(^|[^\p{L}\p{N}_\\])_(?=\S)(.*?\S)_(?![\p{L}\p{N}_])/gu, '$1$2')
    .replace(/`+/g, '')
    .replace(/^\s*\||\|\s*$/g, '')
    .replace(/\s*\|\s*/g, ' · ')
    .replace(/\s+/g, ' ')
    .trim()
}

// Values that look like credentials. Each keeps its label and replaces the value only.
const SECRET_RULES: Array<[RegExp, string]> = [
  [/\b(Bearer|Basic|Token)(\s+)[^\s'"`,;]+/gi, `$1$2${DYING_SECRET_MASK}`],
  [/\b((?:access[_-]?|refresh[_-]?|api[_-]?|auth[_-]?)?(?:token|secret|password|passwd|pwd|api[_-]?key))(\s*[:=]\s*)(['"]?)[^\s'"`,;]+/gi, `$1$2$3${DYING_SECRET_MASK}`],
  [/\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}/g, DYING_SECRET_MASK],
  [/\b(?:sk|pk|rk|ghp|gho|ghs|ghu|github_pat|glpat|xox[abprs])[-_][A-Za-z0-9_-]{8,}/g, DYING_SECRET_MASK],
  [/(?<![\w-])--\d-[A-Za-z0-9_-]{16,}/g, DYING_SECRET_MASK],
  // a long opaque run of letters AND digits (hashes, keys, ids that work as keys).
  [/(?<![\w/.-])(?=[A-Za-z0-9_-]*\d)(?=[A-Za-z0-9_-]*[A-Za-z])[A-Za-z0-9_-]{32,}(?![\w-])/g, DYING_SECRET_MASK],
]

// ── local absolute paths ──
// A path ends at whitespace or a quote/bracket. A Windows directory name may hold spaces when a
// separator follows it (`C:\Program Files\…`, `C:/Program Files/…`); a word that looks like a file
// name (`a.txt `) ends the path there. A quoted path is taken whole up to its quote.
const SEG = String.raw`[^\s'"\x60<>()[\]{},;|]`
const WIN_SEG = String.raw`[^\s\\/'"\x60<>|:*?()[\]{},;]`
/** the start of a local absolute path: drive, UNC share, home, file URL or a POSIX root. */
const PATH_START = String.raw`(?:[A-Za-z]:[\\/]|\\\\(?=${WIN_SEG})|~[\\/]|file:\/\/|\/(?=${SEG}))`
// no word, dot, colon, slash or backslash right before: a URL's path (`https://h/x`) or `ko/ja` is never one.
const BEFORE = String.raw`(?<![\p{L}\p{N}_.:/\\~-])`
/** one Windows directory name and its separator; inner spaces allowed, not after a file-like word. */
const WIN_DIR = String.raw`(?:(?!${WIN_SEG}*\.[A-Za-z0-9]{1,8} )${WIN_SEG}+ +)*${WIN_SEG}+[\\/]`
const WINDOWS_PATH = new RegExp(String.raw`${BEFORE}(?:[A-Za-z]:|\\\\${WIN_SEG}+)[\\/](?:${WIN_DIR})*${WIN_SEG}*`, 'gu')
// a home path takes no spaces (`~/a.ts and/or b` must keep `and/or`).
const HOME_PATH = new RegExp(String.raw`${BEFORE}~[\\/]${SEG}*`, 'gu')
const FILE_URL = /\bfile:\/\/[^\s'"`<>]*/gi
const POSIX_PATH = new RegExp(String.raw`${BEFORE}\/${SEG}+(?:\/${SEG}*)*`, 'gu')
const QUOTED_PATH = new RegExp(String.raw`(["'\x60])(${PATH_START}[^"'\x60\n]*)\1`, 'gu')
/**
 * a lone root (`/tmp`, `/home`) or a file right under the root (`/config.json`, `/.env`) is still a
 * machine location; other one-segment words (`/help`) are not.
 */
const POSIX_ROOTS = new Set(['bin', 'boot', 'data', 'dev', 'etc', 'home', 'lib', 'lib64', 'media', 'mnt', 'opt', 'private',
  'proc', 'root', 'run', 'sbin', 'srv', 'sys', 'tmp', 'usr', 'var', 'Users', 'Volumes', 'Applications', 'Library', 'System',
  'workspace', 'workspaces', 'nix', 'snap'])

/**
 * whether a POSIX-looking match is a machine path. An API route (`/api/v1/…`, `/flowgate/api/…`: `api`
 * in its first two segments, not under a machine root) is not; `/home/u/server/api/x.py` still is.
 */
function isLocalPosix(path: string): boolean {
  const segments = path.split('/').filter(Boolean)
  if (!segments.length) return false
  const root = POSIX_ROOTS.has(segments[0])
  if (!root && segments.slice(0, 2).includes('api')) return false
  const rootFile = segments.length === 1 && /\.[^.\s]/.test(segments[0])
  return root || rootFile || segments.length >= 2 || path.endsWith('/')
}

function maskPaths(text: string, workFolder: string): string {
  return text
    .replace(QUOTED_PATH, (whole, quote: string, path: string) =>
      /^(?:[A-Za-z]:|\\\\|~|file:)/.test(path) || isLocalPosix(path) ? `${quote}${workFolder}${quote}` : whole)
    .replace(FILE_URL, workFolder)
    .replace(WINDOWS_PATH, workFolder)
    .replace(HOME_PATH, workFolder)
    .replace(POSIX_PATH, (path) => (isLocalPosix(path) ? workFolder : path))
}

/**
 * the preview's masking: token-like values become `••••`, local absolute paths `workFolder` — each
 * to its end. Applied to whole lines before any length cap, so a cut never leaves part of a value.
 */
export function maskDyingPreview(text: string, workFolder: string): string {
  let out = text
  for (const [pattern, replacement] of SECRET_RULES) out = out.replace(pattern, replacement)
  return maskPaths(out, workFolder)
}

/** `line` cut to `max` characters, moved back so a mask or the work-folder label is never split. */
function capLine(line: string, max: number, labels: string[]): string {
  const chars = Array.from(line)
  if (chars.length <= max) return line
  let cut = chars.slice(0, max).join('').length
  for (const label of labels) {
    for (let at = line.indexOf(label); at !== -1 && at < cut; at = line.indexOf(label, at + 1)) {
      if (at + label.length > cut) cut = at
    }
  }
  return line.slice(0, cut).trimEnd()
}

/**
 * the preview of a last message, or null when no readable text is in it (empty, or marks only such
 * as a lone code fence): the badge then stays alone. `workFolder` is the localized label a local
 * path is shown as (`작업 폴더/…`).
 */
export function dyingMessagePreview(message: string | null | undefined, workFolder: string): DyingMessagePreview | null {
  if (typeof message !== 'string' || !message.trim()) return null
  const content = message.replace(/\r\n?/g, '\n').split('\n').map(plainLine).filter(Boolean)
  if (!content.length) return null
  const lines: string[] = []
  let used = 0
  let lastLineCut = false
  for (const line of content) {
    const masked = maskDyingPreview(line, workFolder)
    const room = PREVIEW_MAX_CHARS - used
    if (room <= 0) break
    const capped = capLine(masked, room, [DYING_SECRET_MASK, workFolder])
    if (capped !== masked) {
      if (capped) lines.push(capped)
      lastLineCut = Boolean(capped)
      break
    }
    lines.push(masked)
    used += Array.from(masked).length + 1
  }
  return { lines, text: lines.join(' '), lastLineCut, totalLines: content.length }
}

/** the screen boxes of one preview line's span (`getClientRects()` of it). */
export type LineBoxes = ArrayLike<{ top: number; bottom: number; right: number; width: number; height: number }>

/** the clamped paragraph: its bottom and right edges and the width of the "…" the clamp draws at the end of its last row. */
export interface DyingClip {
  bottom: number
  right: number
  ellipsis: number
}

/**
 * content lines of the original the box does not show whole: every line from the first one that is
 * not shown whole. A line is not shown whole when
 * - one of its screen boxes ends below the 3-line clip (folded past it), or
 * - the paragraph overflows the clip and one of its boxes on the last shown row reaches the place of the
 *   clamp's "…" (`clip.right - clip.ellipsis`): the ellipsis covers its last characters, or
 * - the character cap cut it, or it never got into the DOM.
 * Returns null when there is no layout to measure (detached or hidden box): the caller keeps its
 * previous value and measures again later.
 */
export function hiddenDyingLines(preview: DyingMessagePreview, boxes: LineBoxes[], clip: DyingClip): number | null {
  const drawn = (r: { width: number; height: number }) => r.width > 0 || r.height > 0
  const rectsOf = preview.lines.map((_, i) => Array.from(boxes[i] ?? []).filter(drawn))
  const all = rectsOf.flat()
  if (!all.length) return null
  const inside = (r: { bottom: number }) => r.bottom <= clip.bottom + 0.5
  // the clamp draws "…" only when something is laid out below the clip.
  const overflows = all.some((r) => !inside(r))
  const shown = all.filter(inside)
  const lastRowBottom = shown.length ? Math.max(...shown.map((r) => r.bottom)) : -Infinity
  const ellipsisFrom = clip.right - Math.max(0, clip.ellipsis)
  const covered = (r: { bottom: number; right: number; height: number }) =>
    overflows && Math.abs(r.bottom - lastRowBottom) < Math.max(r.height, 1) / 2 && r.right > ellipsisFrom + 0.5
  let whole = 0
  for (let i = 0; i < preview.lines.length; i += 1) {
    const rects = rectsOf[i]
    const isCut = preview.lastLineCut && i === preview.lines.length - 1
    if (isCut || !rects.length || rects.some((r) => !inside(r) || covered(r))) break
    whole += 1
  }
  return preview.totalLines - whole
}
