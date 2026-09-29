import { execFileSync } from 'node:child_process'
import { statSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { beforeAll, describe, expect, it } from 'vitest'

// T0009 TR0010 rev2: the /app production bundle crashed at module init with
// "Class extends value undefined is not a constructor or null". vite.config.ts aliased
// axios onto `require.resolve('axios')` = axios/dist/node/axios.cjs (the Node build), which
// pulls https-proxy-agent -> agent-base, whose `class Agent extends require('events')
// .EventEmitter` reads from Vite's empty browser stub for the Node builtin. `npm run build`
// still succeeds, so this runs the real production build and inspects which modules it
// bundled.
const ROOT = resolve(__dirname, '../..')

// vite/esbuild refuse to load under the jsdom test environment (esbuild's TextEncoder
// invariant), so config resolution and the build run in a plain Node child process.
// write:false — nothing lands in dist/.
const BUILD_SCRIPT = `
const { build, resolveConfig } = await import('vite')
const config = await resolveConfig({ root: process.cwd(), logLevel: 'silent' }, 'build', 'production')
const alias = Object.fromEntries(
  config.resolve.alias.filter((a) => typeof a.find === 'string').map((a) => [a.find, a.replacement]),
)
const out = await build({ root: process.cwd(), logLevel: 'silent', build: { write: false } })
const ids = out.output.filter((o) => o.type === 'chunk').flatMap((c) => Object.keys(c.modules))
process.stdout.write(JSON.stringify({ alias, ids }))
`

let alias: Record<string, string>
let moduleIds: string[]

beforeAll(() => {
  const stdout = execFileSync(process.execPath, ['--input-type=module', '-e', BUILD_SCRIPT], {
    cwd: ROOT,
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  })
  const parsed = JSON.parse(stdout) as { alias: Record<string, string>; ids: string[] }
  alias = parsed.alias
  moduleIds = parsed.ids.map((id) => id.replace(/\\/g, '/'))
}, 120_000)

describe('client_gui production bundle', () => {
  it('aliases vue/vue-i18n/axios onto package directories, not their Node/CJS entry files', () => {
    for (const name of ['vue', 'vue-i18n', 'axios']) {
      expect(statSync(alias[name]).isDirectory(), name).toBe(true)
      expect(statSync(join(alias[name], 'package.json')).isFile(), name).toBe(true)
    }
  })

  it('ships browser builds only: no Node-only modules or builtin stubs in the /app bundle', () => {
    expect(moduleIds.some((id) => id.endsWith('/client_gui/src/app/main.ts'))).toBe(true)
    // The exact crash chain from the rejected build, plus the Node/CJS entries that led to it.
    const forbidden = [
      '/node_modules/axios/dist/node/',
      '/node_modules/https-proxy-agent/',
      '/node_modules/agent-base/',
      '/node_modules/follow-redirects/',
      '/node_modules/form-data/',
      '__vite-browser-external',
      '/node_modules/vue/index.js',
      '/node_modules/vue/dist/vue.cjs',
      '/node_modules/vue-i18n/index.js',
      '/node_modules/@vue/compiler-',
    ]
    expect(moduleIds.filter((id) => forbidden.some((f) => id.includes(f)))).toEqual([])
    // axios must come from its browser (XHR) adapter.
    expect(moduleIds.some((id) => id.endsWith('/node_modules/axios/lib/adapters/xhr.js'))).toBe(true)
  })
})
