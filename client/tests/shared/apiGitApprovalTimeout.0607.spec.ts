/**
 * flowgate.default.0607 T0004 §3.5 / §5 Case G·H — only a final approval that carries a
 * `git_action` gets the Git-finalize ceiling; a plain approve on the same path keeps the
 * 30s default.
 *
 * NR0003 §6: `POST /documents/review_transitions/approve` matched none of the long-running
 * path rules, so an approval whose request ran fetch + merge + push was abandoned by the
 * browser at 30s while the server kept going. Asserted through the real axios instance
 * (stub adapter captures the config the interceptor produced), the same way
 * apiTreeTimeout.spec.ts does, so the interceptor itself is what is under test.
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest'
import type { AxiosRequestConfig, InternalAxiosRequestConfig } from 'axios'

import api, { GIT_APPROVAL_TIMEOUT_MS } from '@shared/api'

const DEFAULT_TIMEOUT_MS = 30_000
const APPROVE_URL = '/api/v1/documents/review_transitions/approve'

const originalAdapter = api.defaults.adapter
let lastConfig: InternalAxiosRequestConfig | null = null

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

async function appliedTimeout(url: string, body: unknown, options?: AxiosRequestConfig): Promise<number> {
  await api.post(url, body, options)
  expect(lastConfig, `no request reached the adapter for ${url}`).not.toBeNull()
  return lastConfig!.timeout as number
}

describe('final approval timeout (0607 T0004 §3.5)', () => {
  it('covers the whole server-side Git budget: fetch 120 + ff 30 + merge 30 + push 120', () => {
    expect(GIT_APPROVAL_TIMEOUT_MS).toBeGreaterThan((120 + 30 + 30 + 120) * 1000)
  })

  it('Case G — an approve carrying git_action gets the Git-finalize ceiling', async () => {
    for (const action of ['merge', 'merge_only', 'push', 'commit_push', 'stash']) {
      expect(
        await appliedTimeout(APPROVE_URL, { doc_id: 'p.m.0001.0002-AC', comment: null, git_action: action }),
        action,
      ).toBe(GIT_APPROVAL_TIMEOUT_MS)
    }
  })

  it('Case H — a plain approve keeps the 30s default', async () => {
    expect(await appliedTimeout(APPROVE_URL, { doc_id: 'p.m.0001.0002-D', comment: null })).toBe(
      DEFAULT_TIMEOUT_MS,
    )
    expect(
      await appliedTimeout(APPROVE_URL, { doc_id: 'p.m.0001.0002-AC', comment: null, git_action: null }),
    ).toBe(DEFAULT_TIMEOUT_MS)
    expect(
      await appliedTimeout(APPROVE_URL, { doc_id: 'p.m.0001.0002-AC', comment: null, git_action: '' }),
    ).toBe(DEFAULT_TIMEOUT_MS)
  })

  it('does not leak onto the sibling review transitions', async () => {
    for (const path of [
      '/api/v1/documents/review_transitions/reject',
      '/api/v1/documents/review_transitions/mark_revised',
      '/api/v1/documents/review_transitions/approve_all',
    ]) {
      expect(await appliedTimeout(path, { doc_id: 'x', git_action: 'merge' }), path).toBe(DEFAULT_TIMEOUT_MS)
    }
  })

  it('lets an explicit per-call timeout win', async () => {
    expect(
      await appliedTimeout(APPROVE_URL, { doc_id: 'x', git_action: 'merge' }, { timeout: 5_000 }),
    ).toBe(5_000)
  })
})
