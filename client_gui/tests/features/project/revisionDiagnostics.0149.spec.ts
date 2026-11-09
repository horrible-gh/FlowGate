import { describe, it, expect, vi, beforeEach } from 'vitest'
import { reactive, defineComponent, h } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({getRequest, postRequest, extractApiErrorMessage: (_: unknown, f: string) => f}))
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ko from '@/app/i18n/ko'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import { checkFlowRevisionAdoption, fetchCurrentFlowRun, startFlowRun, stopFlowRun, resumeFlowRun } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource } from '@/features/designer/center/graph/serverFlowGraphSource'
import { createFlowRunControl, createFlowRunState, type FlowRunState } from '@/features/designer/center/run/flowRunControl'
import { createRunFailurePublisher, runErrorText } from '@/features/designer/shell/runFailureToast'
import { recentRunFailureDiagnostics } from '@/features/designer/center/run/runFailureDiagnostic'
registerAppMessages(i18n)
const labels: Record<string, string> = {K1: '검수 카드', K2: '진행 카드', K3: '열린 카드', absent: '추정하면 안 되는 카드'}
const words = (e: any, lookup = (k: string) => labels[k] ?? null) => runErrorText(e,
  (k,v) => v ? i18n.global.t(k,v) : i18n.global.t(k), k => i18n.global.te(k), lookup)
function harness(params: unknown, code = 'graph_revision_migration_blocked') {
  getRequest.mockImplementation(async (url: string) => url.endsWith('/revision-adoption')
    ? {data: {adoptable: false, error: {status: 409, code, message: 'PRIVATE_SERVER_MESSAGE', params}}}
    : {data: {run: {run_id: 'run', flow_id: 'f', revision_no: 1, status: 'idle', open_node_keys: [], idle_reason: 'needs_intervention'}}})
  const source = createServerFlowGraphSource({loadFlowCanvas: vi.fn(), saveFlowCanvasLayout: vi.fn(),
    loadFlowRun: fetchCurrentFlowRun, startFlowRun, stopFlowRun, resumeFlowRun, checkFlowRevisionAdoption})
  const states = reactive<Record<string, FlowRunState>>({f: {...createFlowRunState(), readState: 'read', run: {runId: 'run', status: 'idle', openNodeKeys: []}}})
  const prepare = vi.fn(() => true)
  const ctl = createFlowRunControl({source, projectId: () => 'p', isOpen: () => true, prepare, refresh: vi.fn()}, states)
  return {ctl, states, prepare}
}
const blocker = (node_key: string, row_status: string | null, active_attempt: boolean) => ({node_key,row_status,active_attempt})
const params = (node_keys: unknown[], blockers: unknown[] = [], extra: object = {}) => ({reason: 'active_attempts', node_keys, blockers, node_keys_total: node_keys.length, ...extra})
async function fail(p: unknown, code?: string) {
  const h = harness(p, code)
  expect(await h.ctl.run('f','all')).toBe(false)
  await flushPromises()
  expect(postRequest).not.toHaveBeenCalled()
  expect(h.prepare).not.toHaveBeenCalled()
  expect(h.states.f.error).toMatchObject({kind:'run', message:'', stage:'revision', outcome:'rejected'})
  return h.states.f.error!
}
beforeEach(() => { getRequest.mockReset(); postRequest.mockReset(); i18n.global.locale.value = 'ko' })

