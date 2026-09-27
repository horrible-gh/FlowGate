// flowgate.default.0604 T0008 §3.5 / §11.11 (D0005 §6) — the review screen must show a
// resolver's `supersede` declaration: which side was kept, the resolver's reason and every
// replaced line, plus a marker on that file in the file list. A file without a declaration
// shows none of it, and the existing conflict-origin tags stay exactly as they were.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import en from '@shared/i18n/en'
import ja from '@shared/i18n/ja'
import ko from '@shared/i18n/ko'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() }, getRequest, postRequest,
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

const DECLARED = 'server/tests/test_work_plan_0395.py'
const PLAIN = 'server/modules/flow_gate/documents/routers/work_plan.py'
const REASON = 'theirs keeps every ours test and moves wp_version to WP_VERSION_SUPPORTED'

function reviewPayload(withSupersede = true) {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0604', merge_id: 97,
      review_state: 'resolved_pending_review', review_fingerprint: 'fp-1', instruction_generation: 0,
      base_head: 'base1', merge_head: 'merge1', snapshot_tree: 'tree1',
      changes: [
        { path: DECLARED, status: 'M', old_path: null },
        { path: PLAIN, status: 'M', old_path: null },
      ],
      conflict_origins: [
        { path: DECLARED, chunk_id: 'c0', selection: 'theirs', start_line: 10, end_line: 12, range_ambiguous: false },
        { path: PLAIN, chunk_id: 'p0', selection: 'manual', start_line: null, end_line: null, range_ambiguous: true },
      ],
      conflict_supersedes: withSupersede ? [{
        path: DECLARED, side: 'theirs', reason: REASON,
        chunks: [{
          chunk: 0, start_line: 1480, end_line: 2290, kept_side: 'theirs', dropped_side: 'ours',
          preserved_lines: new Array(151).fill('    kept'),
          changed_lines: [
            { line: `    future_raw = '{"wp_version":2}'`, replacement: `    future_raw = '{"wp_version":3}'` },
            { line: '    assert body["wp_version"] == 1', replacement: '    assert body["wp_version"] == wp.WP_VERSION_SUPPORTED' },
            { line: '    (11, "future.json", 2),', replacement: '    (11, "future.json", 3),' },
          ],
        }],
      }] : [],
      conversation: [], held_test_operations: [],
      pending_conversation: null, resolver_provider: 'Claude Sonnet 5',
      auto_authority: false, reconciliation_kind: null, last_error: null,
      can_approve: true, can_reject: true, can_send: true,
    },
  }
}

let payload = reviewPayload()

function mountDialog() {
  return mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0604', mergeId: 97, branch: 'flowgate_default_0599', baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }], selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  payload = reviewPayload()
  getRequest.mockReset()
  postRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/review-diff')) {
      const path = decodeURIComponent(url.split('path=')[1] ?? '')
      return Promise.resolve({
        data: {
          ok: true,
          data: {
            group_id: 'flowgate.default.0604', merge_id: 97, path, status: 'M',
            old: { exists: true, binary: false, truncated: false, size: 2, content: 'a\n' },
            new: { exists: true, binary: false, truncated: false, size: 2, content: 'b\n' },
          },
        },
      })
    }
    if (url.includes('/review')) return Promise.resolve({ data: payload })
    return Promise.reject(new Error('unexpected ' + url))
  })
})

afterEach(() => {
  i18n.global.locale.value = 'ko'
})

async function selectFile(wrapper: ReturnType<typeof mountDialog>, path: string) {
  const name = path.slice(path.lastIndexOf('/') + 1)
  const button = wrapper.findAll('button.gmr-file').find((node) => node.text().includes(name))
  expect(button, `file button ${name}`).toBeTruthy()
  await button!.trigger('click')
  await flushPromises()
}

