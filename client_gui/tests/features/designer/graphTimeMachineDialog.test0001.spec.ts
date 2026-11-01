/**
 * flowgate.v02test.0001 T#1 — the 되돌리기 dialog (center/run/GraphTimeMachineDialog.vue) holds a title, the card,
 * one line and its buttons; the result a title, one line and 확인. No bullets, counts, notes or ids repeated.
 */
import { mount } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import GraphTimeMachineDialog from '@/features/designer/center/run/GraphTimeMachineDialog.vue'
import type { FlowTimeMachineDialog } from '@/features/designer/center/designerCenterSession'

registerAppMessages(i18n)

const titles = new Map([['card-1', '설계']])
const base: FlowTimeMachineDialog = { seq: 1, flowId: 'p.ui.0001', nodeId: 'card-1', phase: 'confirm', basis: 'b', notice: null, outcome: null }
const mounts: Array<{ unmount(): void }> = []

function show(dialog: FlowTimeMachineDialog) {
  i18n.global.locale.value = 'ko'
  const wrapper = mount(GraphTimeMachineDialog, { props: { dialog, titles }, attachTo: document.body, global: { plugins: [i18n] } })
  mounts.push(wrapper)
  return wrapper
}

const q = (test: string) => document.body.querySelector(`[data-test="${test}"]`)
const title = () => q('graph-time-machine-title')!.textContent!.trim()
const body = () => q('graph-time-machine')!
const lines = () => [...body().querySelectorAll('p')].map((p) => p.textContent!.trim())
const buttons = () => [...document.body.querySelectorAll('.graph-tm-actions button')].map((b) => b.textContent!.trim())

afterEach(() => {
  while (mounts.length) mounts.pop()!.unmount()
  document.body.innerHTML = ''
})

describe('되돌리기 dialog — simplified (flowgate.v02test.0001 T#1)', () => {
  it('confirmation: title, the card, one line, 취소 / 되돌리기 — nothing else', () => {
    show(base)
    expect(title()).toBe('여기부터 되돌리기')
    expect(lines()).toEqual(['설계 (card-1)', '이 카드부터 이후 카드를 되돌립니다. 실행 기록은 유지됩니다.'])
    expect(body().querySelector('ul')).toBeNull()
    expect(buttons()).toEqual(['취소', '되돌리기'])
  })

  it('keeps a real notice (basis changed) and nothing more', () => {
    show({ ...base, notice: 'changed' })
    expect(q('graph-time-machine-notice')!.getAttribute('data-notice')).toBe('changed')
    expect(lines()).toHaveLength(3)
  })

  it('done: 되돌리기 완료, one line, 확인 — no impact list, counts or note', () => {
    show({
      ...base,
      phase: 'done',
      outcome: {
        kind: 'done',
        result: { nodeKey: 'card-1', impactNodeKeys: ['card-1', 'card-2'], resetNodeKeys: ['card-2'], revertedCommitCount: 2, invalidatedAttemptCount: 1, reopenedDocumentIds: ['x.0001-T'] },
      } as FlowTimeMachineDialog['outcome'],
    })
    expect(title()).toBe('되돌리기 완료')
    expect(lines()).toEqual(['선택한 카드부터 되돌렸습니다.'])
    expect(body().querySelector('ul')).toBeNull()
    expect(body().textContent).not.toMatch(/card-2|영향|병합|attempt|x\.0001-T/)
    expect(buttons()).toEqual(['확인'])
  })

  it('blocked: 되돌리기 실패 with the first reason of the server only', () => {
    show({
      ...base,
      phase: 'blocked',
      outcome: {
        kind: 'blocked',
        code: 'graph_time_machine_blocked',
        message: '',
        impactNodeKeys: ['card-2'],
        reasons: [{ code: 'a', message: '이미 실행 중입니다.' }, { code: 'b', message: '두 번째 사유' }],
      } as FlowTimeMachineDialog['outcome'],
    })
    expect(title()).toBe('되돌리기 실패')
    expect(lines()).toEqual(['이미 실행 중입니다.'])
    expect(buttons()).toEqual(['확인'])
  })

  it('failed: 되돌리기 실패 with the short reason, 확인', () => {
    show({ ...base, phase: 'failed', outcome: { kind: 'failed', code: null, status: null, message: '' } })
    expect(title()).toBe('되돌리기 실패')
    expect(q('graph-time-machine-failure')!.textContent).toBe('되돌리지 못했습니다.')
    expect(buttons()).toEqual(['확인'])
  })
})
