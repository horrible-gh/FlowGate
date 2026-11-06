/**
 * flowgate.v02.0053 T#7 — the settings tab on a narrow Inspector (264px in a wide window). jsdom draws no
 * layout, so the drawn result was checked in Chromium (the real Frame and registry, 1348px window, Inspector
 * 264px up to 1084px, ko / en / ja: no overlap, nothing outside the tab, every choice clicked where it is drawn).
 * This keeps the rules that make it so, so they are not lost:
 *
 * - the block title line wraps (title one word, sub fills and wraps, the controls go to the next line);
 * - the requirement 범위 | AI Team and 안전 한도 rows follow the block's width, not the window's (no viewport
 *   media query): two to a line only while both fit whole, never more than two;
 * - the check card's 용도 line wraps its choice under its label when it does not fit beside it;
 * - a control row may take more than one line (`wrap`): the 병합 대상 select with its two switches.
 *
 * flowgate.v02.0056 F2-1: the progress-after-review row (the other `wrap` row) is removed with its setting; the
 * RUN card's 지시 AI row is a one-line select row.
 */
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import InspectorControlRow from '@/features/designer/inspector/parts/InspectorControlRow.vue'

const INSPECTOR = resolve(__dirname, '../../../../../src/features/designer/inspector')
const source = (path: string) => readFileSync(resolve(INSPECTOR, path), 'utf8')
/** The body of the first CSS rule whose selector is exactly `selector`. */
function rule(src: string, selector: string): string {
  const at = src.indexOf(`${selector} {`)
  expect(at, selector).toBeGreaterThan(0)
  return src.slice(at, src.indexOf('}', at))
}

