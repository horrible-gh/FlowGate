// flowgate.default.0604 T0008 §3.5 (D0005 §6 / 완료 조건 6) — export the REAL 승인 대기
// 화면 DOM with a `supersede` declaration so merge-review-supersede.0604.mjs can measure
// it in headless Chrome under the production CSS bundle. Same split as
// GitMergeReview.waitInPlace.0481.fixture.spec.ts: the component renders here, the
// judging happens there.
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const DECLARED = 'server/tests/test_work_plan_0395.py'
const PLAIN = 'server/modules/flow_gate/documents/routers/work_plan.py'

const SUPERSEDE = {
  path: DECLARED,
  side: 'theirs',
  reason: 'theirs(0599)가 ours(main)의 0597 회귀 시험 166줄을 그대로 품고, 병합 결과의 '
    + 'WP_VERSION_SUPPORTED=2 기준으로 wp_version 기대값 3줄만 바꿨다.',
  chunks: [{
    chunk: 0, start_line: 1480, end_line: 2290, kept_side: 'theirs', dropped_side: 'ours',
    preserved_lines: new Array(151).fill('    kept'),
    changed_lines: [
      { line: `    future_raw = '{"wp_version":2,"binding":"advisory"}\\n'`,
        replacement: `    future_raw = '{"wp_version":3,"binding":"advisory"}\\n'` },
      { line: '        assert restored.json()["body"]["wp_version"] == 1',
        replacement: '        assert restored.json()["body"]["wp_version"] == wp.WP_VERSION_SUPPORTED' },
      { line: `        (11, bad_dir / "future.json", '{"wp_version":2}\\n', "wp_version_unsupported"),`,
        replacement: `        (11, bad_dir / "future.json", '{"wp_version":3}\\n', "wp_version_unsupported"),` },
    ],
  }],
}

function payload() {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0599', merge_id: 97,
      review_state: 'resolved_pending_review', review_fingerprint: 'fp-1', instruction_generation: 0,
      base_head: 'base1', merge_head: 'merge1', snapshot_tree: 'tree1',
      changes: [
        { path: DECLARED, status: 'M', old_path: null },
        { path: PLAIN, status: 'M', old_path: null },
      ],
      conflict_origins: [
        { path: DECLARED, chunk_id: 'c0', selection: 'theirs', start_line: 1480, end_line: 2120, range_ambiguous: false },
        { path: PLAIN, chunk_id: 'p0', selection: 'manual', start_line: null, end_line: null, range_ambiguous: true },
        { path: PLAIN, chunk_id: 'p3', selection: 'theirs', start_line: 40, end_line: 42, range_ambiguous: false },
      ],
      conflict_supersedes: [SUPERSEDE],
      conversation: [], held_test_operations: [], pending_conversation: null,
      resolver_provider: 'Claude Sonnet 5', auto_authority: false,
      reconciliation_kind: null, last_error: null,
      can_approve: true, can_reject: true, can_send: true,
    },
  }
}

function diff(path: string) {
  return {
    ok: true,
    data: {
      group_id: 'flowgate.default.0599', merge_id: 97, path, status: 'M',
      old: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nb\n' },
      new: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nc\n' },
    },
  }
}

it('exports the approval screen with a declared file and a plain file selected', async () => {
  i18n.global.locale.value = 'ko'
  getRequest.mockImplementation((url: string, params?: { path?: string }) => {
    if (String(url).includes('/review-diff')) return Promise.resolve({ data: diff(params?.path ?? DECLARED) })
    if (String(url).includes('/review')) return Promise.resolve({ data: payload() })
    return Promise.reject(new Error('unexpected ' + url))
  })
  const wrapper = mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0599', mergeId: 97, branch: 'flowgate_default_0599', baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }], selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
  await flushPromises()
  const out = process.env.FLOWGATE_SCRATCH || mkdtempSync(join(tmpdir(), 'fg-supersede-0604-'))

  await wrapper.findAll('button.gmr-file')[0].trigger('click')
  await flushPromises()
  expect(wrapper.find('[data-test="gmr-supersede"]').exists()).toBe(true)
  writeFileSync(resolve(out, 'merge-review-supersede.declared.html'), wrapper.html(), 'utf8')

  await wrapper.findAll('button.gmr-file')[1].trigger('click')
  await flushPromises()
  expect(wrapper.find('[data-test="gmr-supersede"]').exists()).toBe(false)
  writeFileSync(resolve(out, 'merge-review-supersede.plain.html'), wrapper.html(), 'utf8')
  wrapper.unmount()
})
