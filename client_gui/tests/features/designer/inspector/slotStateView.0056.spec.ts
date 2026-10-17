/**
 * flowgate.v02.0056 F1 — what a slot line draws for its state (0056 CH §5), decided in one place
 * (parts/slotStateView.ts): a text badge only for a tone that says something to the operator (a settled one
 * drawn dim), nothing for a neutral or unknown state, and the plain wording of a line without a document.
 */
import { describe, expect, it } from 'vitest'
import { SLOT_STATE_SHOWN_TONES, slotStateTitle, slotStateView } from '@/features/designer/inspector/parts/slotStateView'

const NONE = { kind: 'none', tone: 'none', text: '', title: undefined, quiet: false }

describe('slotStateView (flowgate.v02.0056 F1)', () => {
  it('the shown tones are every tone but neutral', () => {
    expect([...SLOT_STATE_SHOWN_TONES].sort()).toEqual(['danger', 'hold', 'info', 'running', 'success', 'warning'])
  })

  it.each(SLOT_STATE_SHOWN_TONES)('%s: a badge with the label; the tooltip adds the sub text and the title', (tone) => {
    expect(slotStateView({ label: '검수 대기', tone, sub: '2/4', title: '확인 완료' }, false, null)).toEqual({
      kind: 'badge',
      tone,
      text: '검수 대기',
      title: '검수 대기 · 2/4 · 확인 완료',
      quiet: tone === 'success',
    })
  })

  it('only a settled (success) badge is quiet', () => {
    expect(slotStateView({ label: '승인됨', tone: 'success' }, false, null).quiet).toBe(true)
    expect(slotStateView({ label: '검수 대기', tone: 'warning' }, false, null).quiet).toBe(false)
    expect(slotStateView({ label: '진행 중', tone: 'running' }, false, null).quiet).toBe(false)
  })

  it('a neutral state, no state and an undefined state draw nothing', () => {
    expect(slotStateView({ label: '작성 중', tone: 'neutral' }, false, null)).toEqual(NONE)
    expect(slotStateView({ label: '완료', tone: 'neutral', sub: '전부' }, false, null)).toEqual(NONE)
    expect(slotStateView(null, false, null)).toEqual(NONE)
    expect(slotStateView(undefined, false, '문서 없음')).toEqual(NONE)
  })

  it('a line without a document says why in plain words, whatever its state; the wording handed in, else nothing', () => {
    expect(slotStateView({ label: '검수 대기', tone: 'warning' }, true, '실행 후')).toEqual({
      kind: 'empty',
      tone: 'none',
      text: '실행 후',
      title: '실행 후',
      quiet: false,
    })
    expect(slotStateView(null, true, '문서 없음')).toMatchObject({ kind: 'empty', text: '문서 없음', title: '문서 없음' })
    expect(slotStateView(null, true, null)).toEqual({ kind: 'empty', tone: 'none', text: '', title: undefined, quiet: false })
  })

  it('slotStateTitle: the label, then the sub text and the title that are given', () => {
    expect(slotStateTitle({ label: '보류', tone: 'hold' })).toBe('보류')
    expect(slotStateTitle({ label: '보류', tone: 'hold', sub: null, title: '확인 완료' })).toBe('보류 · 확인 완료')
    expect(slotStateTitle({ label: '보류', tone: 'hold', sub: '2/4', title: '' })).toBe('보류 · 2/4')
    expect(slotStateTitle({ label: '보류', tone: 'hold', sub: '2/4', title: '확인 완료' })).toBe('보류 · 2/4 · 확인 완료')
  })
})
