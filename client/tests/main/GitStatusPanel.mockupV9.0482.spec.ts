import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const GROUP = 'flowgate.default.0482'
const conflictPaths = Array.from({ length: 12 }, (_, i) => `conflict/file-${i + 1}.ts`)
const commits = (n: number) => Array.from({ length: n }, (_, i) => ({
  doc_id: `${GROUP}.${String(i + 1).padStart(4, '0')}-TR`, doc_code: `${i + 1}-TR`,
  state: 'live', commit: `abcde${i}`, subject: `commit ${i + 1}`,
  skipped_reason: null, cancel_commit: null, restored: false,
}))
const slot = (suffix: string, live: number, canceled: number, conflict = false) => ({
  group_id: `${GROUP}${suffix}`, branch: `slot-${suffix}`,
  status: conflict ? 'conflict' : (suffix === '-c' ? 'merged' : 'waiting'), merge_id: null,
  tr_commits: {
    live, canceled, no_commit: 0, commits: commits(live + canceled), more: 0,
    reapply_pending: false, last_block: null,
    conflict_session: conflict ? {
      merge_id: 77, kind: 'tr_revert', doc_id: `${GROUP}.0006-TR`, doc_code: '0006-TR',
      files: ['a.ts', 'b.ts', 'c.ts'], remaining: ['a.ts'], review_state: 'open',
    } : null,
  },
})

function status(cleanable = 1) {
  return {
    enabled: true, base_branch: 'main', base_path_state: 'checkout', ahead_count: 2, behind_count: 0,
    base_dirty: { dirty: true, files: Array.from({ length: 7 }, (_, i) => `tracked/${i}.ts`) },
    base_untracked: { count: 20, files: Array.from({ length: 20 }, (_, i) => `new/${i}.ts`), truncated: true },
    slots: [slot('-a', 3, 0), slot('-b', 7, 1), slot('-c', 4, 0), slot('-d', 2, 0, true)],
    pending: [
      { group_id: `${GROUP}-pending`, branch: 'pending', status: 'awaiting_choice', default_action: 'merge', merge_id: 10 },
      { group_id: `${GROUP}-conflict`, branch: 'conflict', status: 'conflict', default_action: 'merge', merge_id: 11 },
    ],
    pending_count: 2, cleanable_count: cleanable,
    terminal_cleanup: {
      last_run_at: '2026-08-31T12:00:00+09:00', last_run_status: 'ok',
      last_cleaned_count: 3,
      pending: cleanable ? [{ group_id: `${GROUP}-cleanup`, reason: 'revert_conflict' }] : [],
    },
    unpushed: { count: 2, commit_count: 2, merges: [{ group_id: `${GROUP}-merged`, merge_commit: 'abc1234', subject: 'merged work', can_unmerge: true }], measured: true },
  }
}

async function render(value: any = status()) {
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/conflicts')) return Promise.resolve({ data: { ok: true, files: conflictPaths.map((path) => ({ path, content: '', conflict_count: 1 })) } })
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { state: { commit_message: { suggested: 'merge work', source: 'auto_title' } } } })
    return Promise.resolve({ data: { ok: true, status: value } })
  })
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia()); i18n.global.locale.value = 'ko'
  getRequest.mockReset(); postRequest.mockReset(); postRequest.mockResolvedValue({ data: { ok: true, result: {} } })
  vi.spyOn(window, 'confirm').mockReturnValue(true)
})

