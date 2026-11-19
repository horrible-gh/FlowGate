import assert from 'node:assert/strict'
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { loadMap, selectAreas, matches } from './test-area.mjs'

function fixture(t, area, files) {
  const root = mkdtempSync(join(tmpdir(), 'fg-area-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  mkdirSync(join(root, 'tests'), { recursive: true })
  writeFileSync(join(root, 'tests/areas.json'), JSON.stringify({ area }))
  for (const file of files) {
    mkdirSync(join(root, file, '..'), { recursive: true })
    writeFileSync(join(root, file), '')
  }
  return root
}
const config = pattern => ({ source: [], tests: [pattern] })

test('parent and union selection keep exact paths and deduplicate', t => {
  const root = fixture(t, {
    'designer.run': config('tests/run*.spec.ts'),
    'designer.canvas': config('tests/canvas.spec.ts'),
    git: config('tests/git.spec.ts'),
  }, ['tests/run.spec.ts', 'tests/runner.spec.ts', 'tests/canvas.spec.ts', 'tests/git.spec.ts'])
  const map = loadMap(root)
  assert.deepEqual(selectAreas(map, ['designer,git', 'designer.run']), map.files)
  assert.deepEqual(selectAreas(map, ['designer.run']), ['tests/run.spec.ts', 'tests/runner.spec.ts'])
  for (const args of [[], ['unknown'], ['designer,'], ['--watch']]) {
    assert.throws(() => selectAreas(map, args))
  }
})

test('unmapped files cannot silently escape verification', t => {
  const root = fixture(t, { git: config('tests/git.spec.ts') }, ['tests/git.spec.ts', 'tests/new.spec.ts'])
  assert.throws(() => loadMap(root), /unmapped/)
})

test('cross-area overlaps fail but overlapping patterns in one area do not', t => {
  const root = fixture(t, { git: config('tests/*.spec.ts'), shell: config('tests/a.spec.ts') }, ['tests/a.spec.ts'])
  assert.throws(() => loadMap(root), /Expected one area/)
  writeFileSync(join(root, 'tests/areas.json'), JSON.stringify({ area: {
    git: { source: [], tests: ['tests/*.spec.ts', 'tests/a.spec.ts'] },
  } }))
  assert.equal(loadMap(root).files.length, 1)
})

test('stale patterns fail even if another pattern covers all files', t => {
  const root = fixture(t, { git: config('tests/*.spec.ts'), shell: config('tests/deleted.spec.ts') }, ['tests/a.spec.ts'])
  assert.throws(() => loadMap(root), /Empty test pattern/)
})

test('patterns span directories, escape literals and reject unsupported syntax', () => {
  assert.equal(matches('tests/a/deep/file.spec.ts', 'tests/a/*.spec.ts'), true)
  assert.equal(matches('tests/aXspec.ts', 'tests/a.spec.ts'), false)
  assert.equal(matches('tests/a.spec.ts', 'tests/?.spec.ts'), true)
  for (const pattern of ['../tests/*', '/tests/*', 'tests/[ab]*']) {
    assert.throws(() => matches('tests/a.spec.ts', pattern), /Invalid area pattern/)
  }
})

test('repository map covers every current spec and designer parent', () => {
  const map = loadMap()
  assert.equal(Object.keys(map.area).length, 11)
  assert.equal(map.files.length, map.owners.size)
  assert.deepEqual(selectAreas(map, ['designer']), map.files.filter(path => path.startsWith('tests/features/designer/')))
})
