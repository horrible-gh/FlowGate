import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'
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

const SRC = resolve(__dirname, '../../../src')
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
      allowed: [/^vue$/, OWN, /^\.\.\/\.\.\/(inspectorTarget|inspectorHost|openDocument)$/, /^@\/core\/auth\/currentUser$/],
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

/** B14 — undecided features (D#1 §8.4) must not be registered or worded. */
const UNDECIDED_MARKER = /expand|delegat|memo|ai_delivery|tsc/i

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
  }
  return out
}

/** Every file-level violation of an Inspector root. */
function boundaryViolations(root: string): string[] {
  return [...frameViolations(root), ...featureFolders(root).flatMap((key) => featureViolations(root, key))]
}

const keyPaths = (value: unknown, prefix = ''): string[] =>
  value && typeof value === 'object'
    ? Object.entries(value as Record<string, unknown>).flatMap(([key, child]) => [`${prefix}${key}`, ...keyPaths(child, `${prefix}${key}.`)])
    : []

/** B4 / B5 (registry contract), B12 (same keys in ko / en / ja), B14 (undecided markers). */
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
    if (UNDECIDED_MARKER.test(String(entry.key))) out.push(`B14 features/${folder}: key '${String(entry.key)}' names an undecided feature`)
    for (const path of new Set(['ko', 'en', 'ja'].flatMap((locale) => keyPaths(messages[locale])))) {
      if (UNDECIDED_MARKER.test(path.split('.').pop() ?? '')) out.push(`B14 features/${folder}: message key '${path}' names an undecided feature`)
    }
  }
  return out
}

const codes = (violations: string[]) => [...new Set(violations.map((line) => line.split(' ')[0]))].sort()

// --- Real source ----------------------------------------------------------------------------------

const FEATURES = featureFolders(INSPECTOR)
const REAL_MODULES = import.meta.glob('../../../src/features/designer/inspector/features/*/register.ts', { eager: true })

describe('features/designer/inspector structure (flowgate.v02.0048)', () => {
  it('B1 / B2 / B7: the root holds only the D#1 §2.5 entries; the Frame and parts stay free of features, API and store', () => {
    expect(frameViolations(INSPECTOR)).toEqual([])
  })

  it.each(FEATURES.length ? FEATURES : ['(no feature folder yet)'])('B3 / B6 / B7 / B8: feature folder %s', (key) => {
    if (!FEATURES.length) {
      expect(isDir(join(INSPECTOR, 'features')) ? featureFolders(INSPECTOR) : []).toEqual([])
      return
    }
    expect(featureViolations(INSPECTOR, key)).toEqual([])
  })

  it('B4 / B5 / B12 / B14: the registrations keep the contract, the three locales and no undecided feature', () => {
    expect(registryViolations(REAL_MODULES)).toEqual([])
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

  it('the registry fixture fails on key, order, value sets, locales and undecided markers', () => {
    const violations = registryViolations(REGISTRY_FIXTURE)
    expect(violations).toEqual(
      expect.arrayContaining([
        "B4/B5 features/two: key 'one' differs from the folder name",
        "B4/B5 features/two: key 'one' is already registered by features/one",
        'B4/B5 features/two: order 10 is already used by features/one',
        "B4/B5 features/three: ownerships has unknown value 'legacy'",
        "B4/B5 features/three: docTypes 'WP' is not a type of cardKinds [test]",
        'B12 features/one: ja keys differ from ko',
        "B14 features/one: message key 'memo' names an undecided feature",
        "B14 features/tscRunner: key 'tscRunner' names an undecided feature",
      ]),
    )
    expect(codes(violations)).toEqual(['B12', 'B14', 'B4/B5'])
  })
})
