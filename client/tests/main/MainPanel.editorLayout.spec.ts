import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Comments are stripped so that prose describing the bug (which quotes both
// braces and `vh` declarations) can never satisfy or break an assertion.
const source = readFileSync(
  join(process.cwd(), 'src/main/components/MainPanel.vue'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '')

function cssRule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull()
  return match?.[1] ?? ''
}

describe('document edit modal layout', () => {
  it('keeps the editor body shrinkable so the footer stays inside the box', () => {
    const rule = cssRule('.document-editor')

    // A viewport-unit minimum here is a flex shrink floor that the px-capped
    // `.document-modal` track cannot absorb, so the footer gets pushed out of
    // `.modal-box { overflow: hidden }` on tall viewports and clipped away.
    expect(rule).not.toMatch(/min-height:\s*[\d.]+v(h|min|max)/)
    expect(rule).toMatch(/min-height:\s*0/)
  })

  it('never lets the footer be squeezed out of the track', () => {
    expect(cssRule('.document-modal--edit .modal-ft')).toMatch(/flex-shrink:\s*0/)
  })

  it('leaves the textarea as the sole scroll container', () => {
    // The body clips instead of scrolling, and the textarea fills the track
    // rather than pinning its own height — together that means exactly one
    // scrollbar (the prior double-scrollbar regression).
    expect(cssRule('.document-editor')).toMatch(/overflow:\s*hidden/)

    const textarea = cssRule('.document-editor__textarea')
    expect(textarea).toMatch(/flex:\s*1 1 auto/)
    expect(textarea).toMatch(/min-height:\s*0/)
    expect(textarea).not.toMatch(/(min-)?height:\s*[\d.]+v(h|min|max)/)
  })

  it('bounds the modal height so the box itself defines the track', () => {
    // `100%`, not a vh cap: the same rule serves the full view, which is centred inside
    // `.modal-bg--below-header` (a container already shorter than the viewport).
    expect(cssRule('.document-modal')).toMatch(/height:\s*min\(860px,\s*100%\)/)
  })
})
