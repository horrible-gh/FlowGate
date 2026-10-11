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
 * - a control row may take more than one line (`wrap`): the 검수 뒤 진행 choice with its status, the 병합 대상
 *   select with its two switches.
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

  it('the check card’s 용도 line puts its choice under the label when it does not fit beside it', () => {
    const src = source('features/checkSettings/CheckSettings.vue')
    expect(rule(src, '.inspector-check-settings-usage')).toMatch(/display: flex;[^}]*flex-wrap: wrap;/)
    expect(rule(src, '.inspector-check-settings-usage-label')).toMatch(/flex: 0 0 100px;/)
    expect(rule(src, '.inspector-check-settings-usage-value')).toMatch(/flex: 1 1 auto;[^}]*flex-wrap: wrap;[^}]*min-width: 0;/)
    expect(src).toMatch(/<InspectorControlRow :label="t\(k\('git\.target'\)\)" :hint="reasonText" wrap>/)
  })

  it('the 검수 뒤 진행 row wraps its status after the choice', () => {
    expect(source('features/settings/SettingsTab.vue')).toMatch(/<InspectorControlRow :label="t\(k\('after_review\.label'\)\)" :hint="t\(k\('after_review\.hint'\)\)" wrap>/)
  })

  it('a control row with `wrap` lets its controls take more lines; without it they stay on one', () => {
    const row = (wrap?: boolean) => mount(InspectorControlRow, { props: { label: 'x', wrap }, slots: { default: () => 'y' } }).get('[data-test="inspector-control-row"]')
    expect(row(true).classes()).toContain('is-wrap')
    expect(row().classes()).not.toContain('is-wrap')
    expect(rule(source('parts/InspectorControlRow.vue'), '.inspector-control-row.is-wrap .inspector-control-value')).toMatch(/flex-wrap: wrap;/)
  })
})
