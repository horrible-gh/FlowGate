/**
 * flowgate.v02.0074 T#5 — the WP→v0.2 card deploy on the client (D#1 §5 · §6.1 · §3.6.1 · §3.6.4; T#4 API, 0014-TR §2),
 * with `@shared/api` mocked as a small server:
 * - the three wrappers of features/document/api/workPlan.ts: their paths, request bodies and the snake_case → camelCase
 *   answers; the execution sends the preview's baseline exactly as the server sent it (wp_revision_no, plan_digest,
 *   draft_version, base_revision_no and the cleanup's remove_node_keys) and refuses a baseline the client made;
 * - the refusal kinds (403 / 404 / 409 / 422 / 423 GROUP_AI_RUN_LOCKED / 500, the stale baselines, the blockers kept);
 * - the header rules (workPlanDeployActions): the common conditions with their tooltips, and the modes by the counts;
 * - WorkPlanActions: two IconButtons, the [재전개 ▾] items, preview → execution, the cleanup dialog (ModalShell sm, its
 *   counts, the execution with that dialog's preview), no change, blockers, a stale refusal (no run again by itself; the
 *   next click starts from a new preview), aria-busy and one deploy at a time, the failure line, the status read again;
 *   a status read again that fails turns every action off (an earlier answer kept on screen does not count), and a
 *   preview answered after the header went away (another kind of card, the Designer closed) runs nothing.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), patchRequest: vi.fn(), postFormRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

import {
  workPlanDeployExecute,
  workPlanDeployFailureOf,
  workPlanDeployPreview,
  workPlanDeployStatus,
  type WorkPlanDeployPreview,
} from '@/features/document'
import WorkPlanActions from '@/features/designer/inspector/features/workPlan/WorkPlanActions.vue'
import { workPlanDeployActions, type WorkPlanDeployStatusFacts } from '@/features/designer/inspector/features/workPlan/workPlanModel'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { prepareInspectorI18n, SELECTIONS, targetOf } from '../support/inspectorSupport'

const i18n = prepareInspectorI18n()

const WP = 'flowgate.v02.0074.0005-WP'
const BASE = `/api/v1/documents/${encodeURIComponent(WP)}/work-plan/deploy`
const PREVIEW = `${BASE}/preview`

const TARGET: InspectorTarget = { ...targetOf(SELECTIONS.graph), docId: WP, docType: 'WP', cardKind: 'plan', ownership: 'graph', readOnly: null, editLock: null }

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })
const networkError = () => ({ isAxiosError: true, message: 'Network Error', request: {} })

function statusRaw(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    doc_id: WP,
    flow_id: 'flowgate.v02.0074',
    wp_node_key: 'gn_wp',
    ownership: 'graph',
    wp_revision_no: 4,
    deployable: true,
    reason: null,
    deployed_count: 0,
    target_count: 2,
    missing_count: 2,
    orphan_count: 0,
    mismatch_count: 0,
    cards: [],
    modes: { first: true, add: false, cleanup: false },
    ...over,
  }
}

const card = (cardId: string, result: string, over: Record<string, unknown> = {}) => ({
  card_id: cardId,
  doc_type: 'T',
  node_key: result === 'create' ? null : `gn_${cardId}`,
  result,
  reason: null,
  instruction: null,
  instruction_reason: null,
  settings_skipped: [],
  ...over,
})

let previews = 0

function previewRaw(mode: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  previews += 1
  return {
    ok: true,
    doc_id: WP,
    mode,
    flow_id: 'flowgate.v02.0074',
    wp_node_key: 'gn_wp',
    cards: [card('c_t1', 'create'), card('c_t2', 'create')],
    blockers: [],
    writes: true,
    no_change: false,
    executable: true,
    counts: { deployed_count: 0, target_count: 2, missing_count: 2, orphan_count: 0, mismatch_count: 0 },
    baseline: { wp_revision_no: 4, plan_digest: `sha-${previews}`, draft_version: 10 + previews, base_revision_no: 3, remove_node_keys: [] },
    ...over,
  }
}

function executeRaw(mode: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true,
    doc_id: WP,
    mode,
    cards: [card('c_t1', 'create', { node_key: 'gn_a' }), card('c_t2', 'create', { node_key: 'gn_b' })],
    counts: { deployed_count: 2, target_count: 2, missing_count: 0, orphan_count: 0, mismatch_count: 0 },
    no_change: false,
    draft_version: 12,
    created_node_keys: ['gn_a', 'gn_b'],
    removed_node_keys: [],
    key_map: { c_t1: 'gn_a', c_t2: 'gn_b' },
    ...over,
  }
}

let status: Record<string, unknown>
let previewAnswer: (body: Record<string, unknown>) => unknown
let executeAnswer: (body: Record<string, unknown>) => unknown
let wrapper: VueWrapper<any> | null = null

const posts = (path: string) => api.postRequest.mock.calls.filter(([p]) => p === path).map(([, body]) => body as Record<string, unknown>)
const statusReads = () => api.getRequest.mock.calls.filter(([p]) => p === BASE).length

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  previews = 0
  for (const fn of Object.values(api)) fn.mockReset()
  status = statusRaw()
  previewAnswer = (body) => previewRaw(String(body.mode))
  executeAnswer = (body) => executeRaw(String(body.mode))
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === BASE) return { data: JSON.parse(JSON.stringify(status)) }
    throw new Error(`unexpected GET ${path}`)
  })
  api.postRequest.mockImplementation(async (path: string, body: Record<string, unknown>) => {
    if (path === PREVIEW) return { data: await previewAnswer(body) }
    if (path === BASE) return { data: await executeAnswer(body) }
    throw new Error(`unexpected POST ${path}`)
  })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

// --- 1-3. The wrappers ---------------------------------------------------------------------------------

describe('the deploy wrappers (features/document/api/workPlan.ts)', () => {
  it('status: GET …/work-plan/deploy, doc id encoded; counts, cards and modes in camelCase', async () => {
    status = statusRaw({
      deployed_count: 2,
      target_count: 1,
      missing_count: 0,
      orphan_count: 1,
      mismatch_count: 1,
      cards: [{ card_id: 'c_t1', node_key: 'gn_a', doc_type: 'T', valid: true, in_plan: true }, { card_id: 'c_old', node_key: 'gn_b', doc_type: 'N', valid: false, in_plan: false }],
      modes: { first: false, add: true, cleanup: true },
    })
    const read = await workPlanDeployStatus(WP)
    expect(api.getRequest).toHaveBeenCalledWith(BASE)
    expect(api.postRequest).not.toHaveBeenCalled()
    expect(read).toEqual({
      docId: WP,
      flowId: 'flowgate.v02.0074',
      wpNodeKey: 'gn_wp',
      ownership: 'graph',
      wpRevisionNo: 4,
      deployable: true,
      reason: null,
      deployedCount: 2,
      targetCount: 1,
      missingCount: 0,
      orphanCount: 1,
      mismatchCount: 1,
      cards: [{ cardId: 'c_t1', nodeKey: 'gn_a', docType: 'T', valid: true, inPlan: true }, { cardId: 'c_old', nodeKey: 'gn_b', docType: 'N', valid: false, inPlan: false }],
      modes: { first: false, add: true, cleanup: true },
    })
    status = statusRaw({ deployable: false, reason: 'flow_owned_by_v01', ownership: 'v01', modes: { first: false, add: false, cleanup: false } })
    expect(await workPlanDeployStatus(WP)).toMatchObject({ deployable: false, reason: 'flow_owned_by_v01', ownership: 'v01' })
  })

  it('preview: POST …/deploy/preview { mode, wp_revision_no }; cards, blockers and the baseline normalized', async () => {
    previewAnswer = () =>
      previewRaw('cleanup', {
        cards: [
          card('c_t1', 'already_deployed', { instruction: 'instruction_kept', instruction_reason: 'has_body', settings_skipped: [{ field: 'ai', reason: 'unknown_ai' }] }),
          card('c_old', 'remove'),
          card('c_keep', 'kept_orphan', { reason: 'has_history' }),
        ],
        blockers: [{ code: 'deploy_chain_nonlinear', node_keys: ['gn_x'], edge_ids: ['e1'] }, { code: 'deploy_draft_invalid', errors: [{ field: 'nodes', reason: 'too_many_items' }] }],
        executable: false,
        baseline: { wp_revision_no: 4, plan_digest: 'abc', draft_version: 7, base_revision_no: 3, remove_node_keys: ['gn_old'] },
      })
    const preview = await workPlanDeployPreview(WP, 'cleanup', 4)
    expect(api.postRequest).toHaveBeenCalledWith(PREVIEW, { mode: 'cleanup', wp_revision_no: 4 })
    expect(preview.mode).toBe('cleanup')
    expect(preview.cards[0]).toEqual({
      cardId: 'c_t1',
      docType: 'T',
      nodeKey: 'gn_c_t1',
      result: 'already_deployed',
      reason: null,
      instruction: 'instruction_kept',
      instructionReason: 'has_body',
      settingsSkipped: [{ field: 'ai', reason: 'unknown_ai' }],
    })
    expect(preview.cards.map((c) => c.result)).toEqual(['already_deployed', 'remove', 'kept_orphan'])
    expect(preview.blockers.map((b) => [b.code, b.nodeKeys, b.edgeIds, b.errors])).toEqual([
      ['deploy_chain_nonlinear', ['gn_x'], ['e1'], []],
      ['deploy_draft_invalid', [], [], [{ field: 'nodes', reason: 'too_many_items' }]],
    ])
    expect([preview.writes, preview.noChange, preview.executable]).toEqual([true, false, false])
    expect(preview.counts).toEqual({ deployedCount: 0, targetCount: 2, missingCount: 2, orphanCount: 0, mismatchCount: 0 })
    expect(preview.baseline).toEqual({ wpRevisionNo: 4, planDigest: 'abc', draftVersion: 7, baseRevisionNo: 3, removeNodeKeys: ['gn_old'] })
  })

  it('execute: POST …/deploy with the mode and the preview baseline exactly as sent (cleanup: its remove_node_keys); the answer normalized', async () => {
    const sent = { wp_revision_no: 4, plan_digest: 'digest-1', draft_version: 0, base_revision_no: 3, remove_node_keys: ['gn_old', 'gn_older'] }
    previewAnswer = () => previewRaw('cleanup', { baseline: sent })
    executeAnswer = () => executeRaw('cleanup', { removed_node_keys: ['gn_old', 'gn_older'], created_node_keys: [], key_map: {} })
    const preview = await workPlanDeployPreview(WP, 'cleanup', 4)
    const result = await workPlanDeployExecute(WP, preview)
    expect(posts(BASE)).toEqual([{ mode: 'cleanup', ...sent }])
    expect(result).toMatchObject({ mode: 'cleanup', noChange: false, draftVersion: 12, createdNodeKeys: [], removedNodeKeys: ['gn_old', 'gn_older'], keyMap: {} })
    // first: the key map and the cards with their node keys.
    previewAnswer = (body) => previewRaw(String(body.mode))
    executeAnswer = (body) => executeRaw(String(body.mode))
    const first = await workPlanDeployExecute(WP, await workPlanDeployPreview(WP, 'first', 4))
    expect(posts(BASE)[1]).toEqual({ mode: 'first', wp_revision_no: 4, plan_digest: 'sha-2', draft_version: 12, base_revision_no: 3, remove_node_keys: [] })
    expect(first.keyMap).toEqual({ c_t1: 'gn_a', c_t2: 'gn_b' })
    expect(first.cards.map((c) => c.nodeKey)).toEqual(['gn_a', 'gn_b'])
    expect(first.counts.deployedCount).toBe(2)
  })

  it('execute takes only a preview’s own baseline: a copy, an edit or a hand-made one is refused before anything is sent', async () => {
    const preview = await workPlanDeployPreview(WP, 'first', 4)
    expect(Object.isFrozen(preview.baseline)).toBe(true)
    expect(() => {
      ;(preview.baseline as { draftVersion: number | null }).draftVersion = 99
    }).toThrow()
    const forged: WorkPlanDeployPreview = { ...preview, baseline: { ...preview.baseline, draftVersion: 99 } }
    await expect(workPlanDeployExecute(WP, forged)).rejects.toThrow(/baseline of its preview/)
    await expect(
      workPlanDeployExecute(WP, { mode: 'first', baseline: { wpRevisionNo: 4, planDigest: 'x', draftVersion: 1, baseRevisionNo: 0, removeNodeKeys: [] } }),
    ).rejects.toThrow()
    expect(posts(BASE)).toEqual([])
    await workPlanDeployExecute(WP, preview)
    expect(posts(BASE)).toEqual([{ mode: 'first', wp_revision_no: 4, plan_digest: 'sha-1', draft_version: 11, base_revision_no: 3, remove_node_keys: [] }])
  })

  it.each([
    [403, { ok: false, code: 'forbidden', message: 'm' }, 'forbidden', false],
    [404, { ok: false, code: 'wp_not_found', message: 'm' }, 'not_found', false],
    [422, { ok: false, code: 'not_a_work_plan', message: 'm' }, 'not_a_work_plan', false],
    [422, { ok: false, code: 'wp_not_editable', message: 'm', reason: 'final_approved' }, 'not_editable', false],
    [422, { ok: false, code: 'invalid_request', message: 'm', errors: [] }, 'invalid', false],
    [423, { ok: false, code: 'GROUP_AI_RUN_LOCKED', message: 'm' }, 'ai_running', false],
    [423, { detail: { code: 'GROUP_AI_RUN_LOCKED' } }, 'ai_running', false],
    [409, { ok: false, code: 'flow_owned_by_v01', message: 'm' }, 'owned_by_v01', false],
    [409, { ok: false, code: 'flow_ownership_conflict', message: 'm' }, 'ownership_conflict', false],
    [409, { ok: false, code: 'graph_run_locked', message: 'm' }, 'run_locked', false],
    [409, { ok: false, code: 'wp_not_on_card', message: 'm' }, 'not_on_card', false],
    [409, { ok: false, code: 'flow_disposed', message: 'm' }, 'disposed', false],
    [409, { ok: false, code: 'wp_unreadable', message: 'm' }, 'unreadable', false],
    [409, { ok: false, code: 'wp_changed', message: 'm', reason: 'plan_digest' }, 'wp_changed', true],
    [409, { ok: false, code: 'wp_attachment_unreadable', message: 'm', card_id: 'c_t1', reason: 'digest_mismatch' }, 'attachment_unreadable', false],
    [409, { ok: false, code: 'deploy_already_deployed', message: 'm' }, 'already_deployed', false],
    [409, { ok: false, code: 'deploy_not_deployed', message: 'm' }, 'not_deployed', false],
    [409, { ok: false, code: 'draft_version_conflict', message: 'm' }, 'draft_conflict', true],
    [409, { ok: false, code: 'deploy_blocked', message: 'm', blockers: [{ code: 'deploy_draft_invalid', errors: [{ field: 'nodes', reason: 'too_many_items' }] }] }, 'blocked', false],
    [409, { ok: false, code: 'deploy_preview_stale', message: 'm', node_keys: ['gn_x'] }, 'preview_stale', true],
    [409, { ok: false, code: 'instruction_target_changed', message: 'm' }, 'target_changed', false],
    [500, { ok: false, code: 'deploy_restore_incomplete', message: 'm', doc_ids: ['d1', 'd2'] }, 'restore_incomplete', false],
    [500, { ok: false, code: 'internal_error', message: 'm' }, 'other', false],
    [409, { ok: false, code: 'something_new', message: 'm' }, 'other', false],
  ] as const)('a %s %j is %s (stale: %s)', (statusCode, data, kind, stale) => {
    const failure = workPlanDeployFailureOf(axiosError(statusCode, data))
    expect(failure.kind).toBe(kind)
    expect(failure.stale).toBe(stale)
    expect(failure.status).toBe(statusCode)
    if ('message' in data) expect(failure.message).toBe('m')
    if (kind === 'blocked') expect(failure.blockers.map((b) => [b.code, b.errors])).toEqual([['deploy_draft_invalid', [{ field: 'nodes', reason: 'too_many_items' }]]])
    if (kind === 'restore_incomplete') expect(failure.docIds).toEqual(['d1', 'd2'])
    if (kind === 'not_editable') expect(failure.reason).toBe('final_approved')
    if (kind === 'wp_changed') expect(failure.reason).toBe('plan_digest')
    if (kind === 'attachment_unreadable') expect(failure.cardId).toBe('c_t1')
  })

  it('no answer is network', () => {
    expect(workPlanDeployFailureOf(networkError()).kind).toBe('network')
  })
})

// --- 4-5. The header rules -------------------------------------------------------------------------------

const facts = (over: Partial<WorkPlanDeployStatusFacts> = {}): WorkPlanDeployStatusFacts => ({
  ownership: 'graph',
  deployable: true,
  reason: null,
  deployedCount: 0,
  targetCount: 2,
  ...over,
})

describe('workPlanDeployActions (the header rules)', () => {
  it.each([
    ['no document', { target: { ...TARGET, docId: null } }, 'reason.no_document'],
    ['a revision view (read only)', { target: { ...TARGET, readOnly: 'revision' as const } }, 'reason.read_only_revision'],
    ['an ownership conflict (read only)', { target: { ...TARGET, readOnly: 'conflict' as const } }, 'reason.ownership_conflict'],
    ['the run lock', { target: { ...TARGET, editLock: 'run' as const } }, 'reason.run_locked'],
    ['the run state unknown', { target: { ...TARGET, editLock: 'unknown' as const } }, 'reason.run_unknown'],
    ['a v0.1 Flow', { status: facts({ ownership: 'v01' }) }, 'reason.owned_by_v01'],
    ['a conflicting Flow', { status: facts({ ownership: 'conflict' }) }, 'reason.ownership_conflict'],
    ['a plan that cannot be edited', { status: facts({ deployable: false, reason: 'wp_not_editable' }) }, 'reason.not_editable'],
    ['a plan not on a card', { status: facts({ deployable: false, reason: 'wp_not_on_card' }) }, 'reason.not_on_card'],
    ['the Graph run lock (server)', { status: facts({ deployable: false, reason: 'graph_run_locked' }) }, 'reason.run_locked'],
    ['an AI run', { status: facts({ deployable: false, reason: 'GROUP_AI_RUN_LOCKED' }) }, 'reason.ai_running'],
    ['the status read failed', { status: null, statusFailed: true }, 'reason.status_failed'],
    ['the status read again failed (an earlier answer kept)', { status: facts({ deployedCount: 0, targetCount: 2 }), statusFailed: true }, 'reason.status_failed'],
    ['the status read again failed (an earlier deployed answer kept)', { status: facts({ deployedCount: 2, targetCount: 2 }), statusFailed: true }, 'reason.status_failed'],
    ['the status not read yet', { status: null }, 'reason.loading'],
    ['a deploy on its way', { busy: true }, 'reason.busy'],
  ])('%s: all four off with %s', (_name, over, reason) => {
    const actions = workPlanDeployActions({ target: TARGET, status: facts(), statusFailed: false, busy: false, ...over })
    for (const item of Object.values(actions)) expect(item).toEqual({ visible: true, enabled: false, reason })
  })

  it('no deployed card, a target: [전개] only', () => {
    const actions = workPlanDeployActions({ target: TARGET, status: facts({ deployedCount: 0, targetCount: 1 }), statusFailed: false, busy: false })
    expect(actions.deploy.enabled).toBe(true)
    expect([actions.redeploy, actions.add, actions.cleanup].map((item) => [item.enabled, item.reason])).toEqual([
      [false, 'reason.not_deployed'],
      [false, 'reason.not_deployed'],
      [false, 'reason.not_deployed'],
    ])
  })

  it('no deployed card and no target: nothing to deploy', () => {
    const actions = workPlanDeployActions({ target: TARGET, status: facts({ deployedCount: 0, targetCount: 0 }), statusFailed: false, busy: false })
    expect(actions.deploy).toEqual({ visible: true, enabled: false, reason: 'reason.no_targets' })
    expect(actions.redeploy.enabled).toBe(false)
  })

  it('a deployed card: [재전개 ▾] and both items; [전개] off as deployed', () => {
    const actions = workPlanDeployActions({ target: TARGET, status: facts({ deployedCount: 3, targetCount: 2 }), statusFailed: false, busy: false })
    expect(actions.deploy).toEqual({ visible: true, enabled: false, reason: 'reason.deployed' })
    expect([actions.redeploy.enabled, actions.add.enabled, actions.cleanup.enabled]).toEqual([true, true, true])
  })

  it('deployed with every quantity at 0: 「추가분 전개」 off (no target), 「정리 포함 재전개」 on', () => {
    const actions = workPlanDeployActions({ target: TARGET, status: facts({ deployedCount: 2, targetCount: 0 }), statusFailed: false, busy: false })
    expect(actions.redeploy.enabled).toBe(true)
    expect(actions.add).toEqual({ visible: true, enabled: false, reason: 'reason.no_targets' })
    expect(actions.cleanup).toEqual({ visible: true, enabled: true, reason: null })
  })
})

// --- 4, 6-10. WorkPlanActions ------------------------------------------------------------------------------

async function mountActions(target: InspectorTarget = TARGET) {
  wrapper = mount(WorkPlanActions, { props: { target }, global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return wrapper
}

const deployButton = () => wrapper!.get('[data-test="work-plan-deploy"]')
const redeployButton = () => wrapper!.get('[data-test="work-plan-redeploy"]')
const menuItem = (operation: string) => document.body.querySelector<HTMLButtonElement>(`[data-test="work-plan-redeploy-${operation}"]`)
const line = () => wrapper!.find('[data-test="work-plan-deploy-line"]')
const dialog = () => document.body.querySelector<HTMLElement>('[data-test="work-plan-cleanup-dialog"]')

async function openMenu() {
  await redeployButton().trigger('click')
  await flushPromises()
}

async function pickItem(operation: string) {
  if (!menuItem(operation)) await openMenu()
  menuItem(operation)!.click()
  await flushPromises()
}

describe('WorkPlanActions (the header buttons)', () => {
  it('two IconButtons (sm, secondary) like the ⋯ button, no text label; the name in the tooltip and aria-label', async () => {
    await mountActions()
    for (const button of [deployButton(), redeployButton()]) {
      expect(button.classes()).toEqual(expect.arrayContaining(['ui-icon-btn', 'ui-icon-btn--sm', 'ui-icon-btn--secondary']))
      expect(button.text()).not.toMatch(/[가-힣]/)
    }
    expect(deployButton().attributes('title')).toBe('전개')
    expect(deployButton().attributes('aria-label')).toBe('전개')
    expect(deployButton().attributes('disabled')).toBeUndefined()
    expect(redeployButton().attributes('title')).toBe('재전개 — 아직 전개되지 않았습니다')
    expect(redeployButton().attributes('aria-haspopup')).toBe('menu')
    expect(redeployButton().attributes('disabled')).toBeDefined()
    expect(statusReads()).toBe(1)
  })

  it.each([
    ['no document', { docId: null }, null, '작업계획 문서가 없습니다'],
    ['readOnly (revision)', { readOnly: 'revision' as const }, null, '이전 버전을 보는 중입니다'],
    ['editLock', { editLock: 'run' as const }, null, '실행 중이라 전개할 수 없습니다'],
    ['a v0.1 Flow', {}, statusRaw({ ownership: 'v01', deployable: false, reason: 'flow_owned_by_v01' }), 'v0.1이 진행하는 흐름입니다'],
    ['a conflicting Flow', { readOnly: 'conflict' as const, ownership: 'conflict' }, null, '담당이 겹친 흐름입니다'],
    ['a plan that cannot be edited', {}, statusRaw({ deployable: false, reason: 'wp_not_editable' }), '편집할 수 없는 작업계획입니다'],
    ['the status read failed', {}, 'fail', '전개 상태를 읽지 못했습니다'],
  ])('%s: both buttons disabled, the reason in the tooltip', async (_name, over, answer, reason) => {
    if (answer === 'fail') api.getRequest.mockRejectedValue(axiosError(500, { ok: false, code: 'internal_error' }))
    else if (answer) status = answer as Record<string, unknown>
    await mountActions({ ...TARGET, ...over })
    expect(deployButton().attributes('disabled')).toBeDefined()
    expect(redeployButton().attributes('disabled')).toBeDefined()
    expect(deployButton().attributes('title')).toBe(`전개 — ${reason}`)
    expect(redeployButton().attributes('title')).toBe(`재전개 — ${reason}`)
  })

  it('[재전개 ▾] opens its two items, each with its own state and tooltip (no target: 「추가분 전개」 off)', async () => {
    status = statusRaw({ deployed_count: 2, target_count: 0, modes: { first: false, add: false, cleanup: true } })
    await mountActions()
    expect(deployButton().attributes('disabled')).toBeDefined()
    expect(deployButton().attributes('title')).toBe('전개 — 이미 전개되었습니다')
    await openMenu()
    const items = [...document.body.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')]
    expect(items.map((item) => [item.dataset.test, item.textContent?.trim(), item.disabled, item.title])).toEqual([
      ['work-plan-redeploy-add', '추가분 전개', true, '추가분 전개 — 전개할 대상이 없습니다'],
      ['work-plan-redeploy-cleanup', '정리 포함 재전개', false, '정리 포함 재전개'],
    ])
    expect(items.every((item) => item.classList.contains('inspector-header-menu-item'))).toBe(true)
  })

  it('[전개]: a preview of first at the status revision, then the execution with its baseline; no dialog; the status read again', async () => {
    await mountActions()
    await deployButton().trigger('click')
    await flushPromises()
    expect(posts(PREVIEW)).toEqual([{ mode: 'first', wp_revision_no: 4 }])
    expect(posts(BASE)).toEqual([{ mode: 'first', wp_revision_no: 4, plan_digest: 'sha-1', draft_version: 11, base_revision_no: 3, remove_node_keys: [] }])
    expect(dialog()).toBeNull()
    expect(line().text()).toBe('전개했습니다 (추가 2 · 제거 0)')
    expect(statusReads()).toBe(2)
  })

  it('「추가분 전개」: add, no dialog', async () => {
    status = statusRaw({ deployed_count: 1, target_count: 2 })
    await mountActions()
    await pickItem('add')
    expect(posts(PREVIEW)).toEqual([{ mode: 'add', wp_revision_no: 4 }])
    expect(posts(BASE)).toHaveLength(1)
    expect(posts(BASE)[0].mode).toBe('add')
    expect(dialog()).toBeNull()
  })

  it('「정리 포함 재전개」: one ModalShell sm with the remove / keep counts; confirmed, it runs that preview’s baseline and remove list', async () => {
    status = statusRaw({ deployed_count: 3, target_count: 1 })
    previewAnswer = () =>
      previewRaw('cleanup', {
        cards: [card('c_t1', 'already_deployed'), card('c_old', 'remove'), card('c_older', 'remove'), card('c_keep', 'kept_orphan', { reason: 'has_body' })],
        baseline: { wp_revision_no: 4, plan_digest: 'cleanup-digest', draft_version: 21, base_revision_no: 3, remove_node_keys: ['gn_c_old', 'gn_c_older'] },
      })
    executeAnswer = () => executeRaw('cleanup', { created_node_keys: [], removed_node_keys: ['gn_c_old', 'gn_c_older'], key_map: {} })
    await mountActions()
    await pickItem('cleanup')
    expect(posts(PREVIEW)).toEqual([{ mode: 'cleanup', wp_revision_no: 4 }])
    expect(posts(BASE)).toEqual([])
    expect(dialog()?.textContent?.trim()).toBe('카드 2개를 지우고 1개는 남깁니다.')
    expect(document.body.querySelectorAll('.ui-modal-surface')).toHaveLength(1)
    expect(document.body.querySelector('.ui-modal-surface')!.classList.contains('ui-modal-surface--sm')).toBe(true)
    document.body.querySelector<HTMLButtonElement>('[data-test="work-plan-cleanup-confirm"]')!.click()
    await flushPromises()
    expect(posts(PREVIEW)).toHaveLength(1)
    expect(posts(BASE)).toEqual([{ mode: 'cleanup', wp_revision_no: 4, plan_digest: 'cleanup-digest', draft_version: 21, base_revision_no: 3, remove_node_keys: ['gn_c_old', 'gn_c_older'] }])
    expect(dialog()).toBeNull()
    expect(line().text()).toBe('전개했습니다 (추가 0 · 제거 2)')
  })

  it('the cleanup dialog cancelled: nothing runs', async () => {
    status = statusRaw({ deployed_count: 1, target_count: 0 })
    await mountActions()
    await pickItem('cleanup')
    expect(dialog()).not.toBeNull()
    document.body.querySelector<HTMLButtonElement>('[data-test="work-plan-cleanup-cancel"]')!.click()
    await flushPromises()
    expect(dialog()).toBeNull()
    expect(posts(BASE)).toEqual([])
  })

  it('a preview with no change runs nothing and says 「변경 없음」; a blocker runs nothing and is the line', async () => {
    previewAnswer = () => previewRaw('first', { writes: false, no_change: true, executable: false, cards: [] })
    await mountActions()
    await deployButton().trigger('click')
    await flushPromises()
    expect(posts(BASE)).toEqual([])
    expect(line().text()).toBe('변경 없음')
    previewAnswer = () => previewRaw('first', { blockers: [{ code: 'wp_card_has_successor', node_key: 'gn_wp', edge_ids: ['e9'] }], executable: false })
    await deployButton().trigger('click')
    await flushPromises()
    expect(posts(PREVIEW)).toHaveLength(2)
    expect(posts(BASE)).toEqual([])
    expect(line().text()).toBe('작업계획 카드 뒤에 이미 연결이 있습니다')
    expect(line().attributes('data-kind')).toBe('blocked')
  })

  it.each([
    ['draft_version_conflict', '그래프가 먼저 저장되었습니다. 다시 누르세요'],
    ['deploy_preview_stale', '정리 대상이 늘었습니다. 다시 누르세요'],
    ['wp_changed', '작업계획이 바뀌었습니다. 다시 누르세요'],
  ])('a %s refusal: the line says so, nothing runs again by itself; the next click starts from a new preview', async (code, text) => {
    vi.useFakeTimers()
    try {
      let refuse = true
      executeAnswer = (body) => {
        if (refuse) throw axiosError(409, { ok: false, code, message: 'm' })
        return executeRaw(String(body.mode))
      }
      await mountActions()
      await deployButton().trigger('click')
      await flushPromises()
      expect(line().text()).toBe(text)
      expect(line().attributes('data-kind')).toBe('failure')
      await vi.advanceTimersByTimeAsync(10_000)
      await flushPromises()
      expect(posts(PREVIEW)).toHaveLength(1)
      expect(posts(BASE)).toHaveLength(1)
      refuse = false
      await deployButton().trigger('click')
      await flushPromises()
      expect(posts(PREVIEW)).toHaveLength(2)
      // The second execution carries the second preview's baseline, not the refused one.
      expect(posts(BASE).map((body) => [body.plan_digest, body.draft_version])).toEqual([['sha-1', 11], ['sha-2', 12]])
    } finally {
      vi.useRealTimers()
    }
  })

  it('busy: aria-busy and disabled while the preview / execution run; a second click sends nothing; the failure line after', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => (release = resolve))
    previewAnswer = async (body) => {
      await held
      return previewRaw(String(body.mode))
    }
    executeAnswer = () => {
      throw axiosError(423, { ok: false, code: 'GROUP_AI_RUN_LOCKED', message: 'm' })
    }
    await mountActions()
    await deployButton().trigger('click')
    await flushPromises()
    expect(deployButton().attributes('aria-busy')).toBe('true')
    expect(redeployButton().attributes('aria-busy')).toBe('true')
    expect(deployButton().attributes('disabled')).toBeDefined()
    await deployButton().trigger('click')
    await (wrapper!.vm as any).$nextTick()
    expect(posts(PREVIEW)).toHaveLength(1)
    release()
    await flushPromises()
    expect(deployButton().attributes('aria-busy')).toBeUndefined()
    expect(posts(BASE)).toHaveLength(1)
    const reason = line().find('[data-test="inspector-reason"]')
    expect(reason.text()).toBe('AI 실행 중이라 전개하지 못했습니다')
    expect(reason.attributes('data-tone')).toBe('danger')
    // A refusal reads the status again (the counts / locks may have moved); it does not deploy again.
    expect(statusReads()).toBe(2)
  })

  it.each([
    ['a deploy', false],
    ['a refusal', true],
  ])('the status read again fails after %s: every action off with 「전개 상태를 읽지 못했습니다」, the earlier answer not used', async (_name, refuse) => {
    status = statusRaw({ deployed_count: 1, target_count: 2, modes: { first: false, add: true, cleanup: true } })
    if (refuse) {
      executeAnswer = () => {
        throw axiosError(409, { ok: false, code: 'draft_version_conflict', message: 'm' })
      }
    }
    await mountActions()
    expect(redeployButton().attributes('disabled')).toBeUndefined()
    await openMenu()
    expect(menuItem('add')!.disabled).toBe(false)
    expect(menuItem('cleanup')!.disabled).toBe(false)
    // The first read succeeded; the one after the deploy fails.
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === BASE) throw axiosError(500, { ok: false, code: 'internal_error' })
      throw new Error(`unexpected GET ${path}`)
    })
    await pickItem('add')
    expect(posts(BASE)).toHaveLength(1)
    expect(statusReads()).toBe(2)
    for (const [button, name] of [[deployButton(), '전개'], [redeployButton(), '재전개']] as const) {
      expect(button.attributes('disabled')).toBeDefined()
      expect(button.attributes('title')).toBe(`${name} — 전개 상태를 읽지 못했습니다`)
    }
    // The [재전개 ▾] items: off with the same reason (the menu cannot open on a disabled trigger; its items' state).
    const actions = (wrapper!.vm as any).actions as Record<string, { enabled: boolean; reason: string | null }>
    for (const operation of ['deploy', 'redeploy', 'add', 'cleanup']) expect(actions[operation]).toEqual({ visible: true, enabled: false, reason: 'reason.status_failed' })
    // A click on the disabled button sends nothing.
    await deployButton().trigger('click')
    await flushPromises()
    expect(posts(PREVIEW)).toHaveLength(1)
  })

  it.each([
    ['another kind of card selected (the header unmounts)', 'swap'],
    ['the Designer closed', 'unmount'],
  ])('%s while a preview is on its way: its late answer runs nothing', async (_name, how) => {
    let release!: () => void
    const held = new Promise<void>((resolve) => (release = resolve))
    previewAnswer = async (body) => {
      await held
      return previewRaw(String(body.mode))
    }
    const Host = defineComponent({
      props: { plan: { type: Boolean, default: true } },
      setup: (props) => () => (props.plan ? h(WorkPlanActions, { target: TARGET }) : h('p', { 'data-test': 'other-card' }, 'other')),
    })
    wrapper = mount(Host, { props: { plan: true }, global: { plugins: [i18n] }, attachTo: document.body })
    await flushPromises()
    await deployButton().trigger('click')
    await flushPromises()
    expect(posts(PREVIEW)).toHaveLength(1)
    if (how === 'swap') {
      await wrapper.setProps({ plan: false })
      expect(wrapper.find('[data-test="work-plan-deploy"]').exists()).toBe(false)
    } else {
      wrapper.unmount()
      wrapper = null
    }
    release()
    await flushPromises()
    expect(posts(BASE)).toEqual([])
    expect(statusReads()).toBe(1)
  })

  it('another card selected while a deploy runs: the late answer is dropped', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => (release = resolve))
    previewAnswer = async (body) => {
      await held
      return previewRaw(String(body.mode))
    }
    await mountActions()
    await deployButton().trigger('click')
    await flushPromises()
    await wrapper!.setProps({ target: { ...TARGET, docId: 'flowgate.v02.0074.0099-WP' } })
    release()
    await flushPromises()
    expect(posts(BASE)).toEqual([])
    expect(line().exists()).toBe(false)
  })
})
