import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// jsdom computes no layout, so — as with MainPanel.editorLayout.spec.ts and
// AiInvokeMiniplayer.spec.ts — this contract is pinned at the CSS source level.
// Comments are stripped first so that prose describing the bug (which quotes the
// very `vh`/`dvh` declarations being forbidden) can neither satisfy nor break an
// assertion.
function read(relative: string): string {
  return readFileSync(join(process.cwd(), relative), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
}

const mainPanel = read('src/main/components/MainPanel.vue')
const appCss = read('shared/app.css')

function cssRule(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull()
  return match?.[1] ?? ''
}

// Every height limit on a box centred in `.modal-bg--below-header` must be measured
// against that container, whose height is `100vh - var(--hdr-h)`. A raw viewport unit
// makes the box taller than its track; `align-items: center` splits the excess evenly,
// so half of it lands below the viewport on a `position: fixed` layer that nothing
// scrolls. In the CH full view that unreachable strip holds the composer — the send and
// (mid-run) stop buttons. B0001 / NR0003.
const VIEWPORT_HEIGHT_UNIT = /(max-)?height:[^;]*\b[\d.]+(dvh|svh|lvh|vh|vmin|vmax)/

describe('document full view layout', () => {
  it('caps the full view box against the overlay, not the viewport', () => {
    const rule = cssRule(appCss, '.modal-bg--below-header > .modal-box')

    expect(rule).toMatch(/max-height:\s*calc\(100%/)
    expect(rule).not.toMatch(VIEWPORT_HEIGHT_UNIT)
  })

  it('still starts the overlay below the header', () => {
    // The cap above is only correct while the container really is the shorter track;
    // going back to a full-screen dim would also re-cover the run monitor (0269 D0002).
    expect(cssRule(appCss, '.modal-bg--below-header')).toContain('top: var(--hdr-h)')
    expect(mainPanel).toMatch(/class="modal-bg modal-bg--below-header"/)
  })

  it('sizes the full view box in container units', () => {
    expect(cssRule(mainPanel, '.document-modal')).not.toMatch(VIEWPORT_HEIGHT_UNIT)
  })

  it('keeps the narrow-window chat rule inside the overlay', () => {
    const rule = cssRule(mainPanel, '.document-modal:has(.document-modal__body--conversation)')

    // `100dvh - 16px` here overflowed the container by a constant 36px at every viewport
    // height, clipping 18px off the composer on any window narrower than 820px.
    expect(rule).toMatch(/height:\s*calc\(100%/)
    expect(rule).not.toMatch(VIEWPORT_HEIGHT_UNIT)
    // Lifting the cap is what let the box outgrow its track; the container cap must survive.
    expect(rule).not.toMatch(/max-height:\s*none/)
  })
})
