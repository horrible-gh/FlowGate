import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// T0011 §5/§6/§9/§11/§15 "구조": source-level guards for the v0.2 boundaries that a DOM
// test cannot see.
const SRC = resolve(__dirname, '../../src')

function filesUnder(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    return statSync(full).isDirectory() ? filesUnder(full) : [full]
  })
}

const rel = (file: string) => relative(SRC, file).replace(/\\/g, '/')
const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

const ALL = filesUnder(SRC).filter((f) => /\.(ts|vue|css)$/.test(f))
const DASHBOARD = ALL.filter((f) => rel(f).startsWith('app/dashboard/'))

describe('v0.2 Dashboard / App Shell structure (T0011)', () => {
  it('the Dashboard lives in app/dashboard and replaces the T#1 placeholder view', () => {
    expect(DASHBOARD.map(rel)).toContain('app/dashboard/DashboardView.vue')
    expect(existsSync(join(SRC, 'app/views/DashboardPlaceholderView.vue'))).toBe(false)
  })

  it('app/dashboard pulls in no domain feature, API transport or store (T0011 §9)', () => {
    for (const file of DASHBOARD) {
      for (const spec of importsOf(read(file))) {
        expect(spec, rel(file)).not.toMatch(/^@shared\/(api|projects|auth)|^axios$|^pinia$|^@\/features\//)
      }
    }
  })

  it('no Dashboard file grows into a single do-everything component', () => {
    for (const file of DASHBOARD.filter((f) => f.endsWith('.vue'))) {
      expect(read(file).split('\n').length, rel(file)).toBeLessThan(320)
    }
  })

  it('the App Shell only assembles layout: Topbar, Sidebar and RouterView (T0011 §5)', () => {
    const imports = importsOf(read(join(SRC, 'app/App.vue')))
    expect(imports.sort()).toEqual(['./components/Sidebar.vue', './components/Topbar.vue', 'vue-router'])
  })

  it('never embeds or imports v0.1 screens (T0011 §8/§10/§16)', () => {
    for (const file of ALL) {
      const src = read(file)
      expect(src, rel(file)).not.toMatch(/<iframe/i)
      for (const spec of importsOf(src)) {
        expect(spec, rel(file)).not.toMatch(/client\/src\/|MainPanel|\/main\/components\//)
      }
    }
  })

  it('does not load v0.1 global CSS; v0.2 styles stay in its own theme + scoped blocks (T0011 §11)', () => {
    for (const file of ALL) {
      expect(read(file), rel(file)).not.toMatch(/@shared\/(app|variables)\.css/)
    }
    expect(importsOf(read(join(SRC, 'app/main.ts')))).toContain('./styles/theme.css')
    for (const file of ALL.filter((f) => f.endsWith('.vue'))) {
      const styleTags = read(file).match(/<style[^>]*>/g) ?? []
      for (const tag of styleTags) expect(tag, rel(file)).toContain('scoped')
    }
  })

  it('components/ (domain-free shared UI) holds only its README and the T0004 ui/ primitives', () => {
    const files = filesUnder(join(SRC, 'components')).map(rel)
    expect(files).toContain('components/README.md')
    for (const file of files) {
      expect(file === 'components/README.md' || file.startsWith('components/ui/')).toBe(true)
    }
  })

  it('components/ui primitives own no feature/API/store/router meaning (T0004 §11)', () => {
    const UI = filesUnder(join(SRC, 'components/ui')).filter((f) => /\.(ts|vue)$/.test(f))
    expect(UI.length).toBeGreaterThan(0)
    for (const file of UI) {
      for (const spec of importsOf(read(file))) {
        expect(spec, rel(file)).not.toMatch(/^@shared\/(api|projects|auth)|^axios$|^pinia$|^@\/features\/|^vue-router$/)
      }
    }
  })
})
