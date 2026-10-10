import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// The registry module checks the real registrations when it loads and throws on a broken one.
// It is loaded here (not imported at the top) so that the file-level checks still report.
type RegistryModule = typeof import('@/features/designer/inspector/inspectorRegistry')
let registry: RegistryModule | null = null
let registryLoadError: unknown = null
try {
  registry = await import('@/features/designer/inspector/inspectorRegistry')
} catch (error) {
  registryLoadError = error
}

function loadedRegistry(): RegistryModule {
  if (!registry) throw registryLoadError
  return registry
}

// flowgate.v02.0048 T#1 — source-level guards of the bottom Inspector structure (D#1 §3.1 / §3.2).
// Data driven: the rule tables below are applied to every folder found under
// `inspector/features/`, so a new feature folder is checked without editing this spec. The
// checks are functions of a root (an Inspector folder, or a fixture shaped like one) returning
// the violations, run on the real source (expect none) and on the boundary fixtures under
// fixtures/structure0048/ (expect them to be caught). Owned by T#1; T#3 / T#4 add B9~B11 / B15
// once, feature sets never edit it.
// flowgate.v02.0048 T#3: B8 lets composables use `@/features/document` and `useInspectorResource`;
// B9 (no transport in feature screens / declarations), B10 (the Designer reaches features/document
// only through its index) and B11 (features/document reaches only the shared transport and its own
// files; its index is `export *` lines) — checked on src/features and on fixtures/structure0048/document.
// flowgate.v02.0048 T#4: B15 (`window.open` only in openDocument.ts and only with 'noopener', D#1 §9.2 /
// §9.5) — checked on the Inspector root and on fixtures/structure0048/open.

const SRC = resolve(__dirname, '../../../src')
const FEATURES_ROOT = join(SRC, 'features')
const INSPECTOR = join(SRC, 'features/designer/inspector')
const FIXTURES = join(__dirname, 'fixtures/structure0048')

// --- Rule tables ---------------------------------------------------------------------------------

/** B1 — what may sit directly in inspector/ (D#1 §2.5). Not yet created entries may be missing. */
const ROOT_ENTRIES = [
  'DesignerInspector.vue',
  'InspectorHeader.vue',
  'inspectorTabs.ts',
  'inspectorRegistry.ts',
  'inspectorTarget.ts',
  'inspectorHost.ts',
  'useInspectorResource.ts',
  'openDocument.ts',
  'parts',
  'features',
]

/** B1 — not source: the folder's ignore rule that keeps parts/ tracked against the root `parts/` rule. */
const ROOT_NON_SOURCE = ['.gitignore']

const FRAME_FILES = ['DesignerInspector.vue', 'InspectorHeader.vue', 'inspectorTabs.ts']

