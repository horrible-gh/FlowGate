/**
 * 0506 T0004 §11-16 — client fallback mention sentinel regression.
 *
 * TR0005 rev0 was rejected for missing sentinel coverage on the client fallback mention
 * surface. `composeMention()` falls back to the local `buildMentText()` builder whenever
 * `token.mention` is null (server did not render one) — that fallback used to push a whole
 * "## Scratch Directory" section with the real `token.scratch_dir` value. This asserts the
 * fallback text never contains the scratch path, whatever it is.
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateToken, type IssuedToken } from '@main/composables/useFlowGateToken'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const SENTINEL = 'C:\\FLOWGATE_SECRET_SCRATCH\\TOKEN_123'

/** useFlowGateToken calls useI18n(), so it has to run inside a component instance. */
function withComposable<T>(fn: (api: ReturnType<typeof useFlowGateToken>) => T): T {
  let out!: T
  mount(
    defineComponent({
      setup() {
        out = fn(useFlowGateToken())
        return () => null
      },
    }),
    { global: { plugins: [i18n] } },
  )
  return out
}

describe('useFlowGateToken client fallback mention — scratch sentinel', () => {
  it('composeMention() falls back to buildMentText() and never leaks token.scratch_dir', () => {
    const token: IssuedToken = {
      raw_token: 'raw',
      token_id: 'tk_1',
      expires_at: 'x',
      scratch_dir: SENTINEL,
      action_scope: 'edit',
      doc_ref: 'flowgate.default.0506.0001-R',
      mention: null,
    }

    const text = withComposable((api) => api.composeMention(token))

    expect(text).not.toContain(SENTINEL)
    expect(text).not.toContain('## Scratch Directory')
  })
})