describe('0604 T0008 — supersede declaration on the review screen', () => {
  it('shows kept side, reason and every replaced line for the declared file', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await selectFile(wrapper, DECLARED)

    const block = wrapper.get('[data-test="gmr-supersede"]')
    expect(block.text()).toContain('상위집합 선언')
    expect(wrapper.get('[data-test="gmr-supersede-side"]').text())
      .toBe('들어오는 쪽이 현재 쪽 변경을 포함한다고 해결자가 선언함')
    expect(wrapper.get('[data-test="gmr-supersede-reason"]').text()).toBe(`사유: ${REASON}`)
    expect(block.text()).toContain('원본 1480–2290행 충돌 청크 · 그대로 남은 줄 151개')
    expect(block.text()).toContain('대체된 현재 쪽 줄 (3)')
    const lines = wrapper.get('[data-test="gmr-supersede-lines"]').findAll('li')
    expect(lines).toHaveLength(3)
    // Indentation is kept verbatim — it is code.
    expect(lines[1].get('.gmr-supersede-old').element.textContent)
      .toBe('-     assert body["wp_version"] == 1')
    expect(lines[1].get('.gmr-supersede-new').element.textContent)
      .toBe('+     assert body["wp_version"] == wp.WP_VERSION_SUPPORTED')
    // Never collapsed: no <details>/toggle wraps the block.
    expect(block.element.closest('details')).toBeNull()

    // The existing conflict-origin tag is unchanged and still rendered above it.
    const tags = wrapper.findAll('.gmr-origin-tag')
    expect(tags).toHaveLength(1)
    expect(tags[0].classes()).toContain('gmr-sel-theirs')
    wrapper.unmount()
  })

  it('marks only the declared file in the file list', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const rows = wrapper.findAll('button.gmr-file')
    const declared = rows.find((node) => node.text().includes('test_work_plan_0395.py'))!
    const plain = rows.find((node) => node.text().includes('work_plan.py') && !node.text().includes('0395'))!
    expect(declared.find('[data-test="gmr-supersede-badge"]').exists()).toBe(true)
    expect(declared.find('[data-test="gmr-supersede-badge"]').text()).toContain('선언')
    expect(plain.find('[data-test="gmr-supersede-badge"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows no declaration block for a file without a declaration', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await selectFile(wrapper, PLAIN)
    expect(wrapper.find('[data-test="gmr-supersede"]').exists()).toBe(false)
    expect(wrapper.findAll('.gmr-origin-tag')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows nothing when the payload has no declarations (or predates 0604)', async () => {
    payload = reviewPayload(false)
    delete (payload.result as Record<string, unknown>).conflict_supersedes
    const wrapper = mountDialog()
    await flushPromises()
    await selectFile(wrapper, DECLARED)
    expect(wrapper.find('[data-test="gmr-supersede"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="gmr-supersede-badge"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([
    ['en', 'The resolver declared that the incoming side contains the current side\'s changes', 'Replaced current-side lines (3)'],
    ['ja', '取り込み側が現在側の変更を含むと解決者が宣言しました', '置き換えられた現在側の行 (3)'],
  ])('%s — the block is translated', async (locale, statement, replaced) => {
    i18n.global.locale.value = locale as 'en' | 'ja'
    const wrapper = mountDialog()
    await flushPromises()
    await selectFile(wrapper, DECLARED)
    expect(wrapper.get('[data-test="gmr-supersede-side"]').text()).toBe(statement)
    expect(wrapper.get('[data-test="gmr-supersede"]').text()).toContain(replaced)
    wrapper.unmount()
  })

  it('ko/en/ja define the same supersede keys', () => {
    const KEYS = [
      'supersede_title', 'supersede_badge', 'supersede_statement.ours', 'supersede_statement.theirs',
      'supersede_reason', 'supersede_chunk', 'supersede_replaced.ours', 'supersede_replaced.theirs',
      'supersede_replaced_none',
    ]
    for (const messages of [ko, en, ja]) {
      const review = (messages as { main: { git_review: Record<string, unknown> } }).main.git_review
      for (const key of KEYS) {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown>)?.[part], review)
        expect(typeof value, key).toBe('string')
        expect((value as string).length, key).toBeGreaterThan(0)
      }
    }
  })
})
