import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

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

  it('starts collapsed, exposes the disabled AI reason, and keeps commit controls inside detail', async () => {
    const wrapper = await render()
    const ai = wrapper.find('button[aria-describedby="git-base-ai-disabled-reason"]')
    expect(ai.attributes('disabled')).toBeDefined()
    expect(wrapper.find('#git-base-ai-disabled-reason').text()).toBe(i18n.global.t('main.git_status.base_ai_disabled_reason'))
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

  it('calls cleanup through the existing endpoint and hides the row at zero', async () => {
    const wrapper = await render()
    await wrapper.find('.git-cleanup-pending button').trigger('click'); await flushPromises()
    expect(postRequest.mock.calls.some(([url]) => String(url).includes('/git/cleanup'))).toBe(true)
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