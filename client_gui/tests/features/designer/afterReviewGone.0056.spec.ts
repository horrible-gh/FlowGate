/**
 * flowgate.v02.0056 F2-2 — closing check of the removed card setting (0056 T: the run-after-review setting is gone from
 * the UI, the settings model, i18n ko · en · ja, the preview and the specs — not hidden, not disabled): no file of
 * client_gui's src, preview or tests names it. The names are put together here so this file does not name them either.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const ROOT = resolve(__dirname, '../../..')
const FOLDERS = ['src', 'preview', 'tests']

const NAMES = [['after', 'review'].join('_'), ['after', 'Review'].join(''), ['검수', '뒤', '진행'].join(' ')]

function files(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name)
    if (statSync(full).isDirectory()) return name === 'node_modules' ? [] : files(full)
    return /\.(ts|vue|js|json|md|css|html)$/.test(name) ? [full] : []
  })
}

describe('the removed run-after-review card setting (flowgate.v02.0056 F2-2 closing check)', () => {
  it('no source, preview or spec file of client_gui names it', () => {
    const found = FOLDERS.flatMap((folder) => files(join(ROOT, folder)))
      .flatMap((file) => {
        const src = readFileSync(file, 'utf8')
        return NAMES.filter((name) => src.includes(name)).map((name) => `${relative(ROOT, file).replace(/\\/g, '/')}: ${name}`)
      })
    expect(found).toEqual([])
  })
})
