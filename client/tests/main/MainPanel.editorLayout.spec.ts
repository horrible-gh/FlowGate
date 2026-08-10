// (A) 유지 — 0394 T0016 / NR0003 §6.3.
// 이 파일이 읽는 것은 CSS 선언과, ".document-modal 규칙이 세 시트를 통틀어 한 벌뿐"이라는
// 전역 불변식이다(두 벌이 되는 순간 편집기와 변경사항 창이 조용히 갈라진다). jsdom은 스타일을
// 적용하지 않으므로 어느 쪽도 마운트로는 관찰할 수 없다.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Comments are stripped so that prose describing the bug (which quotes both
// braces and `vh` declarations) can never satisfy or break an assertion.
const mainPanelSource = readFileSync(
  join(process.cwd(), 'src/main/components/MainPanel.vue'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '')
const sharedCssSource = readFileSync(
  join(process.cwd(), 'shared/app.css'),
  'utf8',
).replace(/\/\*[\s\S]*?\*\//g, '')
const groupChangesSource = readFileSync(
  join(process.cwd(), 'src/main/components/GroupChangesDialog.vue'),
  'utf8',
)

function cssRule(source: string, selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]+)\\}`))
  expect(match, `missing CSS rule for ${selector}`).not.toBeNull()
  return match?.[1] ?? ''
}

describe('document edit modal layout', () => {
  it('keeps the editor body shrinkable so the footer stays inside the box', () => {
    const rule = cssRule(mainPanelSource, '.document-editor')

    // A viewport-unit minimum here is a flex shrink floor that the px-capped
    // `.document-modal` track cannot absorb, so the footer gets pushed out of
    // `.modal-box { overflow: hidden }` on tall viewports and clipped away.
    expect(rule).not.toMatch(/min-height:\s*[\d.]+v(h|min|max)/)
    expect(rule).toMatch(/min-height:\s*0/)
  })

  it('never lets the footer be squeezed out of the track', () => {
    expect(cssRule(sharedCssSource, '.document-modal--edit .modal-ft')).toMatch(/flex-shrink:\s*0/)
  })

  it('leaves the textarea as the sole scroll container', () => {
    // The body clips instead of scrolling, and the textarea fills the track
    // rather than pinning its own height — together that means exactly one
    // scrollbar (the prior double-scrollbar regression).
    expect(cssRule(mainPanelSource, '.document-editor')).toMatch(/overflow:\s*hidden/)

    const textarea = cssRule(mainPanelSource, '.document-editor__textarea')
    expect(textarea).toMatch(/flex:\s*1 1 auto/)
    expect(textarea).toMatch(/min-height:\s*0/)
    expect(textarea).not.toMatch(/(min-)?height:\s*[\d.]+v(h|min|max)/)
  })

  it('bounds the modal height so the box itself defines the track', () => {
    // `100%`, not a vh cap: the same rule serves the full view, which is centred inside
    // `.modal-bg--below-header` (a container already shorter than the viewport).
    expect(cssRule(sharedCssSource, '.document-modal')).toMatch(/height:\s*min\(860px,\s*100%\)/)
  })

  it('uses one shared shell for the editor and group changes dialog', () => {
    const shell = /class="modal-box document-modal document-modal--edit"/
    expect(mainPanelSource).toMatch(shell)
    expect(groupChangesSource).toMatch(shell)
    expect(groupChangesSource).toMatch(/<teleport to="body">/)
    expect(groupChangesSource).not.toMatch(/gcd-(overlay|dialog)/)
    expect(groupChangesSource).not.toMatch(/@click\.self/)

    const modalRuleCount = [mainPanelSource, sharedCssSource, groupChangesSource]
      .map((source) => source.match(/\.document-modal\s*\{/g)?.length ?? 0)
      .reduce((sum, count) => sum + count, 0)
    expect(modalRuleCount).toBe(1)
  })
})
