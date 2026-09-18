/**
 * flowgate.default.0560 T0022 — export the eight migrated instances' real DOM so the companion
 * harness can measure them under the PRODUCTION stylesheet.
 *
 * jsdom cannot answer the two questions this step's §4 asks in pixels:
 *   - the WIDTH each instance ends up with. Five of the eight had a width the common size
 *     scale does not carry exactly, and two of them ask for a `size` that CHANGES with state
 *     (`AiInvokeDialog` while the review loop is on, `WorkPlanProposalDialog` with no provider
 *     section). An override or a computed size that silently fails to apply is exactly the
 *     regression a DOM-shape test cannot see.
 *   - the left-to-right ORDER of the footer buttons. `DialogFooter` sorts by role, but what a
 *     user meets is the painted row — so the order is read off the buttons' coordinates.
 *
 * `tests/browser/dialog-complex-geometry.0560.mjs` loads what this writes.
 */
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, getRequest, postRequest }
})
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import GitUntrackedConflictDialog from '@main/components/GitUntrackedConflictDialog.vue'
import GroupChangesDialog from '@main/components/GroupChangesDialog.vue'
import NextActionModal from '@main/components/NextActionModal.vue'
import WorkPlanCreateDialog from '@main/components/WorkPlanCreateDialog.vue'
import WorkPlanProposalDialog from '@main/components/WorkPlanProposalDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const PROVIDERS = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'Codex', exec_type: 'cli', kind: 'codex', enabled: true },
]

