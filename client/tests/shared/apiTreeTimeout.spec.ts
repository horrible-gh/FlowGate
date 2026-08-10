/**
 * 0283 TS0006 — tree GETs get the 130s ceiling, not the 30s default (TR0005 §A).
 *
 * Root cause of bug 0283.0001-B: the base file-tree and group-tree GETs do a synchronous
 * recursive directory walk of the storage root, but fell under the 30s `DEFAULT_TIMEOUT_MS`,
 * so axios aborted healthy-but-slow walks on remote/UNC storage with ECONNABORTED.
 *
 * 0394 T0016 (NR0003 §6.2-라): this suite used to read the committed `shared/api.ts` as
 * text, pull the `LONG_RUNNING_PATHS` array literal out with a regex, rebuild each entry
 * into a real RegExp and re-implement the interceptor's decision
 * (`LONG_RUNNING_PATHS.some((re) => re.test(path))`) inside the test. Two things were
 * therefore never checked: that the interceptor still consults that list, and that it
 * still writes the ceiling onto the request. Reformatting the array — one entry per two
 * lines, a trailing comment, `new RegExp(...)` instead of a literal — broke the parser and
 * turned the suite red without touching the policy; deleting the interceptor left it green.
 *
 * The list is still module-private on purpose (the policy lives in one place rather than at
 * every call site), so instead of reaching for it, the assertions now go through the real
 * axios instance: a stub adapter captures the config the interceptor produced, and the
 * timeout actually applied to the request is what gets asserted.
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest'
import type { AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios'

import api from '@shared/api'

const DEFAULT_TIMEOUT_MS = 30_000
const LONG_TIMEOUT_MS = 130_000

// The exact URLs the explorer store builds (src/main/stores/explorer.ts).
const FILE_TREE_URL = '/api/v1/projects/flowgate/files/tree?branch=main'
const GROUP_TREE_URL = '/api/v1/projects/flowgate/groups/tree?branch=main'
const GROUP_BRANCH_TREE_URL = '/api/v1/projects/flowgate/git/groups/flowgate.default.0283/tree'

const originalAdapter = api.defaults.adapter
let lastConfig: InternalAxiosRequestConfig | null = null

// Stops at the adapter: nothing leaves the process, and the config handed to it is exactly
// what the request interceptor produced.
api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
  lastConfig = config
  return { data: {}, status: 200, statusText: 'OK', headers: {}, config }
}

afterAll(() => {
  api.defaults.adapter = originalAdapter
})

beforeEach(() => {
  lastConfig = null
})

/** The timeout the request actually went out with. */
async function appliedTimeout(url: string, options?: AxiosRequestConfig): Promise<number> {
  await api.get(url, options)
  expect(lastConfig, `no request reached the adapter for ${url}`).not.toBeNull()
  return lastConfig!.timeout as number
}

describe('shared/api timeout policy', () => {
  it('starts every request on the 30s default', () => {
    expect(api.defaults.timeout).toBe(DEFAULT_TIMEOUT_MS)
  })

  it('gives the base file-tree GET the long ceiling', async () => {
    expect(await appliedTimeout(FILE_TREE_URL)).toBe(LONG_TIMEOUT_MS)
  })

  it('gives the group-tree GET the long ceiling', async () => {
    expect(await appliedTimeout(GROUP_TREE_URL)).toBe(LONG_TIMEOUT_MS)
  })

  it('keeps the group-branch tree GET on the long ceiling via the existing /git/ rule', async () => {
    expect(await appliedTimeout(GROUP_BRANCH_TREE_URL)).toBe(LONG_TIMEOUT_MS)
  })

  it('does not widen the policy to ordinary reads', async () => {
    for (const path of [
      '/api/v1/projects',
      '/api/v1/projects/flowgate/documents',
      '/api/v1/projects/flowgate/groups/flowgate.default.0283/documents',
      '/api/v1/auth/refresh',
      '/api/v1/dashboard/events',
    ]) {
      expect(await appliedTimeout(path), `${path} should keep the 30s default`).toBe(
        DEFAULT_TIMEOUT_MS,
      )
    }
  })

  it('lets an explicit per-call timeout win over the ceiling', async () => {
    // Only requests still on the default are raised: a caller that has already decided
    // must not be overridden, on a tree GET least of all.
    expect(await appliedTimeout(FILE_TREE_URL, { timeout: 5_000 })).toBe(5_000)
  })
})
