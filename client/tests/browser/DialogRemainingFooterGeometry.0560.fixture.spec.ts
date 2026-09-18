/**
 * flowgate.default.0560 T0039 §8 / §공통 UI 계약 — export the six dialogs NR0029 §4.3 listed
 * as footer-order violations, so the companion harness can measure them under the PRODUCTION
 * stylesheet.
 *
 * NR0029 §4.3 named exactly these six instances and, for each, the painted order that broke
 * DS0007 §3.1 ("취소는 항상 주버튼 바로 왼쪽"). jsdom can only answer "which element comes
 * first in the DOM"; `.fg-dialog-footer__actions` is a `justify-content: flex-end` flex row
 * whose visual order CSS is free to change, and `ContinuousWarningDialog`'s old
 * `.cwarn-footer` was a `flex-wrap` 50% grid — exactly the case where DOM order and painted
 * order part company. This fixture is the export half; the measuring half is
 * `tests/browser/dialog-remaining-footer-geometry.0560.mjs`.
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

import CommandSelectorModal from '@main/components/CommandSelectorModal.vue'
import ContinuousWarningDialog from '@main/components/ContinuousWarningDialog.vue'
import DesignHandoffDialog from '@main/components/DesignHandoffDialog.vue'
import GroupInfoModal from '@main/components/GroupInfoModal.vue'
import MentionMessageDialog from '@main/components/MentionMessageDialog.vue'
import TimeMachineDialog from '@main/components/TimeMachineDialog.vue'
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

const STEPS = [
  { docId: 'flowgate.default.0560.0009-TR', seq: 9, typeCode: 'TR', title: '앞 레포트' },
  { docId: 'flowgate.default.0560.0010-T', seq: 10, typeCode: 'T', title: '지시' },
]

it('exports the six NR0029 §4.3 footers for built-CSS geometry', async () => {
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
    if (String(url).includes('/commands')) {
      return Promise.resolve({
        data: { commands: [{ command_id: 'c1', name: '빌드', template: 'npm run build' }] },
      })
    }
    return Promise.resolve({ data: { messages: [] } })
  })

  const cases: Record<string, string> = {}

  async function capture(name: string, open: () => Promise<void>): Promise<void> {
    document.body.innerHTML = ''
    await open()
    cases[name] = exportOverlay()
    resetDialogSystem()
  }

  await capture('continuous-warning-dialog', async () => {
    mount(ContinuousWarningDialog, mountOptions({
      visible: true, project: 'flowgate', stepCount: 3, targetLabel: '작업레포트', reviewMode: false,
    }))
    await flushPromises()
  })

  await capture('design-handoff-dialog', async () => {
    mount(DesignHandoffDialog, mountOptions({
      visible: true,
      docRef: 'flowgate.default.0560.0039-T',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0560',
      defaultTypes: [],
    }))
    await flushPromises()
  })

  await capture('mention-message-dialog', async () => {
    mount(MentionMessageDialog, mountOptions({
      visible: true, projectId: 'flowgate', docType: 'T', docTypes: [], candidates: [],
    }))
    await flushPromises()
  })

  // NR0029 §4.3's row is the RESULT screen — `[Git패널][재시도][닫기]`, with 닫기 painted
  // rightmost. A retryable result is the variant that has both a cancel and a primary.
  await capture('time-machine-dialog-result', async () => {
    const wrapper = mount(TimeMachineDialog, mountOptions({ visible: false, steps: STEPS }))
    await wrapper.setProps({
      visible: true,
      cancelResult: {
        attempted: false, blocked_reason: 'dirty_worktree', canceled: [], skipped: [],
        terminal_reopened: [], stopped_reason: null, retryable: true,
      },
    })
    await flushPromises()
  })

  await capture('group-info-modal', async () => {
    mount(GroupInfoModal, mountOptions({
      visible: true, groupId: 'flowgate.default.0560', groupName: '다이얼로그 공통 계층', documents: [],
    }))
    await flushPromises()
  })

  // NR0029 §4.3's row is the ERROR/result mode, whose [닫기] used to be a `btn-primary`.
  await capture('command-selector-modal-result', async () => {
    postRequest.mockResolvedValue({
      data: {
        command_id: 'c1', resolved: 'npm run build', stdout: '', stderr: 'boom',
        return_code: 1, executed_at: '2026-09-18T07:00:00+09:00',
      },
    })
    const wrapper = mount(CommandSelectorModal, mountOptions({ visible: false }))
    await wrapper.setProps({ visible: true })
    await flushPromises()
    const item = document.querySelector<HTMLElement>('.csm-item')
    expect(item, 'the command list did not render').toBeTruthy()
    item!.click()
    await flushPromises()
    const execute = document.querySelector<HTMLButtonElement>('[data-dialog-action-id="executeCommand-2"]')
    expect(execute, 'the execute action is missing').toBeTruthy()
    execute!.click()
    await flushPromises()
    expect(document.querySelector('.csm-result-code'), 'the result screen did not open').toBeTruthy()
  })

  expect(Object.keys(cases)).toHaveLength(6)

  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(
    resolve(scratch, 'dialog-remaining-footer-geometry.0560.json'),
    JSON.stringify(cases, null, 2),
    'utf8',
  )
})
