import { describe, expect, it } from 'vitest'
import {
  buildCandidateList,
  prependMessageSection,
  WILDCARD_DOC_TYPE,
  SECTION_SEPARATOR,
  type MessageEntry,
} from '@main/utils/mentionMessages'

function msg(over: Partial<MessageEntry>): MessageEntry {
  return { id: 1, doc_type: 'D', message: 'm', updated_at: '2026-06-13T00:00:00+09:00', ...over }
}

describe('buildCandidateList (L0007 §2.1)', () => {
  it('ranks specific doc_type above [전체], drops unrelated types', () => {
    const raw: MessageEntry[] = [
      msg({ id: 1, doc_type: '*', message: 'all-msg' }),
      msg({ id: 2, doc_type: 'D', message: 'design-msg' }),
      msg({ id: 3, doc_type: 'P', message: 'proto-msg' }), // unrelated -> dropped
    ]
    const out = buildCandidateList(raw, 'D')
    expect(out.map((m) => m.message)).toEqual(['design-msg', 'all-msg'])
  })

  it('within a rank, sorts updated_at desc then id asc', () => {
    const raw: MessageEntry[] = [
      msg({ id: 10, doc_type: 'D', message: 'older', updated_at: '2026-06-10T00:00:00+09:00' }),
      msg({ id: 11, doc_type: 'D', message: 'newer', updated_at: '2026-06-12T00:00:00+09:00' }),
      msg({ id: 5, doc_type: 'D', message: 'tieA', updated_at: '2026-06-12T00:00:00+09:00' }),
    ]
    const out = buildCandidateList(raw, 'D')
    // newest first; among equal updated_at the smaller id wins
    expect(out.map((m) => m.message)).toEqual(['tieA', 'newer', 'older'])
  })

  it('dedupes by trim(message), keeping the higher-priority entry', () => {
    const raw: MessageEntry[] = [
      msg({ id: 1, doc_type: '*', message: '  dup  ' }),
      msg({ id: 2, doc_type: 'D', message: 'dup' }),
    ]
    const out = buildCandidateList(raw, 'D')
    expect(out).toHaveLength(1)
    expect(out[0].doc_type).toBe('D') // specific (rank 0) kept over [전체]
  })

  it('when current type is the wildcard, only [전체] rows show, no double count', () => {
    const raw: MessageEntry[] = [
      msg({ id: 1, doc_type: '*', message: 'all-1' }),
      msg({ id: 2, doc_type: '*', message: 'all-2' }),
      msg({ id: 3, doc_type: 'D', message: 'design' }), // dropped (not '*')
    ]
    const out = buildCandidateList(raw, WILDCARD_DOC_TYPE)
    expect(out.map((m) => m.message).sort()).toEqual(['all-1', 'all-2'])
  })

  it('returns [] for empty input (L0007 fallback trigger)', () => {
    expect(buildCandidateList([], 'D')).toEqual([])
  })
})

describe('prependMessageSection (R0001 group 0081)', () => {
  it('prepends the message as a labeled section ABOVE the mention (not buried at the end)', () => {
    const out = prependMessageSection('mention', 'msg', '사용자 메세지')
    expect(out).toBe('## 사용자 메세지\n---\nmsg' + SECTION_SEPARATOR + 'mention')
    // the section leads; the original mention follows
    expect(out.startsWith('## 사용자 메세지')).toBe(true)
    expect(out.endsWith('mention')).toBe(true)
  })
  it('empty mention -> section alone (no trailing separator)', () => {
    expect(prependMessageSection('', 'msg', 'User message')).toBe('## User message\n---\nmsg')
  })
  it('blank message -> mention unchanged', () => {
    expect(prependMessageSection('mention', '   ', 'User message')).toBe('mention')
  })
  it('trims the message body inside the section', () => {
    expect(prependMessageSection('mention', '  msg  ', 'H')).toBe('## H\n---\nmsg' + SECTION_SEPARATOR + 'mention')
  })
})
