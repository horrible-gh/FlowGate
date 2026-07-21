/**
 * 0283 TS0006 — tree GETs get the 130s ceiling, not the 30s default (TR0005 §A).
 *
 * Root cause of bug 0283.0001-B: the base file-tree and group-tree GETs do a synchronous
 * recursive directory walk of the storage root, but fell under the 30s `DEFAULT_TIMEOUT_MS`,
 * so axios aborted healthy-but-slow walks on remote/UNC storage with ECONNABORTED.
 *
 * `LONG_RUNNING_PATHS` is module-private (deliberately — the policy lives in one place
 * rather than at every call site), so instead of importing `@shared/api` this reads the
 * committed source, rebuilds the real RegExp literals, and applies them exactly as the
 * request interceptor does: `LONG_RUNNING_PATHS.some((re) => re.test(path))`.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

// vitest's root is the client dir (vitest.config.ts lives there).
const source = readFileSync(resolve(process.cwd(), 'shared/api.ts'), 'utf-8')

function longRunningPaths(): RegExp[] {
  const block = source.match(/const LONG_RUNNING_PATHS = \[([\s\S]*?)\n\]/)
  expect(block, 'LONG_RUNNING_PATHS array literal not found in shared/api.ts').toBeTruthy()
  const patterns: RegExp[] = []
  for (const raw of block![1].split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('//')) continue
    const literal = line.match(/^\/(.*)\/([a-z]*),?$/)
    expect(literal, `unparsable LONG_RUNNING_PATHS entry: ${line}`).toBeTruthy()
    patterns.push(new RegExp(literal![1], literal![2]))
  }
  expect(patterns.length).toBeGreaterThan(0)
  return patterns
}

// The interceptor's decision, reproduced verbatim.
const isLongRunning = (path: string) => longRunningPaths().some((re) => re.test(path))

// The exact URLs the explorer store builds (src/main/stores/explorer.ts).
const FILE_TREE_URL = '/api/v1/projects/flowgate/files/tree?branch=main'
const GROUP_TREE_URL = '/api/v1/projects/flowgate/groups/tree?branch=main'
const GROUP_BRANCH_TREE_URL = '/api/v1/projects/flowgate/git/groups/flowgate.default.0283/tree'

describe('shared/api timeout policy', () => {
  it('parses the committed LONG_RUNNING_PATHS into real regexes', () => {
    expect(longRunningPaths().every((re) => re instanceof RegExp)).toBe(true)
  })

  it('gives the base file-tree GET the long ceiling', () => {
    expect(isLongRunning(FILE_TREE_URL)).toBe(true)
  })

  it('gives the group-tree GET the long ceiling', () => {
    expect(isLongRunning(GROUP_TREE_URL)).toBe(true)
  })

  it('keeps the group-branch tree GET on the long ceiling via the existing /git/ rule', () => {
    expect(isLongRunning(GROUP_BRANCH_TREE_URL)).toBe(true)
  })

  it('does not widen the policy to ordinary reads', () => {
    for (const path of [
      '/api/v1/projects',
      '/api/v1/projects/flowgate/documents',
      '/api/v1/projects/flowgate/groups/flowgate.default.0283/documents',
      '/api/v1/auth/refresh',
      '/api/v1/dashboard/events',
    ]) {
      expect(isLongRunning(path), `${path} should keep the 30s default`).toBe(false)
    }
  })

  it('keeps the two ceilings and the interceptor gate intact', () => {
    expect(source).toContain('const DEFAULT_TIMEOUT_MS = 30_000')
    expect(source).toContain('const LONG_TIMEOUT_MS = 130_000')
    // Only requests still on the default are raised — an explicit per-call timeout wins.
    expect(source).toContain('if (config.timeout === DEFAULT_TIMEOUT_MS) {')
    expect(source).toContain('config.timeout = LONG_TIMEOUT_MS')
  })
})
