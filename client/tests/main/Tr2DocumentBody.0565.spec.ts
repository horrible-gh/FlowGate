import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import Tr2DocumentBody from '@main/components/documents/Tr2DocumentBody.vue'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest }))

const docId = 'flowgate.default.0565.0009-TR2'
const tab = { id: docId, title: 'Connected TR2', type: 'md' as const, typeCode: 'TR2', path: 'document.json', projectId: 'flowgate' }
const body = {
  tr2_version: 1, source_t2_doc_id: 'flowgate.default.0565.0008-T2',
  edit_spec: {
    termination: 'ready_to_apply',
    edits: [{ id: 'e1', kind: 'edit', file: 'a.txt', anchor_old: 'before', replacement_new: 'after', confidence: 'high', rationale: 'fix' }],
    deferred: [{ id: 'd1', reason: 'needs_runtime', rationale: 'runtime only' }],
    gate: { commands: ['pytest -q'] },
  },
}
function view(revision = 2) {
  return {
    document: { revision_no: revision, doc_review_status: 'pending_review', editable: true }, body,
    derived: { files: [{ path: 'a.txt', kind: 'edit', exists: true, edit_ids: ['e1'] }], live_precheck: { drift: false, baseline_fingerprint: 'sha256:base', live_fingerprint: 'sha256:base' } },
    approval: { latest_attempt: null, attempts: [] }, history: { source_history_state: 'aligned', ledger: [] },
  }
}
const mounted: Array<ReturnType<typeof mount>> = []
function mountBody(readOnly = false) { const wrapper = mount(Tr2DocumentBody, { props: { tab, readOnly } }); mounted.push(wrapper); return wrapper }
beforeEach(() => {
  getRequest.mockReset(); postRequest.mockReset(); putRequest.mockReset()
  getRequest.mockImplementation((url: string) => Promise.resolve({ data: url.includes('/files/')
    ? { path: 'a.txt', kind: 'edit', before_text: 'before', edits: body.edit_spec.edits }
    : view() }))
  postRequest.mockResolvedValue({ data: { ready: true, code: null, worktree_clean: true } })
  putRequest.mockResolvedValue({ data: { new_revision: 3 } })
})
afterEach(() => { mounted.forEach((wrapper) => wrapper.unmount()); mounted.length = 0 })

describe('TR2 connected body', () => {
  it('reads server projection, file detail and live diagnostic', async () => {
    const wrapper = mountBody(); await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(docId)}/tr2`)
    expect(wrapper.text()).toContain('Source history')
    expect(wrapper.text()).toContain('needs_runtime')
    await wrapper.find('.tr2-files button').trigger('click'); await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(expect.stringContaining('/tr2/files/a.txt'))
    expect(wrapper.find('.tr2-detail').text()).toContain('AFTER')
    expect(wrapper.find('.tr2-detail').text()).toContain('after')
    await wrapper.findAll('button').find((button) => button.text().includes('Live diagnostic'))!.trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(expect.stringContaining('/tr2/precheck'), {})
    expect(wrapper.text()).toContain('ready')
  })

  it('saves only editable edit_spec with revision CAS and refreshes', async () => {
    const wrapper = mountBody(); await flushPromises()
    await wrapper.findAll('button').find((button) => button.text().includes('Edit specification'))!.trigger('click')
    const textarea = wrapper.find('textarea')
    const spec = structuredClone(body.edit_spec); spec.edits[0].replacement_new = 'changed'
    await textarea.setValue(JSON.stringify(spec))
    await wrapper.findAll('button').find((button) => button.text() === 'Save')!.trigger('click')
    await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(docId)}/tr2`, {
      expected_revision: 2, body: { tr2_version: 1, source_t2_doc_id: body.source_t2_doc_id, edit_spec: spec },
    })
    expect(getRequest).toHaveBeenCalledTimes(2)
  })

  it('blocks edits in read-only mode and refreshes on document SSE', async () => {
    const wrapper = mountBody(true); await flushPromises()
    expect(wrapper.findAll('button').find((button) => button.text().includes('Edit specification'))!.attributes('disabled')).toBeDefined()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId } }))
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(2)
    expect(putRequest).not.toHaveBeenCalled()
  })
})