describe('0149 T-A1 transmission -> assessment -> control -> toast', () => {
  it('2f review without attempt and active open/pending attempts name exactly the server cards', async () => {
    const review = words(await fail(params(['K1'], [blocker('K1','review',false)])))
    expect(review).toContain('검수 카드 (활성 실행 없이 검수 상태인 카드)')
    expect(review).not.toContain('진행 카드')
    for (const status of ['open','pending']) {
      const active = words(await fail(params(['K2'], [blocker('K2',status,true)])))
      expect(active).toContain('진행 카드 (활성 실행이 있는 카드)')
      expect(active).not.toBe(review)
    }
    const mixed = words(await fail(params(['K1','K2','K3'], [blocker('K1','review',false),blocker('K2','open',true),blocker('K3','open',false)])))
    expect(mixed).toContain('검수 카드'); expect(mixed).toContain('진행 카드'); expect(mixed).toContain('열린 카드')
    expect(mixed).not.toContain(labels.absent)
  })
  it('2f truncation uses server total, fallback uses the key, private identities are discarded everywhere', async () => {
    const e = await fail(params(['K1','K2','missing','K3'], [], {node_keys_total:55, jobs:[{job_id:'PRIVATE_JOB'}], decision_ids:['PRIVATE_DECISION'], attempt_id:'PRIVATE_ATTEMPT', token:'PRIVATE_TOKEN', doc_id:'PRIVATE_DOC'}))
    const text = words(e)
    expect(text).toContain('missing'); expect(text).toContain('그 밖 52개'); expect(text).not.toContain('열린 카드')
    expect(e.blocked).toEqual({reason:'active_attempts',nodeKeys:['K1','K2','missing','K3'],blockers:[],total:55})
    const records = recentRunFailureDiagnostics().filter(r => r.publicationId === e.publicationId)
    expect(records.length).toBeGreaterThan(0)
    expect(records.every(r => JSON.stringify(r.blocked) === JSON.stringify(e.blocked))).toBe(true)
    expect(JSON.stringify({e, text, records})).not.toContain('PRIVATE_')
  })
  it.each([undefined, {}, {reason:'active_attempts'}, params([])])('2g absent/empty keys never invent a target (%j)', async p => {
    const text = words(await fail(p))
    expect(text).toContain('revision 평가'); expect(text).not.toContain('대상 카드:'); expect(text).not.toContain('대상 없음')
    expect(text).not.toContain(labels.K1)
  })
  it('2h validates key type, length, uniqueness, count, membership and blocker fields', async () => {
    const e = await fail(params([null, 1, {}, '', 'x'.repeat(129), 'Bearer private', 'token_private', 'K1', 'K1', ...Array.from({length:70},(_,i)=>`K${i+2}`)], [
      null, [], {node_key:'K1',row_status:'review',active_attempt:'false'}, blocker('K1','review',false),
      blocker('K1','open',true), blocker('absent','review',false), blocker('K2',null,true),
      {node_key:'K3',row_status:{bad:true},active_attempt:true}, blocker('K4','x'.repeat(65),true),
    ], {reason:'bad reason',node_keys_total:-1}))
    expect(e.blocked?.nodeKeys).toHaveLength(50)
    expect(e.blocked?.nodeKeys.slice(0,2)).toEqual(['K1','K2'])
    expect(e.blocked?.blockers).toEqual([{nodeKey:'K1',rowStatus:'review',activeAttempt:false},{nodeKey:'K2',rowStatus:null,activeAttempt:true}])
    expect(e.blocked?.total).toBeNull(); expect(e.blocked?.reason).toBeNull()
    expect(() => words(e)).not.toThrow()
    expect(JSON.stringify(e)).not.toContain('Bearer')
  })
  it.each([-1, 1.5, 0, '55', null, Infinity, Number.MAX_SAFE_INTEGER + 1])('2h invalid total %s becomes null', async total => {
    expect((await fail(params(['K1'], [], {node_keys_total:total}))).blocked?.total).toBeNull()
  })
  it.each(['gate_rejected','gate_recovery_reset_required','gate_jobs_in_progress'])('2i existing gate reason %s retains named cards', async reason => {
    const e = await fail({reason,node_keys:['K1'],jobs:[{job_id:'PRIVATE_JOB'}],decision_ids:['PRIVATE_DECISION']})
    expect(words(e)).toContain('검수 카드'); expect(words(e)).not.toContain('PRIVATE_')
    expect(words(e)).not.toContain(`blocked_reason_${reason}`)
  })
  it.each(['graph_node_protected','graph_node_key_reused'])('2i %s uses the same path', async code => {
    const e = await fail({node_keys:['K1']}, code)
    expect(e.blocked?.nodeKeys).toEqual(['K1']); expect(words(e)).toContain('검수 카드')
  })
  it('unrelated errors discard the entire params object', async () => {
    expect((await fail(params(['K1']), 'run_already_active')).blocked).toBeNull()
  })
  it('card labels are bounded and inserted as text', async () => {
    const e = await fail(params(['K1']))
    const text = words(e, () => '<img src=x onerror=alert(1)>' + 'a'.repeat(100))
    const w = mount(defineComponent({render: () => h('div', text)}))
    expect(w.find('img').exists()).toBe(false)
    expect(w.text()).toContain('<img src=x onerror=alert(1)>')
    expect(w.text()).not.toContain('a'.repeat(61)); w.unmount()
  })
  it('A-5 recreated control, re-entry and repeated clicks preserve refusal and publisher rules', async () => {
    const p = params(['K1'], [blocker('K1','review',false)])
    const h = harness(p), publisher = createRunFailurePublisher()
    const texts: string[] = []
    for (let i=0;i<3;i++) {
      expect(await h.ctl.run('f','all')).toBe(false); await flushPromises()
      const e=h.states.f.error!; texts.push(words(e))
      expect(publisher.accept(e)).toBe(true); expect(publisher.accept({...e})).toBe(false)
    }
    const recreated = await fail(p)
    expect(words(recreated)).toBe(texts[0]); expect(new Set(texts).size).toBe(1)
    publisher.clear(); expect(publisher.accept(recreated)).toBe(true)
    expect(postRequest).not.toHaveBeenCalled()
  })
  it('ko/en/ja have identical new keys, no remediation promises, and translated refusal text', async () => {
    const tables = [ko,en,ja].map(m => m.designer.top.run_errors)
    const keys = Object.keys(tables[0]).filter(k => k.startsWith('blocked_') || ['graph_revision_migration_blocked','graph_node_protected','graph_node_key_reused'].includes(k)).sort()
    expect(keys).toHaveLength(12)
    for (const table of tables) {
      expect(Object.keys(table).filter(k => k.startsWith('blocked_') || ['graph_revision_migration_blocked','graph_node_protected','graph_node_key_reused'].includes(k)).sort()).toEqual(keys)
      const text = keys.map(k => (table as Record<string,string>)[k]).join(' ')
      // 0156 TR5: the Graph Time Machine recovery guidance is the required wording (0012 TR4 D1); only other promises stay forbidden.
      expect(text).not.toMatch(/Stop|재검수|중지|re-review|再検証|停止/i)
    }
    const e = await fail(params(['K1'], [blocker('K1','review',false)]))
    for (const locale of ['ko','en','ja'] as const) {
      i18n.global.locale.value = locale
      expect(words(e)).toContain('검수 카드'); expect(words(e)).not.toContain('gui.designer.top.')
    }
  })
})
