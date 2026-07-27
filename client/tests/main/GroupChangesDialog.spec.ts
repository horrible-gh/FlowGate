// flowgate.default.0325 TR0007 rev1 — [변경사항 열기] 가 여는 변경사항 열람 화면.
// 반려 사유: "시안에서 제시한 [변경사항 열기] 가 왜 반영되어 있지 않지? 이게 핵심인데?"
// 요약(사이드바)은 "몇 파일 · 몇 줄"까지만 답하고, R0001 이 물은 "소스가 잘 됐는지"는
// 실제 diff 를 읽어야 답이 난다. 이 스펙은 시안(TR0003 §4) 후보 ③의 구성 요소가 실제로
// 동작하는지를 못박는다: 파일 목록(상태 배지 · 파일별 +/− · 검색 · 상태 필터),
// 통합/분할 diff, 파일 간 이동, 그리고 실패·바이너리·신규 파일의 처리.
//
// flowgate.default.0329 NR0003 — 서버 응답이 hunks 가 아니라 old/new 전체 내용으로
// 바뀌었다(0326 NR0005 §4 의 계약으로 단일화). 목이 실제 서버 응답 모양을 흉내내지
// 않으면 계약이 깨져도 이 스펙은 계속 초록으로 남는다는 것이 NR0003 §6 의 핵심
// 발견이었으므로, 아래 목은 read_group_file_diff 가 실제로 반환하는 old/new 구조를
// 그대로 따른다.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupChangesDialog from '@main/components/GroupChangesDialog.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

const CHANGES = [
  { path: 'server/services/git_service.py', status: 'M', insertions: 60, deletions: 4 },
  { path: 'client/src/main/components/DocInfoPanel.vue', status: 'M', insertions: 120, deletions: 3 },
  { path: 'client/tests/main/GroupChangesDialog.spec.ts', status: '?', insertions: 90, deletions: 0 },
  { path: 'client/src/main/components/Obsolete.vue', status: 'D', insertions: 0, deletions: 40 },
  { path: 'assets/logo.png', status: 'M', insertions: null, deletions: null },
]

// NR0003 (flowgate.default.0329) — the server ships old/new file CONTENT, not
// pre-parsed hunks (the same contract flowgate.default.0326's base file explorer
// uses); the dialog derives its own line diff via useFileDiff. One replaced line
// plus one pure addition, framed by a context line on each side.
const MODIFIED_DIFF = {
  path: 'server/services/git_service.py',
  status: 'M',
  old: { exists: true, binary: false, truncated: false, size: 26, content: 'keep one\nold line\nkeep two\n' },
  new: { exists: true, binary: false, truncated: false, size: 42, content: 'keep one\nnew line\nextra line\nkeep two\n' },
}

function diffFor(path: string): Record<string, unknown> {
  if (path === 'assets/logo.png') {
    return {
      ...MODIFIED_DIFF, path, status: 'M',
      old: { exists: true, binary: true, truncated: false, size: 128, content: null },
      new: { exists: true, binary: true, truncated: false, size: 130, content: null },
    }
  }
  if (path.endsWith('.spec.ts')) {
    return {
      ...MODIFIED_DIFF, path, status: 'A',
      old: { exists: false, binary: false, truncated: false, size: 0, content: null },
      new: { exists: true, binary: false, truncated: false, size: 19, content: 'brand new content\n' },
    }
  }
  return { ...MODIFIED_DIFF, path }
}

function routeDiff(failFor: string | null = null) {
  getRequest.mockImplementation((url: string) => {
    const match = /\/diff\?path=([^&]+)/.exec(url)
    if (!match) return Promise.reject(new Error(`unexpected url: ${url}`))
    const path = decodeURIComponent(match[1])
    if (failFor && path === failFor) return Promise.reject(new Error('boom'))
    return Promise.resolve({ data: { data: diffFor(path) } })
  })
}

