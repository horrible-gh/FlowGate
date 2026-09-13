/**
 * flowgate.default.0560 T0020 — export the six split-out dialogs' real DOM so the companion
 * harness can measure them under the PRODUCTION stylesheet.
 *
 * Two things jsdom cannot answer, and both are in this T's title:
 *   - the nested pair's z-ORDER. `GitMergeReviewDialog`'s own shell stays legacy until 5순위,
 *     so the child joins the common stack while its parent is a plain `.modal-bg` — whether
 *     the child still paints above it is a question about two stylesheets, not about the DOM.
 *   - the widths. Every one of these instances carried a measured width before the split, and
 *     the common size scale would otherwise quietly resize them.
 *
 * `tests/browser/dialog-nested-split-geometry.0560.mjs` loads what this writes.
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

import AiProviderListEditor from '../../src/settings/components/AiProviderListEditor.vue'
import ConfirmDialog from '@main/components/dialogs/ConfirmDialog.vue'
import GitMergeReviewDialog from '@main/components/GitMergeReviewDialog.vue'
import GitStatusPanelDialog from '@main/components/GitStatusPanelDialog.vue'
import NotificationAiDetailDialog from '@main/components/NotificationAiDetailDialog.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const mountOptions = (props: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  props,
  global: { plugins: [i18n], ...(extra.global as object ?? {}) },
  attachTo: document.body,
})

const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: ['claude'], api: ['claude'] } }
const PROVIDER_ROW = {
  id: 'aip_1', name: 'claude cli', exec_type: 'cli', kind: 'claude',
  enabled: true, cli_command: 'claude -p',
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

/**
 * Mounted components, so `capture()` can unmount them in order. `resetDialogSystem()` zeroes
 * the scroll-lock refcount while still-mounted entries hold it, and the release that arrives
 * with a later unmount then underflows and prints a dev warning — the order below is what
 * keeps the fixture's own teardown quiet and truthful.
 */
const live: ReturnType<typeof mount>[] = []

function mountLive(...args: Parameters<typeof mount>): ReturnType<typeof mount> {
  const wrapper = mount(...args)
  live.push(wrapper)
  return wrapper
}

function overlay(): string {
  const el = document.querySelector('.fg-dialog-overlay')
  expect(el, 'the dialog did not render a common-layer overlay').toBeTruthy()
  return el!.outerHTML
}

it('exports the six split-out dialogs for built-CSS geometry', async () => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/review-diff')) {
      return Promise.resolve({
        data: {
          ok: true,
          result: {
            path: 'server/app/git_service.py',
            status: 'M',
            old: { content: 'a\n', binary: false, truncated: false },
            new: { content: 'b\n', binary: false, truncated: false },
          },
        },
      })
    }
    return Promise.resolve({ data: reviewPayload() })
  })

  const cases: Record<string, string> = {}

  async function capture(name: string, open: () => Promise<void>, whole = false): Promise<void> {
    document.body.innerHTML = ''
    await open()
    cases[name] = whole ? document.body.innerHTML : overlay()
    while (live.length > 0) live.pop()!.unmount()
    resetDialogSystem()
  }

  async function openRowAction(wrapper: ReturnType<typeof mount>, title: string) {
    await wrapper.find(`button[title="${i18n.global.t(title)}"]`).trigger('click')
    await flushPromises()
  }

  const editorProps = { providers: [PROVIDER_ROW], defaultIndex: 0, catalog: CATALOG }

  await capture('ai-provider-form', async () => {
    const wrapper = mountLive(AiProviderListEditor as never, mountOptions(editorProps))
    await openRowAction(wrapper, 'common.edit')
  })

  await capture('ai-provider-command', async () => {
    const wrapper = mountLive(AiProviderListEditor as never, mountOptions(editorProps))
    await openRowAction(wrapper, 'settings.ai.view_command')
  })

  await capture('ai-provider-delete', async () => {
    const wrapper = mountLive(AiProviderListEditor as never, mountOptions(editorProps))
    await openRowAction(wrapper, 'common.delete')
  })

  await capture('git-status-panel', async () => {
    mountLive(GitStatusPanelDialog, mountOptions({ open: true, projectId: 'flowgate' }, {
      global: { stubs: { GitStatusPanel: true } },
    }))
    await flushPromises()
  })

  await capture('notification-ai-detail', async () => {
    mountLive(NotificationAiDetailDialog as never, mountOptions({
      open: true,
      detail: {
        run_id: 'run1', succeeded: true, doc_ref: 'flowgate.default.0560.0020-T',
        doc_title: '4.5순위', stop_code: 'completed', end_reason: null, finished_at: null,
        provider_name: 'Claude Sonnet 5', last_message: 'done', stop_reason: null,
      },
      loading: false,
      errored: false,
      returnFocusTo: null,
    }))
    await flushPromises()
  })

  // The nested pair, captured whole: the legacy parent teleports to <body> and the child
  // teleports to the common host, so only the whole body holds both layers at once.
  await capture('merge-reject-nested', async () => {
    const wrapper = mountLive(GitMergeReviewDialog, mountOptions({
      groupId: 'flowgate.default.0481',
      mergeId: 9,
      branch: 'group/0481',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    }))
    await flushPromises()
    // The review dialog teleports to <body>, so its own footer is not inside `wrapper`.
    document.querySelector<HTMLButtonElement>('.gmr-ft-actions .btn-danger-ol')!.click()
    await flushPromises()
    expect(document.querySelector('[data-dialog-variant="form-actions"]')).toBeTruthy()
  }, true)

  // Three layers: legacy parent -> reject sub-dialog -> the discard confirm T0020 §2.2 (a)
  // puts in front of a typed reason.
  await capture('merge-reject-discard-confirm', async () => {
    mountLive(ConfirmDialog as never, mountOptions({ host: true }))
    const wrapper = mountLive(GitMergeReviewDialog, mountOptions({
      groupId: 'flowgate.default.0481',
      mergeId: 9,
      branch: 'group/0481',
      baseBranch: 'main',
      providers: [{ id: 'p1', name: 'Claude Sonnet 5' }],
      selectedProvider: 'p1',
    }))
    await flushPromises()
    document.querySelector<HTMLButtonElement>('.gmr-ft-actions .btn-danger-ol')!.click()
    await flushPromises()
    const box = document.querySelector('[data-dialog-variant="form-actions"] textarea') as HTMLTextAreaElement
    box.value = '다시 해 주세요'
    box.dispatchEvent(new Event('input'))
    await flushPromises()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(document.querySelector('[data-dialog-variant="confirm-danger"]')).toBeTruthy()
  }, true)

  expect(Object.keys(cases)).toHaveLength(7)

  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, 'dialog-nested-split-geometry.0560.json'), JSON.stringify(cases, null, 2), 'utf8')
})
