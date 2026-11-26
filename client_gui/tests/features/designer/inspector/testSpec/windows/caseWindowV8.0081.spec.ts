// F81 case window v8: evidence as one fixed row per kind + the kind's modeless evidence window (search, delete, add).
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import TestEvidenceRows from '@/features/documentWindow/TestEvidenceRows.vue'
import TestEvidenceWindow from '@/features/documentWindow/TestEvidenceWindow.vue'
import { useTestEvidence, type TestEvidence } from '@/features/documentWindow/testEvidenceModel'
import { besideSpot } from '@/features/designer/inspector/features/testSpec/windows/scenarioWindowModel'
import i18n from '@shared/i18n'
const api = vi.hoisted(() => ({ getRequest: vi.fn(), postFormRequest: vi.fn(), downloadBlobRequest: vi.fn(), deleteRequest: vi.fn(), patchRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => api)

type Kind = 'input' | 'log' | 'output'
const file = (key: string, kind: Kind, attempt = 1) => ({ key, filename: `${key}.txt`, download_url: `/d/${key}`, preview_url: `/p/${key}`, test_evidence: { kind, attempt } })
let stored: ReturnType<typeof file>[] = []
const many = (kind: Kind, n: number) => Array.from({ length: n }, (_, i) => file(`${kind}-${String(i + 1).padStart(3, '0')}`, kind))

function harness(open: Kind[] = [], caseId = ref('TC-1')) {
  const eligibility: (string | null)[] = []
  let changed = 0
  let model!: TestEvidence
  const Host = defineComponent({
    setup() {
      model = useTestEvidence(() => ({ tsId: 'ts', caseId: caseId.value, flowId: 'flow-1', revision: 1 }), {
        eligibility: (value) => { eligibility.push(value) }, changed: () => { changed++ },
      })
      const kinds = ref<Kind[]>(open)
      return () => h('div', [
        h(TestEvidenceRows, { evidence: model, open: kinds.value, onToggle: (kind: Kind) => { kinds.value = kinds.value.includes(kind) ? kinds.value.filter(k => k !== kind) : [...kinds.value, kind] } }),
        ...kinds.value.map(kind => h(TestEvidenceWindow, { key: kind, evidence: model, kind, caseId: 'TC-1', x: 0, y: 0 })),
      ])
    },
  })
  const wrapper = mount(Host, { global: { plugins: [i18n], stubs: { teleport: true } }, attachTo: document.body })
  return { wrapper, eligibility, changed: () => changed, model: () => model }
}

describe('case window v8 evidence rows', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    i18n.global.locale.value = 'ko'
    stored = []
    api.getRequest.mockImplementation(async (url: string) => {
      if (url.endsWith('/preview')) return { data: { kind: 'text', text: 'head' } }
      if (url === '/api/v1/flows/flow-1/attachments') return { data: { attachments: stored.map(f => ({ key: f.key, content_sha256: `sha-${f.key}` })) } }
      return { data: { attachments: stored, cases: { 'TC-1': { attempt: 1, pass_reason: null } } } }
    })
    api.postFormRequest.mockResolvedValue({ data: {} })
    api.deleteRequest.mockImplementation(async (url: string) => {
      const key = decodeURIComponent(url.split('/attachments/')[1].split('?')[0])
      stored = stored.filter(f => f.key !== key)
      return { data: { deleted: true } }
    })
  })

  it('shows name · count · the two newest files · [전체 N] · ＋ 추가, and only name + add for an empty kind', async () => {
    stored = [...many('input', 3), file('out-1', 'output')]
    const { wrapper, eligibility } = harness()
    await flushPromises()
    const input = wrapper.get('[data-test="case-evidence-input"]')
    expect(input.get('[data-test="case-evidence-count-input"]').text()).toBe('3')
    expect(input.findAll('[data-test="case-evidence-recent"]').map(c => c.text())).toEqual(['input-003.txt', 'input-002.txt'])
    expect(input.get('[data-test="case-evidence-all-input"]').text()).toContain('전체 3')
    const log = wrapper.get('[data-test="case-evidence-log"]')
    expect(log.find('[data-test="case-evidence-count-log"]').exists()).toBe(false)
    expect(log.find('[data-test="case-evidence-all-log"]').exists()).toBe(false)
    expect(log.findAll('[data-test="case-evidence-recent"]')).toHaveLength(0)
    expect(log.text()).toBe('로그＋ 추가')
    expect(log.text()).not.toContain('없음')
    expect(eligibility.at(-1)).toBeNull()
    wrapper.unmount()
  })

  it('keeps the same rows for 20 or 200 files per kind (only two names drawn, no list in the case window)', async () => {
    const shape = async (n: number) => {
      stored = [...many('input', n), ...many('log', n), ...many('output', n)]
      const { wrapper } = harness()
      await flushPromises()
      const out = ['input', 'log', 'output'].map(kind => ({
        chips: wrapper.get(`[data-test="case-evidence-${kind}"]`).findAll('[data-test="case-evidence-recent"]').length,
        count: wrapper.get(`[data-test="case-evidence-count-${kind}"]`).text(),
      }))
      const nodes = wrapper.get('[data-test="case-evidence"]').findAll('*').length
      wrapper.unmount()
      return { out, nodes }
    }
    const twenty = await shape(20), twoHundred = await shape(200)
    expect(twenty.out.map(o => o.chips)).toEqual([2, 2, 2]); expect(twoHundred.out.map(o => o.chips)).toEqual([2, 2, 2])
    expect(twenty.out.map(o => o.count)).toEqual(['20', '20', '20']); expect(twoHundred.out.map(o => o.count)).toEqual(['200', '200', '200'])
    expect(twoHundred.nodes).toBe(twenty.nodes)
    // Previews are read only for an open evidence window's kind, never for the rows.
    expect(api.getRequest.mock.calls.some(([url]) => String(url).endsWith('/preview'))).toBe(false)
  })

  it('opens the kind window from [전체 N]: search, then delete one — the row count drops', async () => {
    stored = many('output', 12)
    const { wrapper, changed } = harness()
    await flushPromises()
    await wrapper.get('[data-test="case-evidence-all-output"]').trigger('click'); await flushPromises()
    const all = wrapper.get('[data-test="case-evidence-all-output"]')
    expect(all.classes()).toContain('all--open')
    expect(wrapper.findAll('[data-test="case-evidence-file"]')).toHaveLength(12)
    await wrapper.get('[data-test="case-evidence-search"]').setValue('output-01')
    const hits = wrapper.findAll('[data-test="case-evidence-file"]')
    expect(hits.map(f => f.attributes('data-key'))).toEqual(['output-012', 'output-011', 'output-010'])
    await hits[1].get('[data-test="case-evidence-delete"]').trigger('click'); await flushPromises()
    expect(api.deleteRequest).toHaveBeenCalledWith('/api/v1/flows/flow-1/attachments/output-011?expected_sha256=sha-output-011')
    expect(wrapper.get('[data-test="case-evidence-count-output"]').text()).toBe('11')
    expect(wrapper.findAll('[data-test="case-evidence-file"]').map(f => f.attributes('data-key'))).toEqual(['output-012', 'output-010'])
    expect(changed()).toBe(1)
    wrapper.unmount()
  })

  it('adds from the window with the server attempt and kind, as the old evidence box did', async () => {
    stored = [file('log-1', 'log')]
    const { wrapper, changed } = harness(['log'])
    await flushPromises()
    const panel = wrapper.findAll('.evidence-window')[0]
    await panel.trigger('drop', { dataTransfer: { files: [new File(['a'], 'a.log')] } }); await flushPromises()
    const [url, form] = api.postFormRequest.mock.calls[0]
    expect(url).toBe('/api/v1/documents/ts/test-spec/cases/TC-1/evidence')
    expect(form.get('kind')).toBe('log'); expect(form.get('attempt')).toBe('1')
    expect(changed()).toBe(1)
    wrapper.unmount()
  })

  it('empties the rows while another case is read: no [전체 N] of the previous case, no stale key with the new case', async () => {
    stored = many('output', 5)
    const caseId = ref('TC-1')
    const { wrapper, model } = harness([], caseId)
    await flushPromises()
    expect(wrapper.get('[data-test="case-evidence-count-output"]').text()).toBe('5')
    const old = model().files.value[0]
    let release!: () => void
    api.getRequest.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ data: { attachments: [file('other-1', 'output')], cases: { 'TC-2': { attempt: 1, pass_reason: null } } } }) }))
    caseId.value = 'TC-2'; await flushPromises()
    // The new list is still being read: nothing of TC-1 is left to open, download or delete.
    expect(wrapper.find('[data-test="case-evidence-count-output"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="case-evidence-all-output"]').exists()).toBe(false)
    expect(wrapper.findAll('[data-test="case-evidence-recent"]')).toHaveLength(0)
    // A TC-1 file kept by a caller is never fetched or deleted against TC-2.
    await model().download(old); await model().remove(old)
    expect(api.downloadBlobRequest).not.toHaveBeenCalled(); expect(api.deleteRequest).not.toHaveBeenCalled()
    release(); await flushPromises()
    expect(wrapper.get('[data-test="case-evidence-count-output"]').text()).toBe('1')
    expect(wrapper.findAll('[data-test="case-evidence-recent"]').map(c => c.text())).toEqual(['other-1.txt'])
    wrapper.unmount()
  })

  it('keeps the same case list on screen while it is read again after a delete (add and delete off meanwhile)', async () => {
    stored = many('log', 3)
    const { wrapper, model } = harness(['log'])
    await flushPromises()
    let release!: () => void
    api.getRequest.mockImplementationOnce(() => new Promise(resolve => { release = () => resolve({ data: { attachments: stored, cases: { 'TC-1': { attempt: 1, pass_reason: null } } } }) }))
    void model().refresh(); await flushPromises()
    expect(wrapper.get('[data-test="case-evidence-count-log"]').text()).toBe('3')
    expect(wrapper.findAll('[data-test="case-evidence-delete"]').every(b => b.attributes('disabled') !== undefined)).toBe(true)
    release(); await flushPromises()
    wrapper.unmount()
  })

  it('folds the earlier attempts\' files under 「이전 시도 (n)」, closed at first, as the old evidence box did', async () => {
    stored = [file('out-a1', 'output', 1), file('out-b1', 'output', 1), file('out-c2', 'output', 2)]
    api.getRequest.mockImplementation(async (url: string) => {
      if (url.endsWith('/preview')) return { data: { kind: 'text', text: 'head' } }
      return { data: { attachments: stored, cases: { 'TC-1': { attempt: 2, pass_reason: null } } } }
    })
    const { wrapper } = harness(['output'])
    await flushPromises()
    const previous = wrapper.get('[data-test="case-evidence-previous"]')
    expect((previous.element as HTMLDetailsElement).open).toBe(false)
    expect(previous.get('[data-test="case-evidence-previous-summary"]').text()).toBe('이전 시도 (2)')
    expect(previous.findAll('[data-test="case-evidence-file"]').map(f => f.attributes('data-key'))).toEqual(['out-b1', 'out-a1'])
    const list = wrapper.get('[data-test="case-evidence-list"]')
    const thisAttempt = () => list.findAll('[data-test="case-evidence-file"]').filter(f => !f.element.closest('details'))
    expect(thisAttempt().map(f => f.attributes('data-key'))).toEqual(['out-c2'])
    // A search narrows both groups; the earlier attempts stay folded.
    await wrapper.get('[data-test="case-evidence-search"]').setValue('b1')
    expect(wrapper.get('[data-test="case-evidence-previous-summary"]').text()).toBe('이전 시도 (1)')
    expect(thisAttempt()).toHaveLength(0)
    wrapper.unmount()
  })

  it('fails closed when the listing cannot be read: no add, pass stays off', async () => {
    api.getRequest.mockRejectedValue(new Error('denied'))
    const { wrapper, eligibility } = harness()
    await flushPromises()
    expect(wrapper.get('[data-test="case-evidence-failure"]').exists()).toBe(true)
    expect(eligibility.at(-1)).toBe('evidence_unavailable')
    expect(wrapper.findAll('input[type="file"]').every(input => input.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
})

describe('case window v8 side windows: beside the case window, never over it', () => {
  const overlaps = (a: { x: number; width: number }, b: { x: number; width: number }) => a.x < b.x + b.width && b.x < a.x + a.width
  it('goes right at 420px when there is room', () => {
    expect(besideSpot({ x: 220, width: 640 }, 1920)).toEqual({ x: 872, width: 420, moved: null })
  })
  it('1280px screen, default case window (x=220, 640): right side narrowed to 400px, not over the case window', () => {
    const spot = besideSpot({ x: 220, width: 640 }, 1280)
    expect(spot).toEqual({ x: 872, width: 400, moved: null })
    expect(overlaps(spot, { x: 220, width: 640 })).toBe(false)
    expect(spot.x + spot.width).toBeLessThanOrEqual(1280)
  })
  it('goes left when the left has the room', () => {
    const spot = besideSpot({ x: 800, width: 640 }, 1500)
    expect(spot).toEqual({ x: 368, width: 420, moved: null })
    expect(overlaps(spot, { x: 800, width: 640 })).toBe(false)
  })
  it('moves (and on a narrow screen narrows) the case window when neither side has 280px', () => {
    for (const viewport of [900, 1000, 1100]) {
      const spot = besideSpot({ x: 220, width: 640 }, viewport)
      expect(spot.moved).not.toBeNull()
      expect(overlaps(spot, spot.moved!)).toBe(false)
      expect(spot.width).toBeGreaterThanOrEqual(280)
      expect(spot.x + spot.width).toBeLessThanOrEqual(viewport)
      expect(spot.moved!.x).toBeGreaterThanOrEqual(8)
    }
  })
  it('never overlaps for any case window place on common screens', () => {
    for (const viewport of [1024, 1280, 1366, 1440, 1920]) for (let x = 0; x <= viewport - 640; x += 37) {
      const spot = besideSpot({ x, width: 640 }, viewport)
      expect(overlaps(spot, spot.moved ?? { x, width: 640 })).toBe(false)
    }
  })
})
