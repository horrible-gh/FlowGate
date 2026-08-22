/**
 * 0449 T0004 item 4 — a /workflow/advance refusal is never laundered into a /token/issue.
 *
 * NR0003 E3: `issueToken()` caught every non-auth advance error and fell through to
 * `/token/issue` "for legacy compat". That fallback mints a token WITHOUT advancing the head
 * cell, so the server's own refusal — 409 sequence_exhausted, 409 head_in_progress, 500
 * internal_error — was wiped off the screen and replaced by whatever the unrelated issue call
 * did next. It is also why the incident's `token_issued=0` could not distinguish "advance was
 * never called" from "advance was refused and the fallback failed too".
 */
import { defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateToken, parseAdvanceFailure } from '@main/composables/useFlowGateToken'

const { postRequest, showToast } = vi.hoisted(() => ({
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

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

function httpError(status: number, data: Record<string, unknown>) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data },
  })
}

const advanceCalls = () =>
  postRequest.mock.calls.filter((c) => String(c[0]).includes('/workflow/advance'))
const tokenIssueCalls = () =>
  postRequest.mock.calls.filter((c) => String(c[0]).includes('/token/issue'))

beforeEach(() => {
  postRequest.mockReset()
  showToast.mockReset()
})

describe('issueToken — advance refusals stop, and keep their code', () => {
  const cases: Array<{ label: string; status: number; data: Record<string, unknown> }> = [
    { label: 'sequence_exhausted', status: 409, data: { error: 'sequence_exhausted', doc_id: 'p.m.0449.0001-B' } },
    { label: 'head_in_progress', status: 409, data: { error: 'head_in_progress', head_type: 'T', head_label: 'T0004' } },
    { label: 'internal_error', status: 500, data: { error: 'internal_error', detail: 'boom' } },
  ]

  for (const c of cases) {
    it(`${c.label}: /token/issue is not called and the code stays on screen`, async () => {
      postRequest.mockRejectedValueOnce(httpError(c.status, c.data))

      const token = await withComposable((api) =>
        api.issueToken({ project: 'p', module: 'm', group: '0449', doc_ref: 'p.m.0449.0001-B' }),
      )

      expect(token).toBeNull()
      expect(advanceCalls()).toHaveLength(1)
      expect(tokenIssueCalls()).toHaveLength(0)
      expect(showToast).toHaveBeenCalledTimes(1)
      // The user is told the server's own refusal code, not a generic "failed to issue token".
      expect(String(showToast.mock.calls[0][0])).toContain(c.label)
      expect(String(showToast.mock.calls[0][0])).not.toContain(i18n.global.t('main.flow_gate_token.issue_failed'))
    })
  }

  it('a network failure with no response body still stops instead of falling back', async () => {
    postRequest.mockRejectedValueOnce(new Error('Network Error'))

    const token = await withComposable((api) =>
      api.issueToken({ project: 'p', module: 'm', group: '0449', doc_ref: 'p.m.0449.0001-B' }),
    )

    expect(token).toBeNull()
    expect(tokenIssueCalls()).toHaveLength(0)
  })

  it('401 keeps its dedicated login guidance (unchanged contract)', async () => {
    postRequest.mockRejectedValueOnce(httpError(401, {}))

    const token = await withComposable((api) =>
      api.issueToken({ project: 'p', module: 'm', group: '0449', doc_ref: 'p.m.0449.0001-B' }),
    )

    expect(token).toBeNull()
    expect(tokenIssueCalls()).toHaveLength(0)
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('main.flow_gate_token.login_required'), 'danger')
  })

  it('403 keeps its dedicated permission guidance (unchanged contract)', async () => {
    postRequest.mockRejectedValueOnce(httpError(403, {}))

    await withComposable((api) =>
      api.issueToken({ project: 'p', module: 'm', group: '0449', doc_ref: 'p.m.0449.0001-B' }),
    )

    expect(tokenIssueCalls()).toHaveLength(0)
    expect(showToast).toHaveBeenCalledWith(i18n.global.t('main.flow_gate_token.permission_denied'), 'danger')
  })

  it('a successful advance still returns the minted token and never touches /token/issue', async () => {
    postRequest.mockResolvedValueOnce({
      data: { token: 'raw', token_id: 'tk_1', expires_at: 'x', scratch_dir: 'd', action_scope: 'new', doc_ref: 'p.m.0449.0001-B' },
    })

    const token = await withComposable((api) =>
      api.issueToken({ project: 'p', module: 'm', group: '0449', doc_ref: 'p.m.0449.0001-B' }),
    )

    expect(token?.raw_token).toBe('raw')
    expect(advanceCalls()).toHaveLength(1)
    expect(tokenIssueCalls()).toHaveLength(0)
  })

  it('POSITIVE CONTROL — an edit scope still goes straight to /token/issue', async () => {
    // Guards the "0 token/issue calls" assertions above from passing for the wrong reason
    // (a mock that never resolves, a composable that never calls anything).
    postRequest.mockResolvedValueOnce({
      data: { raw_token: 'raw', token_id: 'tk_2', expires_at: 'x', scratch_dir: 'd', action_scope: 'edit' },
    })

    const token = await withComposable((api) =>
      api.issueToken({ project: 'p', module: 'm', group: '0449', action_scope: 'edit', doc_ref: 'p.m.0449.0001-B' }),
    )

    expect(token?.raw_token).toBe('raw')
    expect(advanceCalls()).toHaveLength(0)
    expect(tokenIssueCalls()).toHaveLength(1)
  })
})

describe('parseAdvanceFailure — one refusal parser for both advance callers', () => {
  it('prefers `error`, then `code`, and keeps `detail`/`message`', () => {
    expect(parseAdvanceFailure(httpError(409, { error: 'sequence_exhausted', detail: 'no head' })))
      .toEqual({ code: 'sequence_exhausted', message: 'no head' })
    expect(parseAdvanceFailure(httpError(400, { code: 'sequence_not_decided', message: 'decide first' })))
      .toEqual({ code: 'sequence_not_decided', message: 'decide first' })
    expect(parseAdvanceFailure(new Error('Network Error')))
      .toEqual({ code: 'issue_failed', message: undefined })
  })

  it('advanceWithWorkPlanScope reports through the same parser', async () => {
    postRequest.mockRejectedValueOnce(httpError(409, { error: 'head_in_progress', detail: 'WP running' }))

    const result = await withComposable((api) =>
      api.advanceWithWorkPlanScope({
        docId: 'p.m.0449.0001-B',
        workPlanScope: { quantity_type_codes: [], provider_ids: [], note: '', provider_id: '' },
      }),
    )

    expect(result.token).toBeNull()
    expect(result.error).toEqual({ code: 'head_in_progress', message: 'WP running' })
    expect(tokenIssueCalls()).toHaveLength(0)
  })
})
