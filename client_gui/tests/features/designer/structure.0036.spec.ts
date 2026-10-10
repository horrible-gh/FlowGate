import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import * as DesignerFeature from '@/features/designer'
import { INSPECTOR_TABS } from '@/features/designer/inspector/inspectorTabs'

// flowgate.v02.0036 — source-level guards for the bottom Inspector Frame (0036-R §2.2 / §2.4 /
// §5.2) that a DOM test cannot see: a frame only, with no feature, API or state machine behind it.
// flowgate.v02.0048 T#1 (D#1 §3.3): the guards cover the three Frame files; the rest of the
// Inspector folder (registry, target, host, parts, features) is guarded by structure.0048.
const SRC = resolve(__dirname, '../../../src')
const INSPECTOR = join(SRC, 'features/designer/inspector')
const INSPECTOR_FILES = ['DesignerInspector.vue', 'InspectorHeader.vue', 'inspectorTabs.ts']

const read = (file: string) => readFileSync(file, 'utf8')
const importsOf = (src: string) => [...src.matchAll(/^\s*import\s[^'"]*['"]([^'"]+)['"]/gm)].map((m) => m[1])

describe('features/designer/inspector structure (flowgate.v02.0036)', () => {
  it('keeps the Frame, its header and the tab rules (the folder listing is structure.0048 B1)', () => {
    for (const name of INSPECTOR_FILES) expect(existsSync(join(INSPECTOR, name)), name).toBe(true)
  })

  it('imports only vue / vue-i18n, ui primitives, its own files and the Canvas Node kind / Graph Model contracts', () => {
    const allowed = [
      /^vue$/,
      /^vue-i18n$/,
      /^\.\.\/\.\.\/\.\.\/components\/ui\/[A-Za-z]+\.vue$/,
      /^\.\/[A-Za-z]+(\.vue)?$/,
      /^\.\.\/center\/canvas\/flowNodeKinds$/,
      /^\.\.\/center\/graph\/flowGraph$/,
    ]
    for (const name of INSPECTOR_FILES) {
      for (const spec of importsOf(read(join(INSPECTOR, name)))) {
        expect(allowed.some((re) => re.test(spec)), `${name}: ${spec}`).toBe(true)
      }
    }
  })

  it('keeps the tab rules free of screens', () => {
    expect(importsOf(read(join(INSPECTOR, 'inspectorTabs.ts')))).toEqual([])
  })

  it('owns no store, API, persistence, router, Vue Flow, Center session or feature part', () => {
    for (const name of INSPECTOR_FILES) {
      const src = read(join(INSPECTOR, name))
      for (const spec of importsOf(src)) {
        expect(spec, name).not.toMatch(/^pinia$|^axios$|^vue-router$|^@shared\/|@vue-flow|\/shell\/|designerCenterSession|@\/features\//)
      }
      expect(src, name).not.toMatch(/defineStore|localStorage|sessionStorage|fetch\(|ModelessShell/)
      expect(src, name).not.toMatch(/GitPanel|ConversationPanel|PalettePanel/)
    }
  })

  it('keeps every tab on screen at a narrow width: tabs shrink to the minimum, the strip never wraps', () => {
    const style = read(join(INSPECTOR, 'DesignerInspector.vue')).split('<style scoped>')[1] ?? ''
    const rule = (selector: string) => {
      const at = style.indexOf(`${selector} {`)
      expect(at, selector).toBeGreaterThanOrEqual(0)
      return style.slice(at, style.indexOf('}', at))
    }
    const strip = rule('.designer-inspector-tabs')
    expect(strip).toMatch(/flex-wrap:\s*nowrap;/)
    expect(strip).toMatch(/min-width:\s*0;/)
    // Gap and side padding come from tabStripStyle, not from a CSS literal that could drift.
    expect(strip).not.toMatch(/\bgap:|\bpadding:/)
    const tab = rule('.designer-inspector-tabs :deep(.ui-tab)')
    expect(tab).toMatch(/flex:\s*0 1 auto;/)
    expect(tab).toMatch(/min-width:\s*var\(--inspector-tab-min-width\);/)
    expect(tab).toMatch(/box-sizing:\s*border-box;/)
    expect(tab).toMatch(/text-overflow:\s*ellipsis;/)
    expect(tab).toMatch(/white-space:\s*nowrap;/)
  })

  it('is not part of the features/designer public surface', () => {
    expect(Object.keys(DesignerFeature)).not.toContain('DesignerInspector')
  })

  it.each(['ko', 'en', 'ja'])('i18n %s Inspector keys match the tab list and the Bottom placeholder strings are gone', async (locale) => {
    const messages = (await import(`@/app/i18n/${locale}.ts`)).default as Record<string, any>
    const inspector = messages.designer.inspector
    // flowgate.v02.0048 T#1: no `pane_placeholder` (no '연동 전' place); the header status labels
    // and the common section states of the parts instead.
    // flowgate.v02.0051: the v7 header line (header), the `disabled` pane state and the strings of
    // the common display parts (slot · review · origin · lock · run_settings).
    // flowgate.v02.0051 stage 2: the card parts' common wordings (unconnected · slot_state · menu).
    // flowgate.v02.0052 묶음 A: the header pill's plain words — the workflow states (wf_*), the node run
    // status (run_status) and the pill tooltip (header.state_tip).
    expect(Object.keys(inspector).sort()).toEqual(
      ['label', 'tabs', 'tabs_label', 'doc_status_label', 'run_status_label', 'review_status', 'run_status', 'pane', 'header', 'slot', 'review', 'origin', 'lock', 'run_settings', 'unconnected', 'slot_state', 'menu'].sort(),
    )
    expect(Object.keys(inspector.tabs)).toEqual([...INSPECTOR_TABS])
    expect(Object.keys(inspector.review_status).sort()).toEqual(['approved', 'pending_review', 'rejected', 'revised', 'wf_done', 'wf_in_progress'])
    expect(Object.keys(inspector.run_status).sort()).toEqual(['completed', 'done', 'failed', 'open', 'pending', 'rejected', 'review', 'running', 'stopped'])
    expect(Object.keys(inspector.pane).sort()).toEqual(['details', 'disabled', 'empty', 'error', 'loading', 'no_document', 'no_settings', 'no_settings_for', 'retry'])
    // flowgate.v02.0052 T#2: why the 설정 tab is empty (inspectorNoSettingsKey).
    expect(Object.keys(inspector.pane.no_settings_for).sort()).toEqual(['conflict', 'graph', 'no_slot', 'unassigned'])
    expect(Object.keys(inspector.header).sort()).toEqual(['copied', 'copy_id', 'kind_bug', 'memo', 'memo_unavailable', 'more', 'read_only', 'read_only_conflict', 'read_only_revision', 'state_tip'])
    expect(Object.keys(inspector.slot).sort()).toEqual(['empty', 'open', 'open_hint', 'progress', 'write'])
    expect(Object.keys(inspector.slot_state).sort()).toEqual(['closed', 'in_progress', 'writing'])
    // flowgate.v02.0052 T#3: ■ 중단 / ↻ 재실행 belong to the top 실행 menu, the card keeps 실행 이력 only.
    expect(Object.keys(inspector.menu).sort()).toEqual(['history'])
    expect(Object.keys(inspector.run_settings).sort()).toEqual(['advanced', 'restart', 'review_ai', 'review_basis', 'review_count', 'run_ai', 'time_limit', 'title'])
    expect(messages.designer.shell).not.toHaveProperty('bottom_label')
    expect(messages.designer.shell).not.toHaveProperty('bottom_parts')
    expect(messages.designer.shell.minimap_label).toBe('MINIMAP')
  })
})
