// (A) 유지 — 0394 T0016 / NR0003 §6.3. 아래 주석대로 CSS 선언을 지키는 가드이며,
// 검사 범위는 T0004에서 이미 "이 오버레이를 스타일링하는 모든 시트"로 넓혀 두었다
// (NR0003 §5.3 "규칙이 전역이면 검사도 전역이어야 한다").
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

const MAIN_PANEL = 'src/main/components/MainPanel.vue'
const APP_CSS = 'shared/app.css'

const mainPanel = read(MAIN_PANEL)

// 0394 T0004 (NR0003 §9.2-나): the rule being checked is "no viewport height unit on a
// box inside the below-header overlay". That is a property of the stylesheet, not of a
// particular file — yet this suite used to read `.document-modal` out of MainPanel.vue
// by name. When the rule was lifted verbatim into shared/app.css, the check went red
// while the styling it guards was untouched and correct. Look in every sheet that
// styles this overlay instead, so moving a rule between them is a refactor and not a
// failure; a rule that exists in NO sheet is still an error.
const STYLESHEETS: ReadonlyArray<readonly [string, string]> = [
  [APP_CSS, read(APP_CSS)],
  [MAIN_PANEL, mainPanel],
]

function cssRule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const pattern = new RegExp(`${escaped}\\s*\\{([^}]+)\\}`)
  const bodies = STYLESHEETS.map(([, source]) => source.match(pattern)).filter(Boolean)

  expect(
    bodies.length,
    `missing CSS rule for ${selector} — looked in ${STYLESHEETS.map(([name]) => name).join(', ')}`,
  ).toBeGreaterThan(0)
  // Every definition has to satisfy the caller's expectation, not just the first one
  // found: two sheets defining the same selector is exactly how a bad value sneaks in.
  return bodies.map((match) => match![1]).join('\n')
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
    const rule = cssRule('.modal-bg--below-header > .modal-box')

    expect(rule).toMatch(/max-height:\s*calc\(100%/)
    expect(rule).not.toMatch(VIEWPORT_HEIGHT_UNIT)
  })

  it('still starts the overlay below the header', () => {
    // The cap above is only correct while the container really is the shorter track;
    // going back to a full-screen dim would also re-cover the run monitor (0269 D0002).
    expect(cssRule('.modal-bg--below-header')).toContain('top: var(--hdr-h)')
    expect(mainPanel).toMatch(/class="modal-bg modal-bg--below-header"/)
  })

  it('sizes the full view box in container units', () => {
    expect(cssRule('.document-modal')).not.toMatch(VIEWPORT_HEIGHT_UNIT)
  })

  it('keeps the narrow-window chat rule inside the overlay', () => {
    const rule = cssRule('.document-modal:has(.document-modal__body--conversation)')

    // `100dvh - 16px` here overflowed the container by a constant 36px at every viewport
    // height, clipping 18px off the composer on any window narrower than 820px.
    expect(rule).toMatch(/height:\s*calc\(100%/)
    expect(rule).not.toMatch(VIEWPORT_HEIGHT_UNIT)
    // Lifting the cap is what let the box outgrow its track; the container cap must survive.
    expect(rule).not.toMatch(/max-height:\s*none/)
  })
})
