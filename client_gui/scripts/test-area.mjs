import { readdirSync, readFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { spawnSync } from 'node:child_process'

export const clientRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

// Same path semantics as the server map: * spans directories; ? is one character.
export function matches(path, pattern) {
  if (typeof pattern !== 'string' || !pattern || /[\[\]{}\\]/.test(pattern)
      || pattern.startsWith('/') || pattern.split('/').includes('..')) {
    throw new Error(`Invalid area pattern: ${pattern}`)
  }
  const regex = pattern.split('').map(char => char === '*' ? '.*' : char === '?' ? '.'
    : char.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('')
  return new RegExp(`^${regex}$`).test(path)
}

function specs(root, directory = 'tests') {
  return readdirSync(join(root, directory), { withFileTypes: true }).flatMap(entry => {
    const path = `${directory}/${entry.name}`
    return entry.isDirectory() ? specs(root, path)
      : entry.isFile() && path.endsWith('.spec.ts') ? [path] : []
  }).sort()
}

export function loadMap(root = clientRoot) {
  const { area } = JSON.parse(readFileSync(join(root, 'tests/areas.json'), 'utf8'))
  if (!area || typeof area !== 'object' || Array.isArray(area) || !Object.keys(area).length) {
    throw new Error('Area map must contain a nonempty area object')
  }
  const files = specs(root)
  const owners = new Map(files.map(path => [path, []]))
  for (const [name, config] of Object.entries(area)) {
    if (!Array.isArray(config.tests) || !config.tests.length || !Array.isArray(config.source)) {
      throw new Error(`Invalid area: ${name}`)
    }
    for (const pattern of config.tests) {
      const matched = files.filter(path => matches(path, pattern))
      if (!matched.length) throw new Error(`Empty test pattern: ${name}: ${pattern}`)
      for (const path of matched) {
        if (!owners.get(path).includes(name)) owners.get(path).push(name)
      }
    }
  }
  for (const [path, names] of owners) {
    if (names.length !== 1) throw new Error(`Expected one area: ${path}: ${names.join(', ') || 'unmapped'}`)
  }
  return { area, files, owners }
}

export function selectAreas(mapping, requested) {
  const names = requested.flatMap(value => value.split(',')).map(value => value.trim())
  if (!names.length || names.some(name => !name || name.startsWith('-'))) {
    throw new Error('Usage: npm run test:area -- <area[,area]> [area]')
  }
  const selected = new Set()
  for (const name of names) {
    const expanded = Object.keys(mapping.area).filter(key => key === name || key.startsWith(`${name}.`))
    if (!expanded.length) throw new Error(`Unknown area: ${name}`)
    expanded.forEach(key => selected.add(key))
  }
  return mapping.files.filter(path => selected.has(mapping.owners.get(path)[0]))
}

export function main(args = process.argv.slice(2)) {
  try {
    const mapping = loadMap()
    if (args.length === 1 && args[0] === '--check') {
      console.log(`area map: ${mapping.files.length} files / ${Object.keys(mapping.area).length} areas`)
      return 0
    }
    const files = selectAreas(mapping, args)
    console.log(`area: ${args.join(',')} | selected ${files.length} files / excluded ${mapping.files.length - files.length} files`)
    const child = spawnSync(process.execPath, [join(clientRoot, 'node_modules/vitest/vitest.mjs'),
      'run', '--config', 'scripts/test-area.config.ts'], {
      cwd: clientRoot, stdio: 'inherit',
      env: { ...process.env, FG_TEST_AREA_FILES: JSON.stringify(files) },
    })
    if (child.error) throw child.error
    return child.status ?? 1
  } catch (error) {
    console.error(error.message)
    return 1
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  process.exitCode = main()
}
