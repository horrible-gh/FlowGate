// flowgate.default.0649 T#3 (NR0003 O2/O4, §5.3 ContinuousWorkDialog preset path) — a work-plan
// preset fills values onto the rows the workflow already has (keep-workflow). When the apply
// preview says keep-workflow is refused for a card-order reason (order_differs, orphan rows,
// a started card out of place, …) starting would run an order other than the plan's: the
// dialog shows the server's reason and keeps the start button off. Other preview answers
// leave the start as it was.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
  putRequest: vi.fn(),
}))

function seqResponse() {
  return {
    data: {
      doc_id: 'flowgate.default.0649.0001-R', doc_class: 'R', decided: true,
      items: [
        { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
        { id: 2, item_seq: 2, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 3, item_seq: 3, type: 'D', label: '기본설계', status: 'pending' },
      ],
      head: { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

function preset(warnings: any[] = []) {
  return {
    sourceDocId: 'flowgate.default.0649.0004-WP',
    sourceRevisionNo: 3,
    instructionMode: 'auto_approved',
    targetSeq: 3,
    providerOverrides: {},
    messageOverrides: {},
    defaultMessage: '',
    filledSeqs: [],
    warnings,
  }
}

function mountDialog(warnings: any[] = []) {
  getRequest.mockResolvedValue(seqResponse())
  return mount(ContinuousWorkDialog, {
    props: { visible: true, docRef: 'flowgate.default.0649.0001-R', preset: preset(warnings) },
    global: { plugins: [i18n] },
  })
}

function primary() {
  return document.querySelector('[data-dialog-action-role="primary"]') as HTMLButtonElement
}

async function switchToAiDirect() {
  // installPreset() releases its "initializing" guard on a setTimeout(0)
  await new Promise(resolve => setTimeout(resolve, 0))
  ;(document.querySelectorAll('.cwd-mode input')[1] as HTMLInputElement).click()
  await flushPromises()
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset()
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('ContinuousWorkDialog preset — card order (0649 T#3)', () => {
  it('a preview whose keep-workflow is refused for order_differs shows the reason and blocks the start', async () => {
    postRequest.mockResolvedValue({
      data: {
        fill_preview: { target_seq: 3 },
        apply_blockers: { keep_workflow: 'order_differs', change_workflow: null },
        warnings: [{ code: 'order_differs', severity: 'warning', message: '계획 순서와 워크플로 순서가 다릅니다.' }],
      },
    })
    const wrapper = mountDialog()
    await flushPromises()
    expect(primary().disabled).toBe(false)

    await switchToAiDirect()
    expect(postRequest).toHaveBeenCalledWith(
      '/api/v1/documents/flowgate.default.0649.0004-WP/work-plan/apply/preview',
      { instruction_mode: 'ai_direct' },
    )
    expect(document.querySelector('[data-test="preset-card-blocker"]')?.textContent?.trim())
      .toBe('계획 순서와 워크플로 순서가 다릅니다.')
    expect(primary().disabled).toBe(true)
    wrapper.unmount()
  })

  it('without a server message the fallback copy is used', async () => {
    postRequest.mockResolvedValue({
      data: { fill_preview: {}, apply_blockers: { keep_workflow: 'order_conflicts_started' }, warnings: [] },
    })
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    expect(document.querySelector('[data-test="preset-card-blocker"]')?.textContent?.trim())
      .toBe(i18n.global.t('main.continuous_work.preset_card_order_blocked'))
    expect(primary().disabled).toBe(true)
    wrapper.unmount()
  })

  it('a keep blocker that is not about card order (nothing_to_fill) does not block the start', async () => {
    postRequest.mockResolvedValue({
      data: { fill_preview: {}, apply_blockers: { keep_workflow: 'nothing_to_fill' }, warnings: [] },
    })
    const wrapper = mountDialog()
    await flushPromises()
    await switchToAiDirect()
    expect(document.querySelector('[data-test="preset-card-blocker"]')).toBeNull()
    expect(primary().disabled).toBe(false)
    wrapper.unmount()
  })

  it('a preset handed over with a card-order blocker starts blocked', async () => {
    const wrapper = mountDialog([
      { code: 'started_card_removed', severity: 'blocker', count: 1, keys: [], item_seqs: [], message: '시작된 카드가 계획에서 사라졌습니다.' },
    ])
    await flushPromises()
    expect(document.querySelector('[data-test="preset-card-blocker"]')?.textContent?.trim())
      .toBe('시작된 카드가 계획에서 사라졌습니다.')
    expect(primary().disabled).toBe(true)
    wrapper.unmount()
  })
})