const SEQUENCE = {
  doc_id: 'flowgate.default.0560.0001-B',
  doc_class: 'B',
  decided: true,
  items: [
    { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
    { id: 2, item_seq: 2, type: 'TR', label: '작업레포트', status: 'pending' },
  ],
  head: { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
}

function reviewPayload() {
  return {
    ok: true,
    result: {
      group_id: 'flowgate.default.0481',
      merge_id: 9,
      review_state: 'resolved_pending_review',
      review_fingerprint: 'fp-1',
      instruction_generation: 0,
      base_head: 'base1',
      merge_head: 'merge1',
      changes: [{ path: 'server/app/git_service.py', status: 'M', old_path: null }],
      conflict_origins: [],
      conversation: [],
      held_test_operations: [],
      pending_conversation: null,
      resolver_provider: 'Claude Sonnet 5',
      reconciliation_kind: null,
      last_error: null,
      can_approve: true,
      can_reject: true,
      can_send: true,
    },
  }
}

function routeGet(url: string): Promise<unknown> {
  const path = String(url)
  if (path.includes('/review-diff') || (path.includes('/git/groups/') && path.includes('/diff?'))) {
    const middle = Array.from({ length: 20 }, (_, i) => `shared-${i + 1}`).join('\n')
    const result = {
      path: 'server/app/git_service.py',
      status: 'M',
      old: { content: `old-first\n${middle}\nold-last\n`, binary: false, truncated: false },
      new: { content: `new-first\n${middle}\nnew-last\n`, binary: false, truncated: false },
    }
    return path.includes('/review-diff')
      ? Promise.resolve({ data: { ok: true, data: result } })
      : Promise.resolve({ data: { data: result } })
  }
  if (path.includes('/git/merge/')) return Promise.resolve({ data: reviewPayload() })
  if (path.includes('/document-types')) {
    return Promise.resolve({ data: { data: [], work_plan_countable_types: [] } })
  }
  if (path.includes('/ai-invoke/providers')) {
    return Promise.resolve({
      data: { ok: true, project: 'flowgate', providers: PROVIDERS, default_provider_id: 'p1' },
    })
  }
  if (path === '/api/v1/modules') return Promise.resolve({ data: { items: [] } })
  if (/\/groups$/.test(path)) {
    return Promise.resolve({ data: { ok: true, total: 0, offset: 0, limit: 100, items: [] } })
  }
  if (/\/documents$/.test(path)) return Promise.resolve({ data: { items: [] } })
  if (/\/predecessors$/.test(path)) return Promise.resolve({ data: { predecessor_doc_ids: [] } })
  if (path.includes('/workflow/sequence')) return Promise.resolve({ data: SEQUENCE })
  return Promise.resolve({ data: {} })
}

const live: ReturnType<typeof mount>[] = []

function mountLive(...args: Parameters<typeof mount>): ReturnType<typeof mount> {
  const wrapper = mount(...args)
  live.push(wrapper)
  return wrapper
}

const options = (props: Record<string, unknown>) => ({
  props,
  global: { plugins: [i18n] },
  attachTo: document.body,
})

it('exports the eight migrated instances for built-CSS geometry', async () => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockImplementation(routeGet)
  postRequest.mockResolvedValue({ data: { ok: true } })

  const cases: Record<string, string> = {}

  async function capture(name: string, open: () => Promise<void>): Promise<void> {
    document.body.innerHTML = ''
    await open()
    const el = document.querySelector('.fg-dialog-overlay')
    expect(el, `${name} did not render a common-layer overlay`).toBeTruthy()
    cases[name] = document.body.innerHTML
    while (live.length > 0) live.pop()!.unmount()
    resetDialogSystem()
  }

  const aiInvokeProps = {
    visible: true,
    project: 'flowgate',
    module: 'default',
    group: '0560',
    docRef: 'flowgate.default.0560.0001-R',
    sequenceDocRef: 'flowgate.default.0560.0001-R',
    continuationInstructionMode: 'auto_approved',
    actionScope: 'edit',
  }

  await capture('ai-invoke', async () => {
    mountLive(AiInvokeDialog as never, options(aiInvokeProps))
    await flushPromises()
  })

  await capture('ai-invoke-loop', async () => {
    mountLive(AiInvokeDialog as never, options({ ...aiInvokeProps, actionScope: 'review' }))
    await flushPromises()
    const loop = document.querySelector<HTMLInputElement>('input[type="radio"][value="loop"]')
    expect(loop, 'the review loop radio is missing').toBeTruthy()
    loop!.checked = true
    loop!.dispatchEvent(new Event('change'))
    await flushPromises()
  })

  await capture('continuous-work', async () => {
    mountLive(ContinuousWorkDialog as never, options({
      visible: true, docRef: 'flowgate.default.0560.0001-B',
    }))
    await flushPromises()
  })

  await capture('work-plan-create', async () => {
    mountLive(WorkPlanCreateDialog as never, options({
      visible: true,
      parentDocId: 'flowgate.default.0560.0001-R',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0560',
    }))
    await flushPromises()
  })

  await capture('work-plan-proposal', async () => {
    mountLive(WorkPlanProposalDialog as never, options({
      visible: true,
      parentDocId: 'flowgate.default.0560.0001-R',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0560',
    }))
    await flushPromises()
    await flushPromises()
  })

  await capture('next-action', async () => {
    mountLive(NextActionModal as never, options({
      visible: true,
      nextStepLabel: 'TR',
      nextTypeCode: 'TR',
      projectId: 'flowgate',
      docModule: 'default',
      groupId: 'flowgate.default.0560',
    }))
    await flushPromises()
    await flushPromises()
  })

  await capture('git-untracked-conflict', async () => {
    const wrapper = mountLive(GitUntrackedConflictDialog as never, options({}))
    void (wrapper.vm as never as {
      resolve: (id: string, files: string[], scope?: string, groups?: unknown) => Promise<string>
    }).resolve('flowgate.default.0560', ['blocked.txt'], 'group', {
      untrackedFiles: ['blocked.txt'], trackedFiles: ['tracked.txt'],
    })
    await flushPromises()
  })

  await capture('group-changes', async () => {
    mountLive(GroupChangesDialog as never, options({
      projectId: 'flowgate',
      groupId: 'flowgate.default.0560',
      branch: 'flowgate_default_0560',
      baseBranch: 'main',
      changes: [{ path: 'a.py', status: 'M', insertions: 1, deletions: 0 }],
    }))
    await flushPromises()
  })

  await capture('git-merge-review', async () => {
    mountLive(GitMergeReviewDialog as never, options({
      groupId: 'flowgate.default.0481',
      mergeId: 9,
      branch: 'group/0481',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    }))
    await flushPromises()
    await flushPromises()
    await new Promise((resolve) => setTimeout(resolve, 0))
    await flushPromises()
  })

  expect(Object.keys(cases)).toHaveLength(9)

  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, 'dialog-complex-geometry.0560.json'), JSON.stringify(cases, null, 2), 'utf8')
})
