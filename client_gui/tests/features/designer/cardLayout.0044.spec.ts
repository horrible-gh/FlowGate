/**
 * flowgate.v02test.0044 (mockup v4): every card is a fixed 176x92; 병합 대기 · 병합 중 · 병합 실패 and Not handed over are head-row
 * icons; the footer holds the buttons only (documents left; 병합 충돌 and [승인] as 22x22 icon buttons at the right end).
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); i18n.global.locale.value = 'ko' })
const render = (data: Record<string, unknown>) => {
  const w = mount(FlowNodeCard, { props: { id: 'n1', data: { kind: 'task', title: 'Card', selected: false, state: null, ...data } as any }, global: { plugins: [i18n], stubs: { Handle: true } } })
  wrappers.push(w)
  return w
}
const doc = (docId: string, report = false) => ({ docId, docType: report ? 'TR' : 'T', title: docId, ownerSlot: report ? 'report' : 'instruction', report })
const t = (key: string) => i18n.global.t(key)
const source = readFileSync(resolve(__dirname, '../../../src/features/designer/center/canvas/FlowNodeCard.vue'), 'utf8')
const rule = (selector: string) => source.match(new RegExp(`\\n${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{([^}]*)\\}`))?.[1] ?? ''

describe('0044 card layout: fixed size', () => {
  it('the card has a fixed height, and the document row never wraps', () => {
    expect(rule('.flow-node-card')).toMatch(/height:\s*92px;/)
    expect(rule('.flow-node-card')).not.toMatch(/min-height/)
    expect(source).toMatch(/\.flow-node-documents \{[^}]*flex-wrap:\s*nowrap;/)
    expect(source).not.toMatch(/flow-node-flags|flow-node-flag\b/)
  })
})

describe('0044 card layout: head-row integration and 미인계 icons', () => {
  it.each(['pending', 'integrating', 'failed'])('%s is a named head-row icon, not a text chip', integration => {
    const w = render({ integration })
    const mark = w.get('.flow-node-head [data-test="designer-canvas-node-integration"]')
    // a waiting merge (hourglass) names what it waits for — its turn when no card is named.
    const label = integration === 'pending' ? t('gui.designer.center.integration.waiting_turn') : t(`gui.designer.center.integration.${integration}`)
    expect(mark.attributes('role')).toBe('img')
    expect(mark.attributes('aria-label')).toBe(label)
    expect(mark.attributes('title')).toBe(label)
    expect(mark.find('svg path').exists()).toBe(true)
    expect(w.find('.flow-node-body [data-test="designer-canvas-node-integration"]').exists()).toBe(false)
    expect(w.find('.flow-node-footer').exists()).toBe(false)
  })
  it('the three integration glyphs differ, and 병합 실패 is not the error state glyph', () => {
    const paths = ['pending', 'integrating', 'failed'].map(integration => render({ integration }).get('[data-test="designer-canvas-node-integration"] path').attributes('d'))
    expect(new Set(paths).size).toBe(3)
    const error = render({ state: 'blocked' }).get('[data-test="designer-canvas-node-state"] path').attributes('d')
    expect(paths[2]).not.toBe(error)
  })
  it('a failed syntax check adds its sentence to the name', () => {
    const mark = render({ integration: 'failed', verificationFailed: true }).get('[data-test="designer-canvas-node-integration"]')
    expect(mark.attributes('title')).toBe(`${t('gui.designer.center.integration.failed')} · ${t('gui.designer.center.integration.verification_failed')}`)
    expect(mark.attributes('aria-label')).toBe(mark.attributes('title'))
  })
  it('미인계 sits in the head row with its count and sentence as its name', () => {
    const mark = render({ uncarried: { attempts: 1, files: 3, unknown: false } }).get('.flow-node-head [data-test="designer-canvas-node-uncarried"]')
    expect(mark.attributes('role')).toBe('img')
    expect(mark.attributes('aria-label')).toMatch(/^미인계 3 · /)
    expect(mark.attributes('title')).toBe(mark.attributes('aria-label'))
  })
})

describe('0044 card layout: footer buttons', () => {
  it('병합 충돌 is an icon button at the footer end, after the documents, even on a card without documents', async () => {
    const w = render({ integration: 'conflict', documents: [doc('a'), doc('b', true)] })
    const footer = w.get('.flow-node-footer')
    const buttons = footer.findAll('button')
    expect(buttons.map(b => b.attributes('data-test'))).toEqual(['designer-canvas-node-document', 'designer-canvas-node-document', 'designer-canvas-node-conflict'])
    const conflict = footer.get('[data-test="designer-canvas-node-conflict"]')
    expect(conflict.text()).toBe('')
    expect(conflict.attributes('aria-label')).toBe(`${t('gui.designer.center.integration.conflict')} · ${t('gui.designer.center.integration.open_conflict')}`)
    expect(conflict.attributes('title')).toBe(conflict.attributes('aria-label'))
    // (mockup v7): the head carries the red conflict mark too (shown only), and the card a red ring
    const mark = w.get('.flow-node-head [data-test="designer-canvas-node-integration"]')
    expect(mark.attributes('data-integration')).toBe('conflict')
    expect(mark.classes()).toContain('flow-node-mark--danger')
    expect(mark.attributes('title')).toBe(conflict.attributes('aria-label'))
    expect(w.get('[data-test="designer-canvas-node"]').classes()).toContain('has-conflict')
    await conflict.trigger('click')
    expect(w.emitted('conflict')).toEqual([['n1']])
    const bare = render({ integration: 'conflict' })
    expect(bare.get('.flow-node-footer [data-test="designer-canvas-node-conflict"]').exists()).toBe(true)
  })
  it('[승인] is an icon button named 승인; a blocked one is disabled and names its reason', async () => {
    const gateState = { key: 'waiting_confirm', labelKey: 'gui.designer.center.card_states.waiting_confirm', tone: 'warning', reason: null }
    const w = render({ kind: 'approval', gateState, approvable: true })
    const approve = w.get('.flow-node-footer [data-test="designer-canvas-node-approve"]')
    expect(approve.text()).toBe('')
    expect(approve.find('svg path').exists()).toBe(true)
    expect(approve.attributes('aria-label')).toBe(t('gui.inspector.gateCard.confirm'))
    expect(approve.attributes('title')).toBe(t('gui.inspector.gateCard.confirm'))
    await approve.trigger('click')
    expect(w.emitted('approve')).toEqual([['n1']])
    const reason = 'gui.designer.center.integration.open_conflict'
    const blocked = render({ kind: 'approval', gateState, approvable: true, approvalBlocked: true, approvalReason: reason }).get('[data-test="designer-canvas-node-approve"]')
    expect(blocked.attributes('disabled')).toBeDefined()
    expect(blocked.attributes('aria-label')).toBe(`${t('gui.inspector.gateCard.confirm')} · ${t(reason)}`)
  })
  it('a card with nothing to press has no footer', () => {
    expect(render({ approvable: true }).find('.flow-node-footer').exists()).toBe(false)
    expect(render({ uncarried: { attempts: 1, files: 1, unknown: false }, integration: 'pending' }).find('.flow-node-footer').exists()).toBe(false)
  })
})
