import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildConversationMention } from '@main/composables/useFlowGateToken'

// Group 0103 B0001 regression: the chat (CH) copy-mention is built client-side and its
// URLs must always carry a host, because an AI worker on another machine consumes the text.
// In production setup.ps1 writes VITE_API_BASE_URL=/flowgate (relative) — which previously
// left every mention URL host-less ("the chat copy mention shows no host anywhere").

const params = {
  rawToken: 'tok-abc',
  docId: 'flowgate.default.0103.0001-B',
  project: 'flowgate',
  module: 'default',
  groupName: 'flowgate.default.0103',
}

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('buildConversationMention host inclusion (B0001)', () => {
  it('absolutizes a relative VITE_API_BASE_URL against window.location.origin', () => {
    vi.stubEnv('VITE_FLOWGATE_PUBLIC_URL', undefined)
    vi.stubEnv('VITE_API_BASE_URL', '/flowgate')
    const origin = window.location.origin
    const mention = buildConversationMention(params)
    // Every GET/POST line must carry the host, never a bare relative path.
    expect(mention).toContain(`GET ${origin}/flowgate/api/v1/document`)
    expect(mention).toContain(`POST ${origin}/flowgate/api/v1/inbox`)
    expect(mention).not.toMatch(/GET \/flowgate\/api\/v1/)
    expect(mention).not.toMatch(/POST \/flowgate\/api\/v1/)
  })

  it('leaves an already-absolute base URL unchanged (no double host)', () => {
    vi.stubEnv('VITE_FLOWGATE_PUBLIC_URL', undefined)
    vi.stubEnv('VITE_API_BASE_URL', 'http://192.168.0.252:8088/flowgate')
    const mention = buildConversationMention(params)
    expect(mention).toContain('GET http://192.168.0.252:8088/flowgate/api/v1/document')
    expect(mention).toContain('POST http://192.168.0.252:8088/flowgate/api/v1/inbox')
    // origin must not be prepended to an already-absolute base.
    expect(mention).not.toContain(`${window.location.origin}http`)
  })
})