describe('GitStatusPanel mockup v9 rendered contract (0482 T#1)', () => {
  it('renders the exact default data, valid localized statuses, and required sections', async () => {
    const wrapper = await render()
    expect(wrapper.text()).toContain(i18n.global.t('main.git_status.base_dirty_summary', { n: 7 }))
    expect(wrapper.findAll('.git-base-untracked-row')).toHaveLength(20)
    expect(wrapper.find('.git-base-untracked__more').text()).toContain('20')
    expect(wrapper.findAll('.git-status-row')).toHaveLength(2)
    expect(wrapper.text()).toContain(i18n.global.t('main.git_status.conflict_files_summary', { n: 12 }))
    expect(wrapper.findAll('.git-status-slot-card')).toHaveLength(4)
    expect(wrapper.text()).toContain(i18n.global.t('main.git_finalize.status.awaiting_choice'))
    expect(wrapper.text()).toContain(i18n.global.t('main.git_finalize.status.waiting'))
    expect(wrapper.text()).toContain(i18n.global.t('main.git_finalize.status.merged'))
    expect(wrapper.text()).not.toContain('main.git_finalize.status.')
  })

  it('starts collapsed, executes base-dirty AI with provider, and keeps commit controls inside detail', async () => {
    const wrapper = await render()
    const ai = wrapper.find('button[aria-describedby="git-base-ai-reason"]')
    expect(ai.attributes('disabled')).toBeUndefined()
    await ai.trigger('click'); await flushPromises()
    const start = postRequest.mock.calls.find(([url]) => String(url).endsWith('/ai-invoke/start'))
    expect(start?.[1]).toMatchObject({ project: 'flowgate', module: 'none', action_scope: 'resolve_base_dirty', mode: 'single' })
    expect(wrapper.find('#git-base-dirty-files').exists()).toBe(false)
    await wrapper.find('button[aria-controls="git-base-dirty-files"]').trigger('click')
    expect(wrapper.findAll('#git-base-dirty-files .git-base-dirty-filerow')).toHaveLength(7)
    expect(wrapper.find('#git-base-dirty-files .git-base-commit-row').exists()).toBe(true)
  })

  it('expands twelve fetched conflict files and invokes existing conflict AI request', async () => {
    const wrapper = await render()
    const summary = wrapper.find('.git-v9-conflict-summary')
    await summary.find('button[aria-controls]').trigger('click')
    expect(summary.findAll('.git-v9-file')).toHaveLength(12)
    await summary.findAll('button')[0].trigger('click'); await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/ai-invoke/'))).toBe(true)
  })

  it('derives preview+more=badge rows from every slot DOM and keeps conflict inside its card', async () => {
    const cards = (await render()).findAll('.git-status-slot-card')
    const expected = [[3, 0], [7, 1], [4, 0], [2, 0]]
    expect(cards).toHaveLength(expected.length)
    cards.forEach((card, index) => {
      const badgeNumbers = (card.find('.git-trc-badge').text().match(/\d+/g) || []).map(Number)
      expect(badgeNumbers.slice(0, 2)).toEqual(expected[index])
      const preview = card.findAll('.git-trc-preview-row').length
      const moreText = card.find('.git-trc-preview-more').exists() ? card.find('.git-trc-preview-more').text() : ''
      const more = Number(moreText.match(/\d+/)?.[0] || 0)
      expect(preview + more).toBe(badgeNumbers[0] + badgeNumbers[1])
    })
    expect(cards[3].find('.git-trc-conflict').exists()).toBe(true)
    expect(cards[3].element.contains(cards[3].find('.git-trc-conflict').element)).toBe(true)
  })

  it('keeps preview and full-list remaining labels semantically separate', async () => {
    const wrapper = await render()
    expect(wrapper.find('.git-trc-preview-more').text()).toContain(i18n.global.t('main.git_status.tr_commits.preview_more', { n: 5 }))
    const value = status(); value.slots[1].tr_commits.more = 3
    const withServerRemainder = await render(value)
    await withServerRemainder.findAll('.git-trc-badge')[1].trigger('click')
    expect(withServerRemainder.find('.git-trc-more').text()).toBe(i18n.global.t('main.git_status.tr_commits.more', { n: 3 }))
  })

  it('calls cleanup exactly once automatically and hides rows when no residual remains', async () => {
    const wrapper = await render()
    expect(wrapper.find('.git-cleanup-pending').exists()).toBe(true)
    expect(postRequest.mock.calls.filter(([url]) => String(url).includes('/git/cleanup'))).toHaveLength(1)
    expect((await render(status(0))).find('.git-cleanup-pending').exists()).toBe(false)
  })

  it('covers unpushed unmerge and the normal pending commit-title/execute entry points', async () => {
    const wrapper = await render()
    expect(wrapper.find('.git-unpushed-row').exists()).toBe(true)
    await wrapper.find('.git-unpushed-row button').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/git/unmerge'))).toBe(true)
    const normal = wrapper.findAll('.git-status-row')[0]
    expect(normal.find('.git-status-commit .git-commit-msg-label').text()).toBe(i18n.global.t('main.git_finalize.commit_message_label'))
    await normal.find('.git-status-row-main .btn-primary').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => String(url).endsWith('/git/finalize'))).toBe(true)
  })

  it('shows merge-now inside base detail after a parked merge has no tracked files', async () => {
    const value: any = status()
    postRequest.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/git/finalize')) {
        value.base_dirty = { dirty: false, files: [] }
        return { data: { ok: false, error: { code: 'base_dirty', details: { files: [] } } } }
      }
      return { data: { ok: true, result: {} } }
    })
    const wrapper = await render(value)
    await wrapper.findAll('.git-status-row')[0].find('.git-status-row-main .btn-primary').trigger('click')
    await flushPromises()
    await wrapper.find('button[aria-controls="git-base-dirty-files"]').trigger('click')
    expect(wrapper.find('#git-base-dirty-files .git-base-commit-row button').text()).toContain(i18n.global.t('main.git_status.base_merge_now_btn'))
  })

  it('renders the R-A reservation as a non-interactive region', async () => {
    const placeholder = (await render()).find('.git-ra-placeholder')
    expect(placeholder.exists()).toBe(true)
    expect(placeholder.findAll('button, input, select, a')).toHaveLength(0)
  })
})