describe('the settings tab on a narrow Inspector (flowgate.v02.0053 T#7)', () => {
  it('a block title line wraps: the title keeps its words, the sub fills the line and wraps', () => {
    const src = source('parts/InspectorBlock.vue')
    expect(rule(src, '.inspector-block-head')).toMatch(/flex-wrap: wrap;/)
    expect(rule(src, '.inspector-block-title')).toMatch(/white-space: nowrap;/)
    expect(rule(src, '.inspector-block-sub')).toMatch(/flex: 1 1 120px;[^}]*min-width: 0;/)
  })

  it('the requirement rows follow the block, two to a line at most, each whole', () => {
    const src = source('features/requirementSettings/RequirementSettings.vue')
    expect(src).not.toMatch(/@media/)
    expect(rule(src, '.inspector-requirement-settings-grid')).toMatch(/display: flex;[^}]*flex-wrap: wrap;/)
    const row = rule(src, '.inspector-requirement-settings-grid > :deep(.inspector-control-row)')
    expect(row).toMatch(/width: max-content;/)
    expect(row).toMatch(/min-width: calc\(50% - 9px\);/)
    expect(row).toMatch(/max-width: 100%;/)
  })

  it('the check card keeps flexible archive/Git boxes and wraps the remote controls', () => {
    const src = source('features/checkSettings/CheckSettings.vue')
    expect(src).not.toContain('inspector-check-settings-usage')
    expect(rule(src, '.inspector-check-settings')).toMatch(/display: flex;[^}]*flex-direction: column;/)
    expect(src).toMatch(/<InspectorControlRow[^>]+wrap>/)
  })

  it('flowgate.v02.0056 F2-1: the settings tab has no wrapping row left; the 지시 AI row is one select', () => {
    const tab = source('features/settings/SettingsTab.vue')
    expect(tab).not.toMatch(/<InspectorControlRow[^>]* wrap>/)
    expect(tab).toMatch(/<InspectorControlRow :label="t\(k\('instruction_ai\.label'\)\)"/)
  })

  it('flowgate.v02test.0002 R0001 #2: the Git box follows the approved HTML, scoped to that block, re-flowing on its own width', () => {
    const src = source('features/checkSettings/CheckSettings.vue')
    const style = src.slice(src.indexOf('<style scoped>'))
    // Box, title line, rows (190 | 290 | switches, 42px / 72px, 10px apart).
    expect(rule(style, '.inspector-check-settings .inspector-check-settings-git')).toMatch(/padding: 16px 19px 18px;[^}]*border-radius: 11px;/)
    const head = rule(style, '.inspector-check-settings-git :deep(.inspector-block-head)')
    expect(head).toMatch(/border-bottom: 1px solid/)
    expect(head).toMatch(/font-size: 17px;/)
    expect(rule(style, '.inspector-check-settings-git :deep(.inspector-control-row)')).toMatch(/grid-template-columns: 190px minmax\(0, 1fr\);[^}]*min-height: 42px;/)
    expect(rule(style, '.inspector-check-settings-git :deep(.inspector-control-row + .inspector-control-row)')).toMatch(/margin-top: 10px;/)
    expect(rule(style, '.inspector-check-settings-git :deep(.inspector-control-row.is-wrap)')).toMatch(/min-height: 72px;/)
    // Scope segment 230 x 42 / 15px in three equal columns (a longer en/ja label widens it instead of being clipped, never
    // past the row); select 233 x 45; refresh 32 x 40 with a 19px icon; switches to the right (280px basis).
    const scope = rule(style, '.inspector-check-settings-git .inspector-check-settings-scope')
    expect(scope).toMatch(/grid-auto-columns: minmax\(0, 1fr\);[^}]*width: max-content;[^}]*min-width: min\(230px, 100%\);[^}]*max-width: 100%;[^}]*height: 42px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-scope :deep(.inspector-segment-option)')).toMatch(/font-size: 15px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-target-group')).toMatch(/flex: 0 0 290px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-target-wrap')).toMatch(/width: 233px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-target')).toMatch(/height: 45px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-refresh')).toMatch(/width: 32px;[^}]*height: 40px;/)
    expect(rule(style, '.inspector-check-settings-git .inspector-check-settings-refresh svg')).toMatch(/width: 19px;[^}]*height: 19px;/)
    expect(rule(style, '.inspector-check-settings-switches')).toMatch(/flex: 1 1 280px;/)
    // Narrow: the box's own width (container queries), never the window's.
    expect(style).not.toMatch(/@media/)
    expect(style).toContain('@container (max-width: 759px)')
    expect(style).toContain('@container (max-width: 480px)')
    // Only a box too narrow for the 230px segment (<= 340px, the Inspector near its minimum) shrinks it: 36px, 13px labels.
    const tight = style.slice(style.indexOf('@container (max-width: 340px)'))
    expect(style).toContain('@container (max-width: 340px)')
    expect(rule(tight, '.inspector-check-settings-git .inspector-check-settings-scope')).toMatch(/min-width: 0;[^}]*height: 36px;/)
    expect(rule(tight, '.inspector-check-settings-git .inspector-check-settings-scope :deep(.inspector-segment-option)')).toMatch(/font-size: 13px;/)
    // The 480px step (labels above their controls) keeps the 230 x 42px segment of the approved HTML's 550px step.
    const narrow = style.slice(style.indexOf('@container (max-width: 480px)'), style.indexOf('@container (max-width: 340px)'))
    expect(rule(narrow, '.inspector-check-settings-switches')).toMatch(/gap: 6px 10px;/)
    expect(narrow).not.toMatch(/inspector-check-settings-scope/)
    // The two switches themselves are not restyled: no Toggle internals are reached from here, and Toggle keeps 40 x 22.
    expect(style).not.toMatch(/ui-toggle/)
    expect(rule(readFileSync(resolve(INSPECTOR, '../../../components/ui/Toggle.vue'), 'utf8'), '.ui-toggle-track')).toMatch(/width: 40px;[^}]*height: 22px;/)
    // The shared parts keep their global look (only this block is scoped).
    expect(rule(source('parts/InspectorBlock.vue'), '.inspector-block')).toMatch(/padding: 10px 12px;[^}]*border-radius: 8px;/)
    expect(rule(source('parts/InspectorControlRow.vue'), '.inspector-control-row')).toMatch(/grid-template-columns: 112px minmax\(0, 1fr\);/)
    expect(rule(source('parts/InspectorSegment.vue'), '.inspector-segment-option')).toMatch(/height: 28px;/)
  })

  it('a control row with `wrap` lets its controls take more lines; without it they stay on one', () => {
    const row = (wrap?: boolean) => mount(InspectorControlRow, { props: { label: 'x', wrap }, slots: { default: () => 'y' } }).get('[data-test="inspector-control-row"]')
    expect(row(true).classes()).toContain('is-wrap')
    expect(row().classes()).not.toContain('is-wrap')
    expect(rule(source('parts/InspectorControlRow.vue'), '.inspector-control-row.is-wrap .inspector-control-value')).toMatch(/flex-wrap: wrap;/)
  })
})
