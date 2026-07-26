import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import DocInfoPanel from '@main/components/DocInfoPanel.vue'

// 0325 R0001 / N0004 §2·§3 — 최종 승인(AC) 화면의 사이드바.
// "승인 후 머지 할까 말까 고민되는데 어떤 파일이 수정됐는지 볼 길이 없다" 가 R0001 이다.
// AC 에서만 [소스 변경 요약] 이 뜨고, 대신 질의 응답 · AI 검수 의견 · 반려 사유는 감춘다.
// 디렉터리별 파일 수 목록은 N0004 §2 가 명시적으로 뺀 항목이라 여기서 없음을 못박는다.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const CHANGES = [
  { path: 'server/modules/flow_gate/services/git_service.py', status: 'M', insertions: 60, deletions: 4 },
  { path: 'client/src/main/components/DocInfoPanel.vue', status: 'M', insertions: 120, deletions: 3 },
  { path: 'client/tests/main/DocInfoPanel.acChanges.spec.ts', status: '?', insertions: 90, deletions: 0 },
  { path: 'client/src/main/components/Obsolete.vue', status: 'D', insertions: 0, deletions: 40 },
  { path: 'assets/logo.png', status: 'M', insertions: null, deletions: null },
]

// 0325 TR0007 rev1 — the per-file diff the [변경사항 열기] viewer reads. Matched BEFORE
// the changes list because both URLs live under /git/groups/{gid}/.
const FILE_DIFF = {
  binary: false, oversized: false, untracked: false, truncated: false,
  insertions: 1, deletions: 0,
  hunks: [{
    old_start: 1, old_lines: 1, new_start: 1, new_lines: 2, section: '',
    lines: [{ kind: 'add', old_lineno: null, new_lineno: 2, text: 'added line' }],
  }],
}

function routeRequest(changes: unknown[] = CHANGES, finalize = { ahead_count: 2, behind_count: 0 }) {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/diff?path=')) return Promise.resolve({ data: { data: FILE_DIFF } })
    if (url.includes('/git/groups/')) {
      return Promise.resolve({
        data: { data: { branch: 'flowgate_default_0325', base_branch: 'main', changes } },
      })
    }
    if (url.includes('/git/finalize')) return Promise.resolve({ data: { state: finalize } })
    return Promise.resolve({ data: { qa: { items: [] } } })
  })
}