describe('terminal_cleanup relative time and status rendering (0482 T0011 item 4)', () => {
  const NOW = new Date('2026-08-31T12:00:00+09:00').getTime()

  beforeEach(() => {
    vi.spyOn(Date, 'now').mockReturnValue(NOW)
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  function withCleanup(overrides: Partial<{ last_run_at: string | null; last_run_status: string | null; last_cleaned_count: number; pending: any[] }>) {
    const value = status(0)
    value.terminal_cleanup = { last_run_at: null, last_run_status: null, last_cleaned_count: 0, pending: [], ...overrides }
    return value
  }

  it('shows "never run" when there is no history', async () => {
    const wrapper = await render(withCleanup({}))
    expect(wrapper.find('.git-cleanup-never').text()).toBe(i18n.global.t('main.git_status.cleanup_never_run'))
  })

  it.each([
    [0, 'cleanup_just_now', {}],
    [59, 'cleanup_just_now', {}],
    [60, 'cleanup_minutes_ago', { n: 1 }],
    [3599, 'cleanup_minutes_ago', { n: 59 }],
    [3600, 'cleanup_hours_ago', { n: 1 }],
    [86399, 'cleanup_hours_ago', { n: 23 }],
    [86400, 'cleanup_days_ago', { n: 1 }],
    [-30, 'cleanup_just_now', {}],   // a future timestamp clamps to 0 elapsed seconds
  ])('elapsed=%dsec renders %s', async (elapsedSec, key, params) => {
    const lastRunAt = new Date(NOW - elapsedSec * 1000).toISOString()
    const wrapper = await render(withCleanup({ last_run_at: lastRunAt, last_run_status: 'ok', last_cleaned_count: 3 }))
    expect(wrapper.find('.git-cleanup-never').text()).toBe(
      i18n.global.t('main.git_status.cleanup_status_ok', { when: i18n.global.t(`main.git_status.${key}`, params), n: 3 }),
    )
  })

  it('distinguishes ok / partial / failed status labels', async () => {
    const lastRunAt = new Date(NOW - 120_000).toISOString()
    for (const st of ['ok', 'partial', 'failed'] as const) {
      const wrapper = await render(withCleanup({ last_run_at: lastRunAt, last_run_status: st, last_cleaned_count: 2 }))
      expect(wrapper.find('.git-cleanup-never').text()).toBe(
        i18n.global.t(`main.git_status.cleanup_status_${st}`, { when: i18n.global.t('main.git_status.cleanup_minutes_ago', { n: 2 }), n: 2 }),
      )
    }
  })

  it('renders revert_conflict and teardown_failed pending rows with distinct reasons and a working retry', async () => {
    const wrapper = await render(withCleanup({
      last_run_at: new Date(NOW - 60_000).toISOString(), last_run_status: 'partial', last_cleaned_count: 1,
      pending: [
        { group_id: `${GROUP}-x1`, reason: 'revert_conflict' },
        { group_id: `${GROUP}-x2`, reason: 'teardown_failed' },
      ],
    }))
    const rows = wrapper.findAll('.git-cleanup-pending')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain(i18n.global.t('main.git_status.cleanup_reason_revert_conflict'))
    expect(rows[1].text()).toContain(i18n.global.t('main.git_status.cleanup_reason_teardown_failed'))
    expect(rows[0].text()).not.toBe(rows[1].text())
    postRequest.mockClear()
    await rows[1].find('button').trigger('click')
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/git/cleanup'))).toBe(true)
  })

  it('shows zero pending rows and only the status line when nothing remains', async () => {
    const wrapper = await render(withCleanup({ last_run_at: new Date(NOW - 60_000).toISOString(), last_run_status: 'ok', last_cleaned_count: 3, pending: [] }))
    expect(wrapper.findAll('.git-cleanup-pending')).toHaveLength(0)
    expect(wrapper.find('.git-cleanup-never').exists()).toBe(true)
  })
})

describe('base-dirty AI button lifecycle (0482 T0011 item 8)', () => {
  it('re-enables the button and refreshes status once the tracked run finishes', async () => {
    postRequest.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/ai-invoke/start')) return { data: { ok: true, run_id: 'aiv_1' } }
      return { data: { ok: true, result: {} } }
    })
    const wrapper = await render()
    const store = useAiInvokeRunsStore()
    await wrapper.find('button[aria-describedby="git-base-ai-reason"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('button[aria-describedby="git-base-ai-reason"]').attributes('disabled')).toBeDefined()

    getRequest.mockClear()
    store.trackFinished({ run_id: 'aiv_1', project_id: 'flowgate', action_scope: 'resolve_base_dirty', status: 'finished' })
    await flushPromises()

    expect(wrapper.find('button[aria-describedby="git-base-ai-reason"]').attributes('disabled')).toBeUndefined()
    expect(getRequest.mock.calls.some(([url]) => String(url).includes('/git/status'))).toBe(true)
  })

  it('surfaces base_dirty_run_in_progress as a running state and base_dirty_empty as a refreshed empty state', async () => {
    postRequest.mockImplementation(async (url: string) => {
      if (String(url).endsWith('/ai-invoke/start')) {
        const err: any = new Error('conflict')
        err.response = { data: { code: 'base_dirty_run_in_progress', run_id: 'aiv_existing' } }
        throw err
      }
      return { data: { ok: true, result: {} } }
    })
    const wrapper = await render()
    await wrapper.find('button[aria-describedby="git-base-ai-reason"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('button[aria-describedby="git-base-ai-reason"]').attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain(i18n.global.t('main.git_status.base_ai_running'))
  })
})

// 0482 R0001 rev2 — "너희들이 하겠다고 한 시안이랑 전혀 다르잖아".
// 앞선 라운드는 요소의 "개수"만 맞췄지 시안(deck 4543n0ab v9)이 그린 모양은 옮기지
// 않았다. 아래는 그 모양 자체를 계약으로 못박는다: 붉은 요약 카드(.summary-chip),
// 채움형 AI 버튼 + 링크형 펼침 토글, 파선으로 갈라진 펼침 영역, 붉은 표식이 붙은 충돌
// 목록, 자동 정리 카드, 줄무늬 예약 자리.
describe('deck 4543n0ab v9 shape parity (0482 R0001 rev2)', () => {
  it('draws base-dirty as the deck summary chip: icon + title + sub line + filled AI + link toggle', async () => {
    const wrapper = await render()
    const summary = wrapper.find('.git-v9-summary')
    expect(summary.find('.git-v9-chip-icon').exists()).toBe(true)
    expect(summary.find('.git-v9-chip').text()).toContain(i18n.global.t('main.git_status.base_dirty_summary', { n: 7 }))
    expect(summary.find('.git-v9-chip-sub').text()).toBe(i18n.global.t('main.git_status.base_dirty_guide'))
    // 시안의 오른쪽 두 컨트롤: 채움형 [AI에게 맡기기] 와 텍스트 링크 [파일별로 보기 ▾].
    expect(summary.find('button[aria-describedby="git-base-ai-reason"]').classes()).toContain('btn-primary')
    const toggle = summary.find('button[aria-controls="git-base-dirty-files"]')
    expect(toggle.classes()).toContain('git-v9-link-btn')
    expect(toggle.classes()).not.toContain('btn-secondary')
    expect(toggle.text()).toContain(i18n.global.t('main.git_status.view_files'))
    // 시안에 없는 별도 안내 문장 줄은 남아 있지 않다.
    expect(wrapper.find('.git-v9-disabled-reason').exists()).toBe(false)
  })

  it('keeps the blocked-merge note and the commit row above the file rows inside the disclosure', async () => {
    const wrapper = await render()
    await wrapper.find('button[aria-controls="git-base-dirty-files"]').trigger('click')
    const detail = wrapper.find('#git-base-dirty-files')
    expect(detail.classes()).toContain('git-v9-disclosure')
    const order = [...detail.element.querySelectorAll('.git-base-dirty-alert__msg, .git-base-commit-row, .git-base-dirty-filerow')]
      .map((element) => element.className.split(' ').find((name) => name.startsWith('git-base')))
    expect(order[0]).toBe('git-base-dirty-alert__msg')
    expect(order[1]).toBe('git-base-commit-row')
    expect(order[2]).toBe('git-base-dirty-filerow')
    // 접힌 기본 화면에는 이 안내가 보이지 않는다(시안: 요약 한 장만).
    const collapsed = await render()
    expect(collapsed.find('.git-base-dirty-alert__msg').exists()).toBe(false)
  })

  it('draws the conflict summary as the same chip and marks every expanded file row', async () => {
    const wrapper = await render()
    const summary = wrapper.find('.git-v9-conflict-summary')
    expect(summary.find('.git-v9-chip-icon').exists()).toBe(true)
    expect(summary.find('.git-v9-chip-sub').text()).toBe(i18n.global.t('main.git_status.conflict_files_guide'))
    expect(summary.findAll('button')[0].classes()).toContain('btn-primary')
    const toggle = summary.find('button[aria-controls]')
    expect(toggle.classes()).toContain('git-v9-link-btn')
    expect(toggle.text()).toContain(i18n.global.t('main.git_status.expand'))
    await toggle.trigger('click')
    const files = summary.findAll('.git-v9-file')
    expect(files).toHaveLength(12)
    files.forEach((file) => expect(file.find('.git-v9-file-mark').text()).toBe('✕'))
    expect(summary.find('.git-v9-conflict-list').exists()).toBe(true)
  })

  it('uses the compact deck badge and pushes the status badge to the right of the slot header', async () => {
    const card = (await render()).findAll('.git-status-slot-card')[1]
    expect(card.find('.git-trc-badge').text().trim()).toBe(
      i18n.global.t('main.git_status.tr_commits.badge', { live: 7, canceled: 1 }),
    )
    expect(card.find('.git-trc-badge').attributes('title')).toBe(
      i18n.global.t('main.git_status.tr_commits.badge_title', { live: 7, canceled: 1 }),
    )
    const header = [...card.find('.git-status-slot').element.children].map((element) => element.className)
    expect(header[header.length - 2]).toContain('git-status-spacer')
    expect(header[header.length - 1]).toContain('badge')
  })

  it('renders terminal cleanup as a card with a residual list, and the R-A slot as a striped reservation', async () => {
    const wrapper = await render()
    const card = wrapper.find('.git-cleanup-card')
    expect(card.exists()).toBe(true)
    expect(card.find('.git-cleanup-card__icon').exists()).toBe(true)
    expect(card.find('.git-cleanup-never').text()).toBe(wrapper.find('.git-cleanup-never').text())
    const residual = wrapper.find('.git-cleanup-remainder .git-cleanup-pending')
    expect(residual.find('.git-cleanup-pending__icon').exists()).toBe(true)
    expect(residual.find('.git-status-spacer').exists()).toBe(true)
    expect(residual.find('button').text()).toContain(i18n.global.t('main.git_status.cleanup_retry'))
    // 잔여가 없으면 카드만 남고 목록 자체가 사라진다.
    expect((await render(status(0))).find('.git-cleanup-remainder').exists()).toBe(false)
    expect(wrapper.find('.git-status-sect > .git-ra-placeholder').exists()).toBe(true)
  })

  it('makes the revert-conflict note a card that never hides inside the commit fold', async () => {
    const card = (await render()).findAll('.git-status-slot-card')[3]
    const conflict = card.find('.git-trc-conflict')
    expect(conflict.exists()).toBe(true)
    expect(card.find('.git-trc-list').exists()).toBe(false)
    expect(conflict.findAll('button')).toHaveLength(2)
  })
})
