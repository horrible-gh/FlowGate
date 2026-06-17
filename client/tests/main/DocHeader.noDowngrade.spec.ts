import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocHeader from '@main/components/DocHeader.vue'

// Regression (group 0040 / NR0003 rev6): the 6연차 root cause.
//
// A background detail GET can start before a manual workflow decision, read the
// pre-decision state, and return after the POST succeeds and the header optimistically
// flips to wf_in_progress. The response is valid but obsolete. Applying it used to restore
// the pre-decision action bar, allowing a second click that hit 409 already_decided.
//
// The primary fix is a request generation: only the newest detail request may commit, and
// a confirmed transition invalidates requests that started before it. The no-downgrade
// invariant remains as defense in depth for a current silent response.

const { getRequest, postRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

// 'undecided' = a valid representation read before the workflow decision: approved, no head.
// 'decided'   = the normal post-decision representation the server stores synchronously.
// 'fail'      = the detail GET rejects (both attempts).
let detailMode: 'undecided' | 'decided' | 'fail' = 'undecided'
let queuedDetailResponses: Promise<any>[] = []

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function detailResponse(mode: 'undecided' | 'decided' = detailMode === 'decided' ? 'decided' : 'undecided') {
  const base = {
    doc_id: 'test.none.0040.0001-R',
    title: 'test',
    status: 'closed',
    type_code: 'R',
    is_editable: true,
    project_id: 'test',
    group_id: 'test.none.0040',
  }
  if (mode === 'decided') {
    return {
      data: {
        ...base,
        doc_review_status: 'wf_in_progress',
        workflow_steps: ['N', 'NR'],
        workflow_head_type: 'N',
        workflow_head_index: 0,
      },
    }
  }
  // 'undecided' — note: NO workflow_head_type, NO workflow_steps.
  return { data: { ...base, doc_review_status: 'approved', workflow_steps: null } }
}

const TAB = { id: 'test.none.0040.0001-R', title: 'test', path: '', type: 'md', typeCode: 'R' }
const PAYLOAD = { docClass: 'R', sequence: [{ type: 'N' }, { type: 'NR' }] }

function mountHeader() {
  return shallowMount(DocHeader, {
    props: { tab: TAB as any },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  detailMode = 'undecided'
  queuedDetailResponses = []
  getRequest.mockReset()
  postRequest.mockReset()
  showToast.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/documents/detail')) {
      const queued = queuedDetailResponses.shift()
      if (queued) return queued
      return detailMode === 'fail'
        ? Promise.reject(new Error('network'))
        : Promise.resolve(detailResponse())
    }
    if (url.includes('/groups')) return Promise.resolve({ data: { groups: [] } })
    return Promise.resolve({ data: {} })
  })
  postRequest.mockResolvedValue({ data: { ok: true } })
})

describe('DocHeader request-order and no-downgrade guards (NR0003 rev6)', () => {
  it('ignores a pre-decision GET that returns after the decision and its newer backfill start', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('approved')

    const stalePreDecision = deferred<any>()
    const currentBackfill = deferred<any>()
    queuedDetailResponses.push(stalePreDecision.promise, currentBackfill.promise)

    // G0 starts while the document is still undecided and remains in flight.
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'test' } }))
    await flushPromises()

    // P1 succeeds, the optimistic transition is committed, and G1 starts.
    const decision = (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowHeadType).toBe('N')

    // G0 arrives late with the valid pre-decision snapshot. It must not commit.
    stalePreDecision.resolve(detailResponse('undecided'))
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowHeadType).toBe('N')

    currentBackfill.resolve(detailResponse('decided'))
    await decision
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    wrapper.unmount()
  })

  it('a current silent response still cannot downgrade an already-decided doc', async () => {
    const wrapper = mountHeader()
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('approved')

    // The current backfill returns an undecided representation.
    detailMode = 'undecided'
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    // Decided markers survive the response, so the button stays gone.
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowHeadType).toBe('N')
    wrapper.unmount()
  })

  it('the optimistic flip seeds workflow_head_type from the first step', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Both backfill attempts fail → only the optimistic state remains.
    detailMode = 'fail'
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowHeadType).toBe('N')
    wrapper.unmount()
  })

  it('downgrade also carries workflow_steps forward so the strip does not blank', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    detailMode = 'undecided' // approved + workflow_steps: null
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    expect((wrapper.vm as any).workflowSteps).toEqual(['N', 'NR'])
    wrapper.unmount()
  })

  it('server wins when the silent backfill IS decided (no false preservation)', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    detailMode = 'decided'
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()

    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')
    expect((wrapper.vm as any).workflowSteps).toEqual(['N', 'NR'])
    expect((wrapper.vm as any).workflowHeadType).toBe('N')
    wrapper.unmount()
  })

  it('the guard is silent-only: a non-silent (deliberate) reload still honors server truth', async () => {
    const wrapper = mountHeader()
    await flushPromises()

    // Get into a locally-decided state via a successful decided backfill.
    detailMode = 'decided'
    await (wrapper.vm as any).onWorkflowConfirmed(PAYLOAD)
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('wf_in_progress')

    // An explicit, non-silent reload that returns an undecided doc must replace it — the
    // guard protects only transient silent backfills, never a deliberate tab load.
    detailMode = 'undecided'
    await (wrapper.vm as any).fetchDoc(TAB.id)
    await flushPromises()
    expect((wrapper.vm as any).docReviewStatus).toBe('approved')
    wrapper.unmount()
  })
})