function mountPanel(typeCode: string, groupId: string | null = 'flowgate.default.0325') {
  return mount(DocInfoPanel, {
    props: {
      docId: 'flowgate.default.0325.0007-AC',
      typeCode,
      groupId,
      reviewStatus: 'pending_review',
      rejectReason: '이전 단계의 반려 사유',
      stepStates: [],
      nextStepIndex: null,
      collapsed: false,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  routeRequest()
})

describe('DocInfoPanel source-change summary (0325 R0001)', () => {
  it('shows the summary on AC and hides Q&A / AI review / rejection', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-chg-headline').exists()).toBe(true)
    expect(wrapper.text()).toContain('Source Changes')
    // N0004 §3: 이 세 섹션은 최종 승인 시점에 자리를 내준다.
    expect(wrapper.text()).not.toContain('Q&A')
    expect(wrapper.text()).not.toContain('AI Review')
    expect(wrapper.text()).not.toContain('Rejection Reason')
    expect(wrapper.text()).not.toContain('이전 단계의 반려 사유')
  })

  it('keeps the three sections and omits the summary on a non-AC document', async () => {
    const wrapper = mountPanel('TR')
    await flushPromises()

    expect(wrapper.find('.dip-chg-headline').exists()).toBe(false)
    expect(wrapper.text()).toContain('Q&A')
    expect(wrapper.text()).toContain('Rejection Reason')
  })

  it('totals files and +/- lines, skipping unknown (binary) counts', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-chg-files').text()).toBe('5 file(s) changed')
    // 60+120+90+0 = 270 / 4+3+0+40 = 47. The binary row contributes nothing.
    expect(wrapper.find('.dip-chg-add').text()).toBe('+270')
    expect(wrapper.find('.dip-chg-del').text()).toBe('−47')
    expect(wrapper.find('.dip-chg-note').exists()).toBe(false)
  })

  it('counts an untracked file as added and lists only non-empty kinds', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    const kinds = wrapper.findAll('.dip-chg-kind').map((row) => row.text().replace(/\s+/g, ' '))
    expect(kinds).toEqual(['AAdded1', 'MModified3', 'DDeleted1'])
  })

  it('omits the +/- pair entirely when no file reported a line count', async () => {
    routeRequest([{ path: 'assets/logo.png', status: 'M', insertions: null, deletions: null }])
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-chg-lines').exists()).toBe(false)
    expect(wrapper.find('.dip-chg-note').exists()).toBe(true)
  })

  it('renders the ahead/behind line from the finalize state', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-chg-branch').text()).toContain('2 commit(s) ahead of')
  })

  it('drops the branch line but keeps the counts when finalize fails', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/groups/')) return Promise.resolve({ data: { data: { changes: CHANGES } } })
      if (url.includes('/git/finalize')) return Promise.reject(new Error('boom'))
      return Promise.resolve({ data: { qa: { items: [] } } })
    })
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-chg-files').exists()).toBe(true)
    expect(wrapper.find('.dip-chg-branch').exists()).toBe(false)
  })

  it('reports a load failure instead of an empty summary', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/git/groups/')) return Promise.reject(new Error('boom'))
      return Promise.resolve({ data: { qa: { items: [] } } })
    })
    const wrapper = mountPanel('AC')
    await flushPromises()

    expect(wrapper.find('.dip-qa-error').text()).toContain('Could not load the source changes')
    expect(wrapper.find('.dip-chg-headline').exists()).toBe(false)
  })

  it('carries no per-directory breakdown (N0004 §2 removed it)', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    // 파일 경로 자체가 사이드바에 늘어놓이지 않는다 — 요약은 숫자만이다.
    expect(wrapper.text()).not.toContain('git_service.py')
    expect(wrapper.text()).not.toContain('server/…')
  })

  // ── 반려 반영 (TR0007 rev1): 시안의 [변경사항 열기] ───────────────────────────
  // 요약만으로는 "머지할까 말까"가 안 풀린다는 것이 반려의 요지였다. 요약 아래의 이
  // 버튼이 실제 diff 열람 화면을 열고, 닫으면 승인 화면 그대로 돌아온다.
  it('offers [변경사항 열기] under the summary and opens the changes viewer', async () => {
    const wrapper = mountPanel('AC')
    await flushPromises()

    const open = wrapper.find('.dip-chg-open')
    expect(open.exists()).toBe(true)
    expect(open.text()).toContain('Open changes')
    // Nothing is fetched or mounted until it is actually clicked.
    expect(wrapper.find('.gcd-dialog').exists()).toBe(false)
    expect(getRequest).not.toHaveBeenCalledWith(expect.stringContaining('/diff?path='))

    await open.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('.gcd-dialog')
    expect(dialog.exists()).toBe(true)
    // The viewer gets the file set the summary already loaded — no second /changes call.
    expect(wrapper.findAll('.gcd-file')).toHaveLength(CHANGES.length)
    expect(dialog.text()).toContain('flowgate_default_0325')
    expect(getRequest).toHaveBeenCalledWith(expect.stringContaining('/diff?path='))

    // Closing returns to the approval screen with the summary still in place.
    await wrapper.find('.gcd-back').trigger('click')
    await flushPromises()
    expect(wrapper.find('.gcd-dialog').exists()).toBe(false)
    expect(wrapper.find('.dip-chg-headline').exists()).toBe(true)
  })

  it('offers no entry point where there is no summary or nothing changed', async () => {
    const nonAc = mountPanel('TR')
    await flushPromises()
    expect(nonAc.find('.dip-chg-open').exists()).toBe(false)

    routeRequest([])
    const empty = mountPanel('AC')
    await flushPromises()
    // Nothing to read → the button would open an empty screen, so it is not offered.
    expect(empty.find('.dip-chg-open').exists()).toBe(false)
  })

  it('skips the section when the group id is unknown', async () => {
    const wrapper = mountPanel('AC', null)
    await flushPromises()

    expect(wrapper.find('.dip-chg-headline').exists()).toBe(false)
    expect(getRequest).not.toHaveBeenCalledWith(expect.stringContaining('/git/finalize'))
  })
})