function mountDialog(changes: unknown[] = CHANGES) {
  return mount(GroupChangesDialog, {
    props: {
      projectId: 'flowgate',
      groupId: 'flowgate.default.0325',
      branch: 'flowgate_default_0325',
      baseBranch: 'main',
      changes,
    },
    global: { plugins: [i18n], stubs: { AppIcon: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  routeDiff()
  i18n.global.locale.value = 'en'
})

describe('GroupChangesDialog (0325 TR0007 rev1 — 변경사항 열기)', () => {
  it('lists every changed file with its status badge and +/- counts', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const files = wrapper.findAll('.gcd-file')
    expect(files).toHaveLength(5)
    expect(files[0].text()).toContain('git_service.py')
    expect(files[0].find('.gcd-add').text()).toBe('+60')
    expect(files[0].find('.gcd-del').text()).toBe('−4')
    // An untracked file is presented as added, not as an unknown '?' state.
    expect(files[2].find('.gcd-badge').text()).toBe('A')
    expect(files[2].find('.gcd-badge').classes()).toContain('gcd-badge-added')
    expect(files[3].find('.gcd-badge').classes()).toContain('gcd-badge-deleted')
    // Binary: no invented "+0 −0" pair, an explicit "unknown" instead.
    expect(files[4].find('.gcd-file-nostat').exists()).toBe(true)

    // Header totals mirror the sidebar summary: 60+120+90+0, 4+3+0+40.
    expect(wrapper.find('.gcd-hd-lines').text().replace(/\s+/g, '')).toBe('+270−47')
    expect(wrapper.find('.gcd-hd').text()).toContain('flowgate_default_0325')
    expect(wrapper.find('.gcd-hd').text()).toContain('main')
  })

  it('opens the first file and renders its unified diff', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('/git/groups/flowgate.default.0325/diff?path='),
    )
    expect(wrapper.find('.gcd-diff-path').text()).toBe('server/services/git_service.py')

    const lines = wrapper.findAll('.gcd-line')
    expect(lines).toHaveLength(5)
    expect(lines[1].classes()).toContain('gcd-line-del')
    expect(lines[1].text()).toContain('old line')
    expect(lines[2].classes()).toContain('gcd-line-add')
    expect(lines[2].text()).toContain('new line')
    // The gutter carries both sides: an added line has no old line number.
    expect(lines[2].findAll('.gcd-ln')[0].text()).toBe('')
    expect(lines[2].findAll('.gcd-ln')[1].text()).toBe('2')
  })

  it('pairs deletions with additions side by side in split view', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.findAll('.gcd-seg button')[1].trigger('click')
    const rows = wrapper.findAll('.gcd-srow')
    // 2 context rows + 1 paired del/add row + 1 addition-only row.
    expect(rows).toHaveLength(4)
    const paired = rows[1].findAll('.gcd-text')
    expect(paired[0].text()).toBe('old line')
    expect(paired[1].text()).toBe('new line')
    // The unpaired addition leaves the left side blank rather than shifting rows up.
    const unpaired = rows[2].findAll('.gcd-text')
    expect(unpaired[0].classes()).toContain('gcd-line-blank')
    expect(unpaired[1].text()).toBe('extra line')
  })

  it('filters by status and by path search, following the selection', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const chips = wrapper.findAll('.gcd-chip')
    expect(chips.map((chip) => chip.text().replace(/\s+/g, ' '))).toEqual([
      'All 5', 'Modified 3', 'Added 1', 'Deleted 1',
    ])

    await chips[3].trigger('click')       // deleted only
    await flushPromises()
    expect(wrapper.findAll('.gcd-file')).toHaveLength(1)
    // Hiding the open file must move the diff pane to a file the list still offers.
    expect(wrapper.find('.gcd-diff-path').text()).toBe('client/src/main/components/Obsolete.vue')

    await chips[0].trigger('click')
    await wrapper.find('.gcd-search input').setValue('DocInfoPanel')
    await flushPromises()
    expect(wrapper.findAll('.gcd-file')).toHaveLength(1)

    await wrapper.find('.gcd-search input').setValue('zzz-no-such-path')
    await flushPromises()
    expect(wrapper.find('.gcd-nomatch').exists()).toBe(true)
  })

  it('moves between files with previous/next', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    const [prev, next] = wrapper.findAll('.gcd-diff-nav button')
    expect((prev.element as HTMLButtonElement).disabled).toBe(true)   // already at the first file

    await next.trigger('click')
    await flushPromises()
    expect(wrapper.find('.gcd-diff-path').text()).toBe('client/src/main/components/DocInfoPanel.vue')

    await wrapper.findAll('.gcd-diff-nav button')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.gcd-diff-path').text()).toBe('server/services/git_service.py')
  })

  it('flags binary and newly-added files instead of rendering nonsense', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.findAll('.gcd-file')[4].trigger('click')   // assets/logo.png
    await flushPromises()
    expect(wrapper.find('.gcd-diff-state').text()).toContain('binary file')
    expect(wrapper.findAll('.gcd-line')).toHaveLength(0)

    await wrapper.findAll('.gcd-file')[2].trigger('click')   // brand-new spec file
    await flushPromises()
    expect(wrapper.find('.gcd-notice').text()).toContain('newly added file')
    expect(wrapper.findAll('.gcd-line')).toHaveLength(1)
  })

  it('reports a diff failure and can retry it', async () => {
    routeDiff('server/services/git_service.py')
    const wrapper = mountDialog()
    await flushPromises()

    expect(wrapper.find('.gcd-diff-error').text()).toContain('Could not load the diff')
    expect(wrapper.findAll('.gcd-line')).toHaveLength(0)

    routeDiff()
    await wrapper.find('.gcd-retry').trigger('click')
    await flushPromises()
    expect(wrapper.find('.gcd-diff-error').exists()).toBe(false)
    expect(wrapper.findAll('.gcd-line')).toHaveLength(5)
  })

  it('returns to the approval screen without requesting anything else', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    await wrapper.find('.gcd-back').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)

    await wrapper.find('.gcd-close').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(2)

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toHaveLength(3)
  })

  it('says so when the group changed nothing at all', async () => {
    const wrapper = mountDialog([])
    await flushPromises()

    expect(wrapper.find('.gcd-blank').exists()).toBe(true)
    expect(wrapper.find('.gcd-bd').exists()).toBe(false)
    expect(getRequest).not.toHaveBeenCalled()
  })
})
