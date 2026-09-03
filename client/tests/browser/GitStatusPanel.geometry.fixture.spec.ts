import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitStatusPanel from '@main/components/GitStatusPanel.vue'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const GROUP = 'flowgate.default.0482'
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

it('exports the actual v9 default-data DOM for built-CSS geometry and parity checks', async () => {
  setActivePinia(createPinia())
  const conflict = Array.from({ length: 12 }, (_, i) => ({ path: `conflict/${i}.ts`, content: '', conflict_count: 1 }))
  const status = {
    enabled: true, base_branch: 'main', base_path_state: 'checkout', ahead_count: 2, behind_count: 0,
    base_dirty: { dirty: true, files: Array.from({ length: 7 }, (_, i) => `tracked/${i}.ts`) },
    base_untracked: { count: 20, files: Array.from({ length: 20 }, (_, i) => `new/${i}.ts`), truncated: true },
    slots: [slot('-a', 3, 0), slot('-b', 7, 1), slot('-c', 4, 0), slot('-d', 2, 0, true)],
    pending_count: 2, cleanable_count: 1,
    pending: [
      { group_id: `${GROUP}-pending`, branch: 'pending', status: 'awaiting_choice', default_action: 'merge', merge_id: 41 },
      { group_id: `${GROUP}-conflict`, branch: 'conflict', status: 'conflict', default_action: 'merge', merge_id: 42 },
    ],
    // T0011: terminal_cleanup replaced the old cleanable_count-derived compatibility row —
    // the DOM parity fixture must carry the real contract, not the removed legacy shape.
    terminal_cleanup: {
      last_run_at: '2026-08-31T12:00:00+09:00', last_run_status: 'partial', last_cleaned_count: 2,
      pending: [{ group_id: `${GROUP}-cleanup`, reason: 'revert_conflict' }],
    },
    unpushed: { count: 2, commit_count: 2, measured: true, merges: [{
      group_id: `${GROUP}-merged`, merge_commit: 'abc1234', subject: 'merged work', can_unmerge: true,
    }] },
  }
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/conflicts')) return Promise.resolve({ data: { files: conflict } })
    if (url.endsWith('/git/finalize')) return Promise.resolve({ data: { state: { commit_message: { suggested: 'merge work', source: 'auto_title' } } } })
    return Promise.resolve({ data: { status } })
  })
  const wrapper = mount(GitStatusPanel, {
    props: { projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, GitConflictResolverDialog: true } },
  })
  await flushPromises()
  await wrapper.find('button[aria-controls="git-base-dirty-files"]').trigger('click')
  await wrapper.find('.git-v9-conflict-summary button[aria-controls]').trigger('click')
  await flushPromises()
  expect(wrapper.findAll('.git-v9-scroll')).toHaveLength(3)
  expect(wrapper.findAll('.git-status-slot-card')).toHaveLength(4)
  expect(wrapper.findAll('.git-trc-preview')).toHaveLength(4)
  expect(wrapper.findAll('.git-cleanup-pending')).toHaveLength(1)
  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, 'git-status-v9.actual-component.html'), wrapper.html(), 'utf8')
})