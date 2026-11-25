import { mount, flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import EvidenceFiles from '@/features/documentWindow/TestEvidenceFiles.vue'
const api = vi.hoisted(() => ({ getRequest: vi.fn(), postFormRequest: vi.fn(), downloadBlobRequest: vi.fn() }))
vi.mock('@shared/api', () => api)
vi.mock('vue-i18n', async () => { const { ref } = await import('vue'); return { useI18n: () => ({ locale: ref('en') }) } })
const file = (key: string, kind: string, attempt: number) => ({ key, filename: `${key}.txt`, download_url: `/download/${key}`, preview_url: `/preview/${key}`, test_evidence: { kind, attempt } })
describe('case evidence files', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.getRequest.mockImplementation(async (url: string) => ({ data: url.endsWith('/preview') ? { kind: 'text', text: 'observed' } : {
      attachments: [file('old', 'output', 1), file('current', 'output', 2), file('input', 'input', 2)],
      cases: { 'TC-001': { attempt: 2, pass_reason: null } },
    } }))
    api.postFormRequest.mockResolvedValue({ data: {} })
  })
  it('separates kinds and previous attempts, previews text, emits eligibility', async () => {
    const wrapper = mount(EvidenceFiles, { props: { tsId: 'ts', caseId: 'TC-001' } })
    await flushPromises()
    const output = wrapper.get('[data-test="case-evidence-output"]')
    expect(output.get('details').text()).toContain('old.txt')
    expect(output.get('details').attributes('open')).toBeUndefined()
    expect(output.text()).toContain('current.txt')
    expect(output.text()).not.toContain('input.txt')
    expect(output.findAll('pre').map(p => p.text())).toEqual(['observed', 'observed'])
    expect(wrapper.emitted('eligibility')?.at(-1)).toEqual([null])
    wrapper.unmount()
  })
  it('uploads every dropped file with the server attempt and selected kind', async () => {
    const wrapper = mount(EvidenceFiles, { props: { tsId: 'ts', caseId: 'TC-001' } })
    await flushPromises()
    await wrapper.get('[data-test="case-evidence-log"]').trigger('drop', { dataTransfer: { files: [new File(['a'], 'a.log'), new File(['b'], 'b.log')] } })
    await flushPromises()
    expect(api.postFormRequest).toHaveBeenCalledTimes(2)
    for (const [url, form] of api.postFormRequest.mock.calls) {
      expect(url).toBe('/api/v1/documents/ts/test-spec/cases/TC-001/evidence')
      expect(form.get('kind')).toBe('log'); expect(form.get('attempt')).toBe('2')
    }
    expect(wrapper.emitted('uploaded')).toHaveLength(1)
    wrapper.unmount()
  })
  it('fails closed after a read error and prevents uploading', async () => {
    api.getRequest.mockRejectedValue(new Error('denied'))
    const wrapper = mount(EvidenceFiles, { props: { tsId: 'ts', caseId: 'TC-001' } })
    await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('denied')
    expect(wrapper.emitted('eligibility')?.at(-1)).toEqual(['evidence_unavailable'])
    expect(wrapper.findAll('input').every(input => input.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
})