const UI = (depth: number) => new RegExp(`^${'\\.\\./'.repeat(depth)}components/ui/[A-Za-z]+\\.vue$`)
const OWN = /^\.\/[A-Za-z]+(\.vue)?$/
const API_STORE = /^@shared\/|^@\/features\/|^pinia$|^axios$|^vue-router$/
const BODY_IO = /fetch\(|\baxios\b|defineStore|useProjectStore|localStorage|sessionStorage/

interface ImportRule {
  /** Allowed import specifiers. `typeOnly` ones must be `import type`. */
  allowed: readonly (RegExp | { re: RegExp; typeOnly: true })[]
  /** Forbidden import specifiers (reported even when also allowed). */
  forbidden?: RegExp
  /** Forbidden source text. */
  body?: RegExp
}

/** B2 — the Frame: the 0036 allow list (own files cover registry / target / host), no feature, parts, API, store. */
const FRAME_RULE: ImportRule = {
  allowed: [/^vue$/, /^vue-i18n$/, UI(3), OWN, /^\.\.\/center\/canvas\/flowNodeKinds$/, /^\.\.\/center\/graph\/flowGraph$/],
  forbidden: /^\.\/(features|parts)\/|^@shared\/|^@\/features\/|^pinia$|^axios$|^vue-router$|designerCenterSession|\/shell\//,
  body: /fetch\(|localStorage|sessionStorage|defineStore/,
}

/** B7 — parts: display only. */
const PARTS_RULE: ImportRule = {
  allowed: [/^vue$/, /^vue-i18n$/, UI(4), OWN],
  forbidden: /^@shared\/|^@\/features\/|^pinia$|^axios$|^vue-router$|^\.\.\/features\//,
  body: BODY_IO,
}

/** B8 (with B7 for *Model.ts) — feature folder files by kind (D#1 §2.4). */
const FEATURE_RULES: readonly { kind: string; file: RegExp; rule: ImportRule }[] = [
  {
    kind: 'register',
    file: /^register\.ts$/,
    rule: { allowed: [OWN, { re: /^\.\.\/\.\.\/inspectorRegistry$/, typeOnly: true }], forbidden: API_STORE, body: BODY_IO },
  },
  { kind: 'messages', file: /^messages\.ts$/, rule: { allowed: [], body: BODY_IO } },
  {
    kind: 'model',
    file: /^[a-z][A-Za-z]*Model\.ts$/,
    rule: {
      allowed: [/^\.\/[A-Za-z]+Model$/, { re: /^\.\.\/\.\.\/inspectorTarget$/, typeOnly: true }],
      forbidden: /^vue$|^@shared\/|^@\/features\/|^pinia$|^axios$|^vue-router$/,
      body: BODY_IO,
    },
  },
  {
    kind: 'composable',
    file: /^use[A-Z][A-Za-z]*\.ts$/,
    rule: {
      allowed: [
        /^vue$/,
        OWN,
        /^\.\.\/\.\.\/(inspectorTarget|inspectorHost|openDocument|useInspectorResource)$/,
        /^@\/core\/auth\/currentUser$/,
        /^@\/features\/document$/,
      ],
      forbidden: /^@shared\/|^pinia$|^axios$|^vue-router$/,
      body: /\baxios\b|defineStore|localStorage|sessionStorage/,
    },
  },
  {
    kind: 'component',
    file: /^[A-Z][A-Za-z]*\.vue$/,
    rule: {
      allowed: [
        /^vue$/,
        /^vue-i18n$/,
        UI(5),
        OWN,
        /^\.\.\/\.\.\/parts\/[A-Za-z]+(\.vue)?$/,
        /^\.\.\/\.\.\/(inspectorTarget|inspectorHost|openDocument)$/,
      ],
      forbidden: API_STORE,
      body: BODY_IO,
    },
  },
]

/** B9 — feature files that only show or declare: no transport of their own. */
const B9_FILE = /^(register|messages)\.ts$|\.vue$/
const B9_TRANSPORT = /fetch\(|\baxios\b|@shared\/api\b/

/** B11 — what features/document may import besides its own files: the shared transport. */
const DOCUMENT_SHARED = /^@shared\/(api|apiErrors)$/
/** B11 — the one line kind of features/document/index.ts (comments and blank lines aside). */
const DOCUMENT_INDEX_LINE = /^export \* from '\.\/[A-Za-z/]+'$/

/** B15 — the one Inspector file that may open a browser tab (D#1 §9.2). */
const OPEN_DOCUMENT_FILE = 'openDocument.ts'
const WINDOW_OPEN = /\b(?:window|globalThis|self)\s*\.\s*open\s*\(/g
/**
 * B15 — the features argument (the third) must be one plain string literal, so the rule can be read
 * from the source, holding a `noopener` feature that is on: the bare name, `yes`, `true` or a non-zero
 * integer (HTML window features). `noopener` in the URL or target, or `noopener=no`, does not count.
 */
const FEATURES_LITERAL = /^'([^'\\]*)'$|^"([^"\\]*)"$|^`([^`\\$]*)`$/
const NOOPENER_ON = /^noopener(?:=(?:yes|true|0*[1-9]\d*))?$/i

/**
 * B14 — undecided features (D#1 §8.4). flowgate.v02.0051 (turn 7): an undecided item may be named and
 * drawn — disabled, as the v7 screen shows it (0051-R §2.3) — but never operated: no feature file calls a
 * function, or wires a template handler, named after one. Comments are not source here.
 */
const UNDECIDED_MARKER = /expand|delegat|memo|ai_delivery|tsc/i
const UNDECIDED_CALL = /(?<!function\s)(?<!\.)\b([A-Za-z_$][\w$]*)\s*\(/g
const UNDECIDED_HANDLER = /@(?:click|submit|change|input|keydown|keyup|update:[\w-]+)(?:\.[\w.]+)*="\s*([A-Za-z_$][\w$.]*)\s*"/g
const COMMENTS = /\/\*[\s\S]*?\*\/|<!--[\s\S]*?-->|(^|[^:'"`])\/\/[^\n]*/g

// --- Checks ----------------------------------------------------------------------------------------

const read = (file: string) => readFileSync(file, 'utf8')
const isDir = (path: string) => existsSync(path) && statSync(path).isDirectory()
const list = (dir: string) => (isDir(dir) ? readdirSync(dir).sort() : [])

interface ImportRef {
  spec: string
  typeOnly: boolean
}

function importsOf(src: string): ImportRef[] {
  const statics = [...src.matchAll(/^\s*import\s([^'"]*?)\s*(?:from\s*)?['"]([^'"]+)['"]/gm)].map((m) => ({
    spec: m[2],
    typeOnly: /^type\s/.test(m[1].trim()),
  }))
  const dynamics = [...src.matchAll(/\bimport\(\s*['"]([^'"]+)['"]\s*\)/g)].map((m) => ({ spec: m[1], typeOnly: false }))
  return [...statics, ...dynamics]
}

/** Module specifiers a file depends on: its imports and its `export … from` re-exports. */
function specifiersOf(src: string): string[] {
  const reexports = [...src.matchAll(/^\s*export\s[^'"]*?\sfrom\s*['"]([^'"]+)['"]/gm)].map((m) => m[1])
  return [...importsOf(src).map((ref) => ref.spec), ...reexports]
}

/** .ts / .vue files under a folder, recursively. */
function sourceFiles(dir: string): string[] {
  return list(dir).flatMap((name) => {
    const full = join(dir, name)
    if (isDir(full)) return sourceFiles(full)
    return /\.(ts|vue)$/.test(name) ? [full] : []
  })
}

/** `path` is `root` or lies under it. */
function inside(root: string, path: string): boolean {
  const rel = relative(root, path)
  return rel === '' || (!rel.startsWith('..') && !isAbsolute(rel))
}

const slashes = (path: string) => path.replace(/\\/g, '/')

function ruleViolations(code: string, label: string, src: string, rule: ImportRule): string[] {
  const out: string[] = []
  for (const { spec, typeOnly } of importsOf(src)) {
    const ok = rule.allowed.some((allowed) => (allowed instanceof RegExp ? allowed.test(spec) : allowed.re.test(spec) && typeOnly))
    if (!ok || rule.forbidden?.test(spec)) out.push(`${code} ${label}: import '${spec}'`)
  }
  const body = rule.body ? src.match(rule.body) : null
  if (body) out.push(`${code} ${label}: uses '${body[0]}'`)
  return out
}

/** Feature folder names under `<root>/features/`. */
function featureFolders(root: string): string[] {
  return list(join(root, 'features')).filter((name) => isDir(join(root, 'features', name)))
}

/** B1, B2, B7 (parts) for an Inspector root. */
function frameViolations(root: string): string[] {
  const out: string[] = []
  for (const name of list(root)) if (!ROOT_ENTRIES.includes(name) && !ROOT_NON_SOURCE.includes(name)) out.push(`B1 ${name}: not an Inspector root entry`)
  for (const name of FRAME_FILES) {
    if (existsSync(join(root, name))) out.push(...ruleViolations('B2', name, read(join(root, name)), FRAME_RULE))
  }
  for (const name of list(join(root, 'parts'))) {
    const label = `parts/${name}`
    if (isDir(join(root, 'parts', name))) out.push(`B7 ${label}: parts holds no folder`)
    else out.push(...ruleViolations('B7', label, read(join(root, 'parts', name)), PARTS_RULE))
  }
  return out
}

/** B3, B6, B7 (*Model.ts), B8 for one feature folder. */
function featureViolations(root: string, key: string): string[] {
  const out: string[] = []
  const dir = join(root, 'features', key)
  const others = featureFolders(root).filter((name) => name !== key)
  for (const required of ['register.ts', 'messages.ts']) {
    if (!existsSync(join(dir, required))) out.push(`B3 features/${key}: no ${required}`)
  }
  for (const name of list(dir)) {
    const label = `features/${key}/${name}`
    if (isDir(join(dir, name))) {
      out.push(`B8 ${label}: a feature folder holds no sub-folder`)
      continue
    }
    const src = read(join(dir, name))
    for (const { spec } of importsOf(src)) {
      const sibling = /^\.\.\/([A-Za-z]+)\//.exec(spec)?.[1] ?? /inspector\/features\/([A-Za-z]+)/.exec(spec)?.[1]
      if (sibling && others.includes(sibling)) out.push(`B6 ${label}: imports feature '${sibling}'`)
    }
    const kind = FEATURE_RULES.find((candidate) => candidate.file.test(name))
    if (!kind) {
      out.push(`B8 ${label}: not a feature file kind (register / messages / *Model.ts / use*.ts / *.vue)`)
      continue
    }
    out.push(...ruleViolations(kind.kind === 'model' ? 'B7' : 'B8', label, src, kind.rule))
    const transport = B9_FILE.test(name) ? src.match(B9_TRANSPORT) : null
    if (transport) out.push(`B9 ${label}: uses '${transport[0]}'`)
  }
  return out
}

/**
 * B10 — every Designer file reaches features/document only through `@/features/document`: a deep alias
 * and any relative path into the folder (its root, its index or a file in it) are violations. `features` = a src/features root.
 */
function documentImportViolations(features: string): string[] {
  const designer = join(features, 'designer')
  const document = join(features, 'document')
  const out: string[] = []
  for (const file of sourceFiles(designer)) {
    for (const spec of specifiersOf(read(file))) {
      const target = spec.startsWith('.') ? resolve(dirname(file), spec) : null
      const deep = /^@\/features\/document\//.test(spec) || (target !== null && inside(document, target))
      if (deep) out.push(`B10 ${slashes(relative(features, file))}: import '${spec}'`)
    }
  }
  return out
}

/**
 * B11 — features/document imports the shared transport and its own files only, holds no screen,
 * and its index holds `export *` lines only.
 */
function documentFeatureViolations(document: string): string[] {
  const out: string[] = []
  for (const file of sourceFiles(document)) {
    const label = `document/${slashes(relative(document, file))}`
    if (file.endsWith('.vue')) out.push(`B11 ${label}: features/document holds no screen`)
    for (const spec of specifiersOf(read(file))) {
      const own = spec.startsWith('.') && inside(document, resolve(dirname(file), spec))
      if (!own && !DOCUMENT_SHARED.test(spec)) out.push(`B11 ${label}: import '${spec}'`)
    }
  }
  const index = join(document, 'index.ts')
  if (!existsSync(index)) return [...out, 'B11 document/index.ts: missing']
  for (const line of read(index).split(/\r?\n/).map((text) => text.trim())) {
    if (line && !line.startsWith('//') && !DOCUMENT_INDEX_LINE.test(line)) out.push(`B11 document/index.ts: '${line}' is not an export * line`)
  }
  return out
}

/**
 * The top-level arguments of a call whose opening parenthesis ends just before `start`. Commas and
 * parentheses inside brackets or string literals do not split or close it.
 */
function callArguments(src: string, start: number): string[] {
  const args: string[] = []
  let depth = 0
  let quote = ''
  let from = start
  for (let i = start; i < src.length; i++) {
    const c = src[i]
    if (quote) {
      if (c === '\\') i++
      else if (c === quote) quote = ''
    } else if (c === "'" || c === '"' || c === '`') quote = c
    else if ('([{'.includes(c)) depth++
    else if (')]}'.includes(c) && depth > 0) depth--
    else if ((c === ',' || c === ')') && depth === 0) {
      args.push(src.slice(from, i).trim())
      if (c === ')') break
      from = i + 1
    }
  }
  return args.filter(Boolean)
}

/** B15 — the call's features argument turns noopener on. */
function opensWithNoopener(args: string[]): boolean {
  const literal = FEATURES_LITERAL.exec(args[2] ?? '')
  if (!literal) return false
  const features = literal[1] ?? literal[2] ?? literal[3]
  return features
    .replace(/\s*=\s*/g, '=')
    .split(/[\s,]+/)
    .some((feature) => NOOPENER_ON.test(feature))
}

/** B15 — `window.open` anywhere under an Inspector root: only in openDocument.ts, and always with 'noopener'. */
function windowOpenViolations(root: string): string[] {
  const out = new Set<string>()
  for (const file of sourceFiles(root)) {
    const label = slashes(relative(root, file))
    const src = read(file)
    for (const call of src.matchAll(WINDOW_OPEN)) {
      if (label !== OPEN_DOCUMENT_FILE) out.add(`B15 ${label}: window.open outside ${OPEN_DOCUMENT_FILE}`)
      else if (!opensWithNoopener(callArguments(src, (call.index ?? 0) + call[0].length))) out.add(`B15 ${label}: window.open without 'noopener'`)
    }
  }
  return [...out]
}

/** Every file-level violation of an Inspector root. */
function boundaryViolations(root: string): string[] {
  return [...frameViolations(root), ...featureFolders(root).flatMap((key) => featureViolations(root, key))]
}

const keyPaths = (value: unknown, prefix = ''): string[] =>
  value && typeof value === 'object'
    ? Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => [`${prefix}${key}`, ...keyPaths(child, `${prefix}${key}.`)])
    : []

/** B4 / B5 (registry contract), B12 (same keys in ko / en / ja). */
function registryViolations(modules: Record<string, unknown>): string[] {
  const out = loadedRegistry().registrationViolations(modules).map((line) => `B4/B5 ${line}`)
  for (const [path, module] of Object.entries(modules)) {
    const folder = /features\/([^/]+)\/register\.ts$/.exec(path)?.[1] ?? path
    const entry = (module as { default?: { key?: unknown; messages?: Record<string, unknown> } }).default
    if (!entry) continue
    const messages = entry.messages ?? {}
    const ko = keyPaths(messages.ko).sort()
    for (const locale of ['en', 'ja']) {
      const other = keyPaths(messages[locale]).sort()
      if (JSON.stringify(other) !== JSON.stringify(ko)) out.push(`B12 features/${folder}: ${locale} keys differ from ko`)
    }
  }
  return out
}

/** B14 — every feature file of an Inspector root: no call or template handler named after an undecided feature. */
function undecidedViolations(root: string): string[] {
  const out = new Set<string>()
  for (const key of featureFolders(root)) {
    const dir = join(root, 'features', key)
    for (const name of list(dir)) {
      if (isDir(join(dir, name)) || !/\.(ts|vue)$/.test(name)) continue
      const label = `features/${key}/${name}`
      const src = read(join(dir, name)).replace(COMMENTS, '$1')
      for (const call of src.matchAll(UNDECIDED_CALL)) if (UNDECIDED_MARKER.test(call[1])) out.add(`B14 ${label}: calls '${call[1]}('`)
      for (const handler of src.matchAll(UNDECIDED_HANDLER)) if (UNDECIDED_MARKER.test(handler[1])) out.add(`B14 ${label}: handler '${handler[1]}'`)
    }
  }
  return [...out]
}

const codes = (violations: string[]) => [...new Set(violations.map((line) => line.split(' ')[0]))].sort()

// --- Real source ----------------------------------------------------------------------------------

const FEATURES = featureFolders(INSPECTOR)
const REAL_MODULES = import.meta.glob('../../../src/features/designer/inspector/features/*/register.ts', { eager: true })

describe('features/designer/inspector structure (flowgate.v02.0048)', () => {
  it('B1 / B2 / B7: the root holds only the D#1 §2.5 entries; the Frame and parts stay free of features, API and store', () => {
    expect(frameViolations(INSPECTOR)).toEqual([])
  })

  it.each(FEATURES.length ? FEATURES : ['(no feature folder yet)'])('B3 / B6 / B7 / B8 / B9: feature folder %s', (key) => {
    if (!FEATURES.length) {
      expect(isDir(join(INSPECTOR, 'features')) ? featureFolders(INSPECTOR) : []).toEqual([])
      return
    }
    expect(featureViolations(INSPECTOR, key)).toEqual([])
  })

  it('B4 / B5 / B12: the registrations keep the contract and the three locales', () => {
    expect(registryViolations(REAL_MODULES)).toEqual([])
  })

  it('B14: an undecided feature may be named and drawn, but no feature file calls or wires an operation named after one', () => {
    expect(undecidedViolations(INSPECTOR)).toEqual([])
  })

  it('B10: the Designer imports features/document only through @/features/document', () => {
    expect(documentImportViolations(FEATURES_ROOT)).toEqual([])
  })

  it('B11: features/document reaches only the shared transport and its own files; its index is export * lines', () => {
    expect(documentFeatureViolations(join(FEATURES_ROOT, 'document'))).toEqual([])
  })

  it("B15: the Inspector opens a browser tab only in openDocument.ts, and only with 'noopener'", () => {
    expect(windowOpenViolations(INSPECTOR)).toEqual([])
  })

  it('B13: the module registry (import.meta.glob) holds exactly the feature folders with a register.ts', () => {
    const folders = FEATURES.filter((key) => existsSync(join(INSPECTOR, 'features', key, 'register.ts')))
    expect(loadedRegistry().inspectorRegistry.entries.map((entry) => entry.key).sort()).toEqual(folders)
    expect(Object.keys(REAL_MODULES).length).toBe(folders.length)
  })
})

// --- Boundary fixtures: the checks must fail on them --------------------------------------------

const REGISTRY_FIXTURE = import.meta.glob('./fixtures/structure0048/registry/features/*/register.ts', { eager: true })

describe('structure.0048 checks catch boundary violations (fixtures/structure0048)', () => {
  it('a clean fixture with every feature file kind passes', () => {
    expect(boundaryViolations(join(FIXTURES, 'clean'))).toEqual([])
  })

  it('the boundary fixture fails with each violation named', () => {
    const violations = boundaryViolations(join(FIXTURES, 'boundary'))
    expect(violations).toEqual(
      expect.arrayContaining([
        'B1 stray.ts: not an Inspector root entry',
        "B2 DesignerInspector.vue: import '@/features/project'",
        "B2 DesignerInspector.vue: import './parts/StatusChip.vue'",
        "B2 DesignerInspector.vue: uses 'fetch('",
        "B7 parts/BadPart.vue: import '@shared/api'",
        "B6 features/alpha/AlphaTab.vue: imports feature 'beta'",
        "B8 features/alpha/AlphaTab.vue: import '../beta/betaModel'",
        "B7 features/beta/betaModel.ts: import 'vue'",
        "B7 features/beta/betaModel.ts: import '@/features/document'",
        'B3 features/gamma: no register.ts',
        "B8 features/delta/useDelta.ts: import '@/features/project'",
        'B8 features/delta/nested: a feature folder holds no sub-folder',
        'B8 features/delta/deltaStore.ts: not a feature file kind (register / messages / *Model.ts / use*.ts / *.vue)',
        "B8 features/epsilon/register.ts: import '../../inspectorRegistry'",
      ]),
    )
    expect(codes(violations)).toEqual(['B1', 'B2', 'B3', 'B6', 'B7', 'B8'])
    // The type-only registry import of the same register.ts is allowed.
    expect(violations.filter((line) => line.startsWith('B8 features/epsilon/register.ts'))).toHaveLength(1)
  })

  it('the document fixtures: a clean Designer + features/document passes; each B9 / B10 / B11 violation is named', () => {
    const clean = join(FIXTURES, 'document/clean')
    expect(boundaryViolations(join(clean, 'designer/inspector'))).toEqual([])
    expect(documentImportViolations(clean)).toEqual([])
    expect(documentFeatureViolations(join(clean, 'document'))).toEqual([])

    const boundary = join(FIXTURES, 'document/boundary')
    const feature = boundaryViolations(join(boundary, 'designer/inspector'))
    expect(feature).toEqual(
      expect.arrayContaining([
        "B9 features/zeta/ZetaTab.vue: uses 'axios'",
        "B9 features/zeta/register.ts: uses '@shared/api'",
        "B9 features/zeta/messages.ts: uses 'fetch('",
        "B8 features/zeta/useZeta.ts: import '@/features/document/api/qa'",
      ]),
    )
    expect(codes(feature)).toEqual(['B8', 'B9'])
    expect(documentImportViolations(boundary).sort()).toEqual([
      "B10 designer/center/peek.ts: import '../../document/api/review'",
      "B10 designer/center/rootPeek.ts: import '../../document'",
      "B10 designer/inspector/features/zeta/useZeta.ts: import '@/features/document/api/qa'",
    ])
    expect(documentFeatureViolations(join(boundary, 'document')).sort()).toEqual([
      'B11 document/DocumentCard.vue: features/document holds no screen',
      "B11 document/api/attachments.ts: import 'axios'",
      "B11 document/api/qa.ts: import '@/features/project'",
      "B11 document/api/qa.ts: import 'vue'",
      "B11 document/api/review.ts: import '../../designer/inspector/inspectorTarget'",
      "B11 document/index.ts: 'export * from 'vue'' is not an export * line",
      "B11 document/index.ts: 'export { documentRead } from './api/document'' is not an export * line",
      "B11 document/index.ts: import 'vue'",
    ])
  })

  it("the open fixtures: openDocument.ts with 'noopener' passes; a window.open elsewhere or without 'noopener' is named", () => {
    expect(windowOpenViolations(join(FIXTURES, 'open/clean'))).toEqual([])
    expect(windowOpenViolations(join(FIXTURES, 'open/boundary')).sort()).toEqual([
      'B15 features/omega/useOmega.ts: window.open outside openDocument.ts',
      "B15 openDocument.ts: window.open without 'noopener'",
      'B15 parts/LinkPart.vue: window.open outside openDocument.ts',
    ])
  })

  it("the open fixtures: only a features argument that turns noopener on counts as 'noopener'", () => {
    expect(windowOpenViolations(join(FIXTURES, 'open/noopener-variants'))).toEqual([])
    for (const root of ['noopener-in-url', 'noopener-as-target', 'noopener-no', 'noopener-zero', 'noopener-computed']) {
      expect(windowOpenViolations(join(FIXTURES, 'open', root)), root).toEqual(["B15 openDocument.ts: window.open without 'noopener'"])
    }
  })

  it('the registry fixture fails on key, order, value sets and locales — not on naming an undecided feature', () => {
    const violations = registryViolations(REGISTRY_FIXTURE)
    expect(violations).toEqual(
      expect.arrayContaining([
        "B4/B5 features/two: key 'one' differs from the folder name",
        "B4/B5 features/two: key 'one' is already registered by features/one",
        'B4/B5 features/two: order 10 is already used by features/one',
        "B4/B5 features/three: ownerships has unknown value 'legacy'",
        "B4/B5 features/three: docTypes 'WP' is not a type of cardKinds [test]",
        'B12 features/one: ja keys differ from ko',
      ]),
    )
    expect(codes(violations)).toEqual(['B12', 'B4/B5'])
  })

  it('the registry fixture: a call or a template handler named after an undecided feature is a B14 violation; naming it is not', () => {
    expect(undecidedViolations(join(FIXTURES, 'registry')).sort()).toEqual([
      "B14 features/one/useOne.ts: calls 'memoSave('",
      "B14 features/tscRunner/TscRunnerTab.vue: handler 'startTsc'",
      "B14 features/tscRunner/useTscRunner.ts: calls 'tscRun('",
    ])
  })
})
