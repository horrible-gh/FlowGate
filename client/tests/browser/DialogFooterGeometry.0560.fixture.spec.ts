/**
 * flowgate.default.0560 T0016 §4-3 — export the five migrated dialogs' real DOM so the
 * companion harness can measure their footers under the PRODUCTION stylesheet.
 *
 * jsdom answers "which element comes first in the DOM"; it does not answer "which button
 * is painted left of which", and `.fg-dialog-footer__actions` is a `justify-content:
 * flex-end` flex row whose visual order is CSS's to change. This fixture is the export
 * half; `tests/browser/dialog-footer-geometry.0560.mjs` loads it into headless Chrome.
 */
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, getRequest, postRequest, patchRequest }
})
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('@main/utils/clipboard', () => ({
  ClipboardAbort: class ClipboardAbort extends Error {},
  copyToClipboardDeferred: async () => true,
  consumeLastFailedCopyText: () => null,
}))
vi.mock('@main/composables/useFlowGateToken', async () => {
  const { ref } = await import('vue')
  return {
    useFlowGateToken: () => ({ requestSequenceEdit: vi.fn(), composeMention: () => 'M', issuing: ref(false) }),
  }
})

import ConfirmModal from '@main/components/ConfirmModal.vue'
import GitBaseDirtyDialog from '@main/components/GitBaseDirtyDialog.vue'
import GroupDiscardModal from '@main/components/GroupDiscardModal.vue'
import ReviewRejectDialog from '@main/components/ReviewRejectDialog.vue'
import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const mountOptions = (props: Record<string, unknown>) => ({
  props,
  global: { plugins: [i18n] },
  attachTo: document.body,
})

function exportOverlay(): string {
  const overlay = document.querySelector('.fg-dialog-overlay')
  expect(overlay, 'the dialog did not render a common-layer overlay').toBeTruthy()
  return overlay!.outerHTML
}

it('exports the five migrated dialogs for built-CSS footer geometry', async () => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/ai-invoke/providers')) {
      return Promise.resolve({
        data: {
          ok: true,
          project: 'flowgate',
          providers: [{ id: 'claude', name: 'Claude', exec_type: 'cli', kind: 'claude' }],
          default_provider_id: 'claude',
        },
      })
    }
    return Promise.resolve({
      data: {
        items: [
          { type: 'N', label: 'investigate', status: 'done', note: '', note_source: null, provider_id: null },
          { type: 'T', label: 'implement', status: 'pending', note: '', note_source: null, provider_id: null },
        ],
      },
    })
  })

  const cases: Record<string, string> = {}

  async function capture(name: string, open: () => Promise<void>): Promise<void> {
    document.body.innerHTML = ''
    await open()
    cases[name] = exportOverlay()
    resetDialogSystem()
  }

  await capture('confirm-modal', async () => {
    mount(ConfirmModal, mountOptions({ visible: true, title: '삭제할까요', message: '되돌릴 수 없습니다' }))
    await flushPromises()
  })

  await capture('group-discard-modal', async () => {
    mount(GroupDiscardModal, mountOptions({
      visible: true,
      groupTitle: '0560',
      documents: [{ id: '1', typeCode: 'T', shortId: 'T0016' }],
    }))
    await flushPromises()
  })

  await capture('git-base-dirty-dialog', async () => {
    const wrapper = mount(GitBaseDirtyDialog, mountOptions({ context: 'finalize' }))
    void (wrapper.vm as unknown as { resolve: (p: string, f: string[]) => Promise<string> })
      .resolve('flowgate', ['client/src/a.ts', 'client/src/b.ts'])
    await flushPromises()
  })

  await capture('workflow-decision-modal', async () => {
    mount(WorkflowDecisionModal, mountOptions({ visible: true, docClass: 'R' }))
    await flushPromises()
  })

  await capture('workflow-edit-modal', async () => {
    mount(WorkflowDecisionModal, mountOptions({
      visible: true, mode: 'edit', docId: 'flowgate.default.0560.0016-T',
    }))
    await flushPromises()
  })

  await capture('review-reject-dialog', async () => {
    mount(ReviewRejectDialog, mountOptions({
      visible: true, docId: 'flowgate.default.0560.0016-T', docName: '[T] 3순위', docType: 'T',
    }))
    await flushPromises()
  })

  expect(Object.keys(cases)).toHaveLength(6)

  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(resolve(scratch, 'dialog-footer-geometry.0560.json'), JSON.stringify(cases, null, 2), 'utf8')
})
