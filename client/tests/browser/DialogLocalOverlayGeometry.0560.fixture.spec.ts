/**
 * flowgate.default.0560 T0018 §2.3-7 / §4-10 — export the two ex-local overlays' real DOM so
 * the companion harness can measure them under the PRODUCTION stylesheet.
 *
 * `WorkPlanAiScopeDialog` and `WorkPlanEditor`'s raw view were two of the four overlays in the
 * whole tree that never used Teleport: both were `position: absolute; inset: 0` inside the
 * WorkPlanEditor panel, so they dimmed that panel and nothing else. L0009 §2 "Teleport" sends
 * every common dialog to one host, so after the migration they cover the viewport. jsdom
 * computes no layout and cannot tell those two apart; this fixture is the export half and
 * `tests/browser/dialog-local-overlay-geometry.0560.mjs` measures the coordinates.
 */
import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, putRequest, postRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  postRequest: vi.fn(),
}))
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>()
  return { ...actual, getRequest, putRequest, postRequest }
})
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))

import WorkPlanAiScopeDialog from '@main/components/WorkPlanAiScopeDialog.vue'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useProjectStore } from '@main/stores/project'
import { resetDialogSystem } from '@main/composables/useDialogStack'

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
]

/** Same shape the work-plan read endpoint returns; see tests/main/WorkPlanEditor.spec.ts. */
const PLAN = {
  ok: true,
  doc_id: 'flowgate.default.0560.0006-WP',
  doc_type: 'WP',
  title: '0560 작업계획',
  group_id: 'flowgate.default.0560',
  parent_doc_id: 'flowgate.default.0560.0001-R',
  status: 'open',
  doc_review_status: 'pending_review',
  revision_no: 1,
  stored_path: 'documents/flowgate/main/default/0560/0006-WP_document.json',
  origin: 'human',
  created_by: 'sjm',
  updated_by: 'sjm',
  updated_at: '2026-09-14T01:00:00+09:00',
  body: {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['D', 'T'],
    quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 1 } },
    provider_candidates: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' }],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
    ],
  },
  registered_providers: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' }],
  provider_status: [],
  assignment_summary: [],
  unassigned_step_count: 3,
  totals: { design_sheets: 1, work_sets: 1, steps: 3 },
  last_application: null,
}

function exportOverlay(): string {
  const overlay = document.querySelector('.fg-dialog-overlay')
  expect(overlay, 'the dialog did not render a common-layer overlay').toBeTruthy()
  return overlay!.outerHTML
}

it('exports the two ex-local overlays for built-CSS geometry', async () => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/document-types')) {
      return Promise.resolve({ data: { data: TYPES, work_plan_countable_types: TYPES } })
    }
    if (String(url).includes('/ai-invoke/providers')) {
      return Promise.resolve({ data: { providers: [], default_provider_id: null } })
    }
    if (String(url).includes('/work-plan')) return Promise.resolve({ data: structuredClone(PLAN) })
    return Promise.resolve({ data: {} })
  })

  const cases: Record<string, string> = {}

  document.body.innerHTML = ''
  mount(WorkPlanAiScopeDialog, {
    props: {
      visible: true,
      countableTypes: [{ code: 'D', label: '기본설계' }, { code: 'T', label: '작업지시' }],
      steps: [{ key: 'T#1', type: 'T', label: '작업지시 1세트', provider_id: null, locked: false }],
      candidates: [{ provider_id: 'p1', display_name: 'Claude Opus' }],
    },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  await flushPromises()
  cases['ai-scope'] = exportOverlay()
  resetDialogSystem()

  document.body.innerHTML = ''
  const editor = mount(WorkPlanEditor, {
    props: { docId: 'flowgate.default.0560.0006-WP', projectId: 'flowgate' },
    global: { plugins: [i18n] },
    attachTo: document.body,
  })
  await flushPromises()
  const rawButton = editor.findAll('.card-hd .card-actions button')
    .find((button) => button.text().includes(i18n.global.t('main.work_plan.raw_view')))
  expect(rawButton, 'the [원문 보기] button is gone').toBeTruthy()
  await rawButton!.trigger('click')
  await flushPromises()
  cases['raw-view'] = exportOverlay()
  resetDialogSystem()

  expect(Object.keys(cases)).toHaveLength(2)

  const scratch = process.env.FLOWGATE_SCRATCH
  if (!scratch) throw new Error('FLOWGATE_SCRATCH is required')
  writeFileSync(
    resolve(scratch, 'dialog-local-overlay-geometry.0560.json'),
    JSON.stringify(cases, null, 2),
    'utf8',
  )
})
