/**
 * flowgate.v02.0047 rev3 — the AI title request of a new chat waits for one model answer,
 * which the server allows up to 90s (server/flowgate/conversation/auto_title.py
 * TIMEOUT_SEC). Under the 30s default the browser gave up first on a CLI model, the error was
 * swallowed and the chat kept its provisional title on screen. Asserted through the real
 * axios instance (a stub adapter captures the config the interceptor produced), as
 * client/tests/shared/apiGitApprovalTimeout.0607.spec.ts does.
 */
import { afterAll, beforeEach, describe, expect, it } from 'vitest'
import type { InternalAxiosRequestConfig } from 'axios'
import api from '@shared/api'
import { autoTitleChat } from '@/features/conversation/api'

const DEFAULT_TIMEOUT_MS = 30_000
const SERVER_MODEL_TIMEOUT_MS = 90_000

const originalAdapter = api.defaults.adapter
let lastConfig: InternalAxiosRequestConfig | null = null

api.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
  lastConfig = config
  return { data: { doc_id: 'p.m.0001.0010-CH', title: 'AI 제목' }, status: 200, statusText: 'OK', headers: {}, config }
}

afterAll(() => {
  api.defaults.adapter = originalAdapter
})

beforeEach(() => {
  lastConfig = null
})

describe('the AI title request timeout (0047 rev3)', () => {
  it('outlasts the server model call', async () => {
    const saved = await autoTitleChat('p.m.0001.0010-CH', 'opus')
    expect(saved.title).toBe('AI 제목')
    expect(lastConfig?.url).toBe('/api/v1/conversations/p.m.0001.0010-CH/auto-title')
    expect(lastConfig?.timeout as number).toBeGreaterThan(SERVER_MODEL_TIMEOUT_MS)
  })

  it('does not leak onto the chat creation next to it', async () => {
    await api.post('/api/v1/conversations', { project_id: 'p', group_id: 'p.m.0001', title: 'x' })
    expect(lastConfig?.timeout).toBe(DEFAULT_TIMEOUT_MS)
  })
})
