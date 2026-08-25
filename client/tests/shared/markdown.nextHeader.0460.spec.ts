import { describe, expect, it } from 'vitest'
import { stripFrontmatter, stripLeadingNextHeader } from '@shared/utils/markdown'

// R0001 (group 0460) — the leading next-document header must not appear in the
// rendered document at all: "당장 이 문서만 봐도 헤더가 문서에 그대로 드러나잖아".
//
// rev1-rev4 wrapped the seven fields in a fenced `text` block, which still put
// them on screen. rev5 removes them before the body reaches the renderer, the
// same way stripFrontmatter() removes the YAML header above it.
//
// Two recognition defects found in rev4 stay pinned here, because removal has to
// fire on exactly the documents that used to slip through:
//
//  1. A gate of md.startsWith('next_type:') can never be true for a stored
//     document: stripFrontmatter() consumes the closing `---` plus ONE line
//     ending, so the blank line between frontmatter and body survives and the
//     string handed to this function begins with "\r\n" (or "\n").
//  2. A per-line regex ending in `(.*)$` matches no line of a CRLF document —
//     JS `.` never matches CR and a non-multiline `$` only matches the very end
//     of the string.
//
// REJECTION_DOC is the byte-for-byte content of the document the "헤더 보정
// 안되는데?" rejection was filed against: test.test.0010.0001-R on the
// flowgate.test deployment (CRLF, frontmatter, one blank line, seven fields).

const HEADER_FIELDS = [
  'next_type: R',
  'next_type_detail: 요건정의',
  'project: flowgate',
  'module: default',
  'group: 0010',
  'title: 테스트',
  'target_id: R0001',
]

const REJECTION_DOC = [
  '---',
  'title: 0408 TR0021 rev1 provider tab verify',
  'type: R',
  'doc_id: test.test.0010.0001-R',
  '---',
  '',
  ...HEADER_FIELDS,
].join('\r\n')

describe('stripLeadingNextHeader — the header leaves the rendered document', () => {
  it('removes the header of the rejected flowgate.test document, leaving nothing behind', () => {
    const body = stripFrontmatter(REJECTION_DOC)
    // The gate that silently did nothing in rev3.
    expect(body.startsWith('next_type:')).toBe(false)
    expect(body).toBe('\r\n' + HEADER_FIELDS.join('\r\n'))

    const out = stripLeadingNextHeader(body)
    expect(out).toBe('')
    expect(out).not.toContain('next_type')
    expect(out).not.toContain('```')
  })

  it('removes the header and starts the body at its first real line (LF)', () => {
    const doc = ['---', 'title: t', '---', '', ...HEADER_FIELDS, '', '# 본문', '', '첫 줄', '둘째 줄'].join('\n')
    const body = stripFrontmatter(doc)
    expect(body).toBe('\n' + HEADER_FIELDS.join('\n') + '\n\n# 본문\n\n첫 줄\n둘째 줄')

    expect(stripLeadingNextHeader(body)).toBe('# 본문\n\n첫 줄\n둘째 줄')
  })

  it('keeps the body below a CRLF header byte-exact, CR included', () => {
    const body = HEADER_FIELDS.join('\r\n') + '\r\n\r\n본문 첫 줄\r\n본문 둘째 줄'
    const out = stripLeadingNextHeader(body)
    expect(out).toBe('본문 첫 줄\r\n본문 둘째 줄')
    // Every LF that remains is part of a CRLF pair — no half-converted line.
    expect(out.split('\n').length - 1).toBe(out.split('\r\n').length - 1)
  })

  it('removes a CRLF header that opens the document with no frontmatter at all', () => {
    expect(stripLeadingNextHeader(HEADER_FIELDS.join('\r\n'))).toBe('')
  })

  it('removes the plain LF header the earlier revisions covered', () => {
    expect(stripLeadingNextHeader(HEADER_FIELDS.join('\n') + '\n\n산문.')).toBe('산문.')
  })

  it('tolerates several blank lines and a BOM above the header', () => {
    const body = '﻿\r\n   \r\n\n' + HEADER_FIELDS.join('\n') + '\n\n산문.'
    expect(stripLeadingNextHeader(body)).toBe('산문.')
  })

  it('accepts group_id in place of group, as the server normalizer does', () => {
    const withGroupId =
      '\r\n' + HEADER_FIELDS.map((f) => (f.startsWith('group:') ? 'group_id: 0010' : f)).join('\r\n')
    expect(stripLeadingNextHeader(withGroupId)).toBe('')
  })

  it('is idempotent — a second pass changes nothing', () => {
    const once = stripLeadingNextHeader(stripFrontmatter(REJECTION_DOC))
    expect(stripLeadingNextHeader(once)).toBe(once)

    const withBody = stripLeadingNextHeader(HEADER_FIELDS.join('\n') + '\n\n# 본문\n\n산문.')
    expect(stripLeadingNextHeader(withBody)).toBe(withBody)
  })
})

describe('stripLeadingNextHeader — everything else is returned untouched', () => {
  it('does not touch prose that merely follows blank lines', () => {
    const body = '\r\n\r\n첫 줄\r\n둘째 줄\r\nproject: flowgate'
    expect(stripLeadingNextHeader(body)).toBe(body)
  })

  it('does not touch an incomplete or out-of-order block below the blank line', () => {
    const missing = '\n' + HEADER_FIELDS.filter((f) => !f.startsWith('module:')).join('\n')
    expect(stripLeadingNextHeader(missing)).toBe(missing)

    const swapped = '\n' + [
      HEADER_FIELDS[0], HEADER_FIELDS[1], HEADER_FIELDS[3], HEADER_FIELDS[2],
      HEADER_FIELDS[4], HEADER_FIELDS[5], HEADER_FIELDS[6],
    ].join('\n')
    expect(stripLeadingNextHeader(swapped)).toBe(swapped)
  })

  it('does not touch the same seven fields quoted further down a document', () => {
    const body = '# 본문\n\n아래는 예시다.\n\n' + HEADER_FIELDS.join('\n')
    expect(stripLeadingNextHeader(body)).toBe(body)
  })

  it('returns falsy input untouched', () => {
    expect(stripLeadingNextHeader('')).toBe('')
  })
})
