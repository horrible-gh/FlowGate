/**
 * flowgate.v02.0048 T#4 — ↗ 열기 (D#1 §9.2 / §9.7): the Inspector opens a document in the v0.1 main
 * screen in a new tab, with 'noopener' (no `window.opener`, no sessionStorage copy) and a URL that
 * carries the doc_id and nothing else — never a token, even when this tab holds one.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { openDocument, openDocumentUrl } from '@/features/designer/inspector/openDocument'

const DOC = 'flowgate.v02.0048.0011-TR'
const ACCESS = 'header.access-token-payload.signature'
const REFRESH = 'refresh-token-value'

let open: ReturnType<typeof vi.fn>

beforeEach(() => {
  // A signed-in tab: the values the new tab must NOT receive through the URL.
  window.__accessToken__ = ACCESS
  sessionStorage.setItem('fg_access_token', ACCESS)
  sessionStorage.setItem('fg_refresh_token', REFRESH)
  localStorage.setItem('fg_refresh_token', REFRESH)
  localStorage.setItem('fg_current_project_id', 'flowgate')
  // With 'noopener' a browser always returns null.
  open = vi.fn(() => null)
  vi.stubGlobal('open', open)
})

afterEach(() => {
  vi.unstubAllGlobals()
  delete window.__accessToken__
  sessionStorage.clear()
  localStorage.clear()
})

function openedUrl(): URL {
  expect(open).toHaveBeenCalledTimes(1)
  return new URL(String(open.mock.calls[0][0]), window.location.origin)
}

describe('openDocument (flowgate.v02.0048 T#4)', () => {
  it("opens /main/?doc=<doc_id> in a new tab with '_blank' and 'noopener'", () => {
    openDocument(DOC)
    expect(open).toHaveBeenCalledWith(`/main/?doc=${DOC}`, '_blank', 'noopener')
  })

  it('the URL is a same-origin relative path holding the doc_id and nothing else', () => {
    openDocument(DOC)
    const raw = String(open.mock.calls[0][0])
    expect(raw.startsWith('/main/')).toBe(true)
    const url = openedUrl()
    expect(url.origin).toBe(window.location.origin)
    expect(url.pathname).toBe('/main/')
    expect([...url.searchParams.keys()]).toEqual(['doc'])
    expect(url.searchParams.get('doc')).toBe(DOC)
    expect(url.hash).toBe('')
  })

  it("carries no token, session value or project id even though this tab holds them", () => {
    openDocument(DOC)
    const raw = String(open.mock.calls[0][0])
    for (const secret of [ACCESS, REFRESH, 'token', 'flowgate/']) expect(raw).not.toContain(secret)
    expect(raw).not.toMatch(/access|refresh|session|project|#/i)
  })

  it('encodes the doc_id so it stays one query value', () => {
    openDocument('a&b=c #d/e?f')
    const url = openedUrl()
    expect([...url.searchParams.keys()]).toEqual(['doc'])
    expect(url.searchParams.get('doc')).toBe('a&b=c #d/e?f')
    expect(openDocumentUrl('a&b=c #d/e?f')).toBe('/main/?doc=a%26b%3Dc%20%23d%2Fe%3Ff')
  })

  it("the features string is exactly 'noopener' (the tab gets no window.opener and no sessionStorage copy)", () => {
    openDocument(DOC)
    const features = String(open.mock.calls[0][2])
    expect(features.split(',').map((item) => item.trim())).toContain('noopener')
    expect(features).not.toMatch(/opener=|noopener\s*=\s*(0|no|false)/i)
  })

  it('does not read the return value: a null (always null with noopener) is not treated as a blocked popup', () => {
    expect(openDocument(DOC)).toBeUndefined()
    open.mockReturnValueOnce({ closed: false })
    expect(openDocument(DOC)).toBeUndefined()
    expect(open).toHaveBeenCalledTimes(2)
  })
})
