// flowgate.default.0481 T0010 rev1 — export the REAL 승인 대기 화면 DOM in its three
// chat states so merge-review-wait.0481.mjs can measure them in headless Chrome with the
// production CSS bundle. Same split as GitConflictResolver.deckParity.0481.fixture.spec.ts:
// the component renders here, the judging happens there.
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
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

type Turn = { turn_id: string; role: 'human' | 'ai'; message: string; provider_id: string | null; status: string; created_at: string }
function turn(id: string, role: 'human' | 'ai', message: string): Turn {
  return { turn_id: id, role, message, provider_id: 'p1', status: 'accepted', created_at: '2026-09-08T00:00:00+09:00' }
}

const HUMAN = turn('h1', 'human', 'ko.ts 쪽은 왜 이렇게 바꿨어?')
const AI = turn('a1', 'ai', 'GitStatusPanel.vue 에서 새로 쓰는 라벨이라 키를 추가했습니다.')

function payload(conversation: Turn[], pending: Record<string, unknown> | null) {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0481', merge_id: 9,
      review_state: 'resolved_pending_review', review_fingerprint: 'fp-1', instruction_generation: 0,
      base_head: 'base1', merge_head: 'merge1', snapshot_tree: 'tree1',
      changes: [{ path: 'server/modules/flow_gate/services/git_service.py', status: 'M', old_path: null }],
      conflict_origins: [], conversation, held_test_operations: [],
      pending_conversation: pending,
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
      group_id: 'flowgate.default.0481', merge_id: 9, path, status: 'M',
      old: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nb\n' },
      new: { exists: true, binary: false, truncated: false, size: 5, content: 'a\nc\n' },
    },
  }
}

async function dump(name: string, conversation: Turn[], pending: Record<string, unknown> | null) {
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/review-diff')) return Promise.resolve({ data: diff('server/modules/flow_gate/services/git_service.py') })
    if (String(url).includes('/review')) return Promise.resolve({ data: payload(conversation, pending) })
    return Promise.reject(new Error('unexpected ' + url))
  })
  const wrapper = mount(GitMergeReviewDialog, {
    props: {
      groupId: 'flowgate.default.0481', mergeId: 9, branch: 'feature/0481', baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }], selectedProvider: 'p1',
    },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
  await flushPromises()
  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, `merge-review.${name}.html`), wrapper.html(), 'utf8')
  wrapper.unmount()
}

it('exports the approval screen in its idle / waiting / answered chat states', async () => {
  i18n.global.locale.value = 'ko'
  await dump('idle', [], null)
  await dump('waiting', [HUMAN], {
    run_id: 'run-1', status: 'running', provider: 'Claude Sonnet 5',
    started_at: null, elapsed_ms: 42_000, write_requested: false, allow_test_edits: false,
  })
  await dump('answered', [HUMAN, AI], null)
  expect(true).toBe(true)
})
