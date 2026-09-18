// (A) 유지 — 0394 T0016 / NR0003 §6.3.
// 이 파일이 읽는 것은 CSS 선언과, ".document-modal 규칙이 세 시트를 통틀어 한 벌뿐"이라는
// 전역 불변식이다(두 벌이 되는 순간 편집기와 변경사항 창이 조용히 갈라진다). jsdom은 스타일을
// 적용하지 않으므로 어느 쪽도 마운트로는 관찰할 수 없다.
//
// flowgate.default.0560 T0018 (4순위): the editor itself moved off `.document-modal` onto the
// common dialog layer (`DocumentEditDialog.vue` + `dialogs/dialog.css`), so every rule this
// suite guards moved with it and the sources below follow. `GroupChangesDialog.vue` is NOT in
// this T's scope and still uses the legacy `.document-modal` shell out of `shared/app.css`,
// which is why that shell and its single-definition invariant are still checked here — the
// two screens are no longer one shell, and the assertion that used to say they were is
// rewritten to say what is actually true now.
import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

// Comments are stripped so that prose describing the bug (which quotes both
// braces and `vh` declarations) can never satisfy or break an assertion.
function read(relative: string): string {
  return readFileSync(join(process.cwd(), relative), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
}

const mainPanelSource = read('src/main/components/MainPanel.vue')
const editDialogSource = read('src/main/components/DocumentEditDialog.vue')
const dialogCssSource = read('src/main/components/dialogs/dialog.css')
const sharedCssSource = read('shared/app.css')
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
    const rule = cssRule(editDialogSource, '.document-editor')

    // A viewport-unit minimum here is a flex shrink floor that the px-capped surface track
    // cannot absorb, so the footer gets pushed out of `.fg-dialog-surface { overflow: hidden }`
    // on tall viewports and clipped away.
    expect(rule).not.toMatch(/min-height:\s*[\d.]+v(h|min|max)/)
    expect(rule).toMatch(/min-height:\s*0/)
  })

  it('never lets the footer be squeezed out of the track', () => {
    // The guarantee `.document-modal--edit .modal-ft { flex-shrink: 0 }` used to give one
    // screen is now the common layer's, for every dialog on it.
    expect(cssRule(dialogCssSource, '.fg-dialog-footer')).toMatch(/flex-shrink:\s*0/)
    expect(cssRule(dialogCssSource, '.fg-dialog-header')).toMatch(/flex-shrink:\s*0/)
    // The body is the one flexible row, and it can shrink to nothing.
    const body = cssRule(dialogCssSource, '.fg-dialog-body')
    expect(body).toMatch(/flex:\s*1 1 auto/)
    expect(body).toMatch(/min-height:\s*0/)
  })

  it('leaves the textarea as the sole scroll container', () => {
    // The body clips instead of scrolling, and the textarea fills the track
    // rather than pinning its own height — together that means exactly one
    // scrollbar (the prior double-scrollbar regression).
    expect(cssRule(editDialogSource, '.document-editor')).toMatch(/overflow:\s*hidden/)

    const textarea = cssRule(editDialogSource, '.document-editor__textarea')
    expect(textarea).toMatch(/flex:\s*1 1 auto/)
    expect(textarea).toMatch(/min-height:\s*0/)
    expect(textarea).not.toMatch(/(min-)?height:\s*[\d.]+v(h|min|max)/)
  })

  it('bounds the dialog height so the box itself defines the track', () => {
    // The editor is a `sheet` surface; the sheet's own height cap is what bounds the track.
    expect(cssRule(dialogCssSource, '.fg-dialog-surface--sheet')).toMatch(/height:\s*min\(860px,/)
    // `.document-modal` in app.css keeps its container-relative cap (`100%`, not a vh cap,
    // because that shell is centred inside `.modal-bg--below-header`). 0560 T0022 §2.7/§2.8
    // moved its last two users onto the common layer; deleting the now-unused rule is 6·순위's
    // job (NR0005 §13 "중복 scoped CSS 제거"), so the single-definition check below still runs.
    expect(cssRule(sharedCssSource, '.document-modal')).toMatch(/height:\s*min\(860px,\s*100%\)/)
  })

  it('keeps the editor and the changes viewer on the common shell, defined exactly once', () => {
    // The editor is a common dialog now: no hand-built modal box, and its measured width is
    // carried by `surface-class` rather than by a second `.document-modal` variant.
    expect(editDialogSource).toMatch(/<DialogShell/)
    expect(editDialogSource).toMatch(/surface-class="document-edit-dialog"/)
    expect(mainPanelSource).not.toMatch(/class="modal-box document-modal document-modal--edit"/)
    expect(cssRule(editDialogSource, '.fg-dialog-surface.document-edit-dialog'))
      .toMatch(/width:\s*min\(1120px,\s*94vw\)/)

    // 0560 T0022 §2.7: GroupChangesDialog was T0018's one remaining legacy user of this shell
    // and is now a common dialog too - same `surface-class` route for the measured width, no
    // hand-built box, no `<teleport>` of its own, and still no backdrop-close binding.
    expect(groupChangesSource).not.toMatch(/class="modal-box document-modal document-modal--edit"/)
    expect(groupChangesSource).not.toMatch(/<teleport to="body">/)
    expect(groupChangesSource).toMatch(/surface-class="gcd-changes-dialog"/)
    expect(cssRule(groupChangesSource, '.fg-dialog-surface.gcd-changes-dialog'))
      .toMatch(/width:\s*min\(1120px,\s*94vw\)/)
    expect(groupChangesSource).not.toMatch(/@click\.self/)

    const modalRuleCount = [mainPanelSource, sharedCssSource, groupChangesSource, editDialogSource]
      .map((source) => source.match(/\.document-modal\s*\{/g)?.length ?? 0)
      .reduce((sum, count) => sum + count, 0)
    expect(modalRuleCount).toBe(1)
  })
})
