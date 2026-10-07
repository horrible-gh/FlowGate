import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as ConversationFeature from '@/features/conversation'
import { APP_MESSAGES } from '@/app/i18n'

// flowgate.v02.0031 — source-level guards for the Conversation part that a DOM test cannot see.
const ROOT = resolve(__dirname, '../../..')
const SRC = join(ROOT, 'src')
const CONV = join(SRC, 'features/conversation')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? filesUnder(full) : [full]
  })
}

const rel = (file: string) => relative(CONV, file).replace(/\\/g, '/')
const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])
const FILES = filesUnder(CONV)

function keysOf(obj: unknown, prefix = ''): string[] {
  if (!obj || typeof obj !== 'object') return [prefix]
  return Object.entries(obj as Record<string, unknown>).flatMap(([k, v]) => keysOf(v, prefix ? `${prefix}.${k}` : k))
}

describe('features/conversation structure (flowgate.v02.0031)', () => {
  it('holds the Conversation part files', () => {
    expect(FILES.map(rel).sort()).toEqual(
      [
        'ConversationComposer.vue',
        'ConversationHeader.vue',
        'ConversationPanel.vue',
        'ConversationParticipants.vue',
        'ConversationTimeline.vue',
        'RunChangeDiff.vue',
        'TimelineActivity.vue',
        'TimelineMessage.vue',
        'api.ts',
        'changeDiff.ts',
        'conversationSession.ts',
        'conversationSource.ts',
        'drafts.ts',
        'index.ts',
        'timeline.ts',
        'types.ts',
      ].sort(),
    )
  })

  it('exposes only the panel at runtime', () => {
    expect(Object.keys(ConversationFeature)).toEqual(['ConversationPanel'])
  })

  it('reaches the server only through api.ts and the shared transport', () => {
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) {
        if (/^@shared\/api$|^axios$/.test(spec)) expect(rel(file), spec).toBe('api.ts')
        expect(spec, rel(file)).not.toMatch(/^pinia$|^vue-router$|^axios$/)
      }
    }
  })

  it('imports only vue / vue-i18n, its own files, ui primitives, the clipboard helper and (api.ts) the shared transport', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\.\/\.\.\/core\/interaction\/clipboard$/,
      /^@shared\/api$/,
      /^@shared\/utils\/uuid$/,
    ]
    for (const file of FILES) {
      for (const spec of importsOf(read(file))) expect(allowed.some((re) => re.test(spec)), `${rel(file)}: ${spec}`).toBe(true)
    }
  })

  it('keeps the model, rules, source and session free of screens', () => {
    for (const name of ['types.ts', 'timeline.ts', 'api.ts', 'conversationSource.ts', 'conversationSession.ts', 'drafts.ts', 'changeDiff.ts']) {
      for (const spec of importsOf(read(join(CONV, name)))) expect(spec, name).not.toMatch(/\.vue$/)
    }
  })

  it('carries no Tool Panel Container, Palette, Git or Designer Shell code (R0001 §10)', () => {
    for (const file of FILES) {
      const src = read(file)
      expect(src, rel(file)).not.toMatch(/features\/designer|DesignerToolPanel|PalettePanel|GitPanel|role="tab"|Tabs\.vue/)
    }
  })

  // flowgate.v02.0037 — the Designer right Tool Panel assembles the part through the public
  // surface, and only there.
  it('is assembled into the Designer only by the right Tool Panel, through the public surface', () => {
    const users = filesUnder(join(SRC, 'features/designer'))
      .filter((file) => /features\/conversation/.test(read(file)))
      .map((file) => relative(SRC, file).replace(/\\/g, '/'))
    expect(users).toEqual(['features/designer/shell/DesignerRightDock.vue'])
    const dock = read(join(SRC, 'features/designer/shell/DesignerRightDock.vue'))
    expect(importsOf(dock).filter((spec) => /features\/conversation/.test(spec))).toEqual(['@/features/conversation'])
  })

  it('has the same conversation message keys in every locale', () => {
    const ko = keysOf(APP_MESSAGES.ko.conversation).sort()
    expect(ko.length).toBeGreaterThan(50)
    expect(keysOf(APP_MESSAGES.en.conversation).sort()).toEqual(ko)
    expect(keysOf(APP_MESSAGES.ja.conversation).sort()).toEqual(ko)
  })

  it('every gui.conversation key used in the part exists', () => {
    const ko = new Set(keysOf(APP_MESSAGES.ko.conversation).map((k) => `gui.conversation.${k}`))
    for (const file of FILES) {
      for (const m of read(file).matchAll(/['`](gui\.conversation\.[a-z_.]+)['`]/g)) {
        expect(ko.has(m[1]), `${rel(file)}: ${m[1]}`).toBe(true)
      }
    }
  })
})
