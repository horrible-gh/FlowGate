import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { createRequire } from 'node:module'
import type { Plugin } from 'vite'
import { PRODUCT_NAME } from '../src/shared/productName.ts'

const text = (path: string) => readFileSync(path, 'utf8')
// Start at the actual module path, including nested/scoped installations. Never
// infer runtime packages from all entries in package-lock.json.
export function packageRoot(id: string): string | undefined {
  const clean = id.replace(/^\0/, '').split('?')[0].replace(/\\/g, '/')
  if (!clean.includes('/node_modules/')) return
  let dir = dirname(clean)
  while (dirname(dir) !== dir) {
    if (existsSync(join(dir, 'package.json'))) return dir
    dir = dirname(dir)
  }
}
export function licenseFiles(root: string): string[] {
  const result: string[] = []
  function visit(dir: string, depth: number) {
    for (const name of readdirSync(dir).sort()) {
      if (!/^(licen[sc]e|copying|copyright|notice)(?:[._-].*|s)?$/i.test(name)) continue
      const path = join(dir, name)
      if (statSync(path).isFile()) result.push(path)
      else if (depth < 2) {
        // License directories may contain files named after bundled components.
        for (const child of readdirSync(path).sort()) {
          const file = join(path, child)
          if (statSync(file).isFile()) result.push(file)
        }
      }
    }
  }
  visit(root, 0)
  if (!result.some(path => /licen[sc]e|copying/i.test(path.slice(root.length)))) {
    throw new Error(`License text unresolved: ${root}`)
  }
  return result
}
function dependencyRoot(name: string, from: string): string {
  const req = createRequire(join(from, 'package.json'))
  try { return dirname(req.resolve(`${name}/package.json`)) } catch {
    const root = packageRoot(req.resolve(name))
    if (!root) throw new Error(`Embedded dependency unresolved: ${name}`)
    return root
  }
}
export function collectNotices(moduleIds: string[], repoRoot: string): string {
  const packages = new Map<string, string>()
  function add(root: string, reason: string, embedded = false) {
    if (packages.has(root)) {
      // A closure member can also contribute its own Rollup module. Preserve that
      // direct inclusion when reporting the actual emitted root-package count.
      if (reason === 'emitted Rollup chunk') packages.set(root, reason)
      return
    }
    packages.set(root, reason)
    const meta = JSON.parse(text(join(root, 'package.json')))
    // Vue Flow publishes prebundled dependencies: Rollup cannot expose their
    // module IDs. Conservatively retain their dependency closure's full notices.
    if (embedded || meta.name === '@vue-flow/core') {
      for (const name of Object.keys(meta.dependencies ?? {}).sort()) {
        add(dependencyRoot(name, root), `dependency closure of ${meta.name}@${meta.version} (prebundled)`, true)
      }
    }
  }
  for (const id of moduleIds) { const root = packageRoot(id); if (root) add(root, 'emitted Rollup chunk') }
  const sections = Array.from(packages).map(([root, reason]) => {
    const meta = JSON.parse(text(join(root, 'package.json')))
    return `${meta.name}@${meta.version}\nInclusion: ${reason}\n\n` +
      licenseFiles(root).map(file => `--- ${file.slice(root.length + 1)} ---\n${text(file)}`).join('\n\n')
  }).sort()
  const ledger = join(repoRoot, 'THIRD_PARTY_NOTICES.md')
  if (!existsSync(ledger)) throw new Error('Required project notice ledger missing: ' + ledger)
  {
    sections.push(`Inline icons and project third-party ledger\n\n${text(ledger)}`)
    // Include the referenced texts, not just the license names in the ledger.
    const licenses = join(repoRoot, 'third_party', 'licenses')
    const walk = (dir: string): void => {
      if (!existsSync(dir)) throw new Error('Required project license directory missing: ' + dir)
      if (readdirSync(dir).length === 0) throw new Error('Empty project license directory: ' + dir)
      for (const name of readdirSync(dir).sort()) {
        const path = join(dir, name)
        if (statSync(path).isDirectory()) walk(path)
        else sections.push(`Project notice: ${path.slice(repoRoot.length + 1).replace(/\\/g, '/')}\n\n${text(path)}`)
      }
    }
    walk(licenses)
  }
  return `${PRODUCT_NAME} — third-party notices\nGenerated from emitted Rollup modules; Vue Flow dependency closure is included conservatively.\n\n${sections.join('\n\n========================================\n\n')}\n`
}
export default function thirdPartyNotices(repoRoot: string): Plugin {
  return {
    name: 'flowgate-third-party-notices',
    apply: 'build',
    generateBundle(_options, bundle) {
      const ids = Object.values(bundle).flatMap(item => item.type === 'chunk' ? Object.keys(item.modules).filter(id => item.modules[id].renderedLength > 0) : [])
      this.emitFile({ type: 'asset', fileName: 'THIRD_PARTY_NOTICES.txt', source: collectNotices(ids, repoRoot) })
      this.emitFile({ type: 'asset', fileName: 'LICENSE.txt', source: text(resolve(repoRoot, 'LICENSE')) })
    },
  }
}
