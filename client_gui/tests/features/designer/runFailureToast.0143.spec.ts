/**
 * flowgate.v02.0143 T#1 (NR flowgate.v02.0143.0003 §4.2, T §2 W1~W2, §3 R8~R10) — the run commands while an automatic Q&A
 * responder answers the open Flow's idle run:
 *
 * - the server's `qa_responding` reaches the run the header reads (the run read `toFlowRun`, the Canvas read's run block
 *   and the Center session's choice between them), absent when an older server does not send it;
 * - `runCommandHold`: only an idle run the server says a responder answers holds 다음 단계 / 전체 실행 (`runHold`); the
 *   phase stays the server's, so 일시정지 / 중지 are never offered for it; an idle reason or a card's question mark alone
 *   never holds (a person's wait stays open); the next run read without it releases at once;
 * - the real Shell (HTTP mocked only, as DesignerTop.0038): the Toolbar's and the Run menu's 다음 단계 / 전체 실행.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick, reactive, toRaw } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import { DesignerShell } from '@/features/designer'
import { createDesignerCenterSession, type DesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import {
  ALL_MENU_TARGETS,
  isMenuCommandEnabled,
  runBandTone,
  runBandView,
  runCommandHold,
  runPhaseOf,
  type DesignerMenuTargets,
} from '@/features/designer/top/designerTop'
import { useProjectStore, type Project } from '@/features/project'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { toFlowRun } from '@/features/project/flowRunApi'
import { createPhaseRunServer, type PhaseRunServer } from './flowRunPhaseServer'
import { flowCanvasResponse } from './flowCanvasResponse'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

const TREE = [
  { id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선', type_code: null, number: null, filename: null, has_md: false, md_path: null },
  { id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', type_code: null, number: '0001', filename: null, has_md: false, md_path: null },
]

class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
const sizeProps = ['offsetWidth', 'offsetHeight'] as const
const originalSize = sizeProps.map((p) => Object.getOwnPropertyDescriptor(HTMLElement.prototype, p))

beforeAll(() => {
  vi.stubGlobal('ResizeObserver', ResizeObserverStub)
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 800 })
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 600 })
})

afterAll(() => {
  vi.unstubAllGlobals()
  sizeProps.forEach((p, i) => {
    if (originalSize[i]) Object.defineProperty(HTMLElement.prototype, p, originalSize[i]!)
    else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[p]
  })
})

let wrapper: VueWrapper<any> | null = null
let runServer: PhaseRunServer = createPhaseRunServer()

function project(id: string, name: string): Project {
  return { id, name, description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] }
}

describe('0143 T-C real Shell publisher and v0.1 fallback', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
    i18n.global.locale.value = 'ko'
    setCurrentUser({ username: 'test', email: null, isAdmin: false })
    runServer = createPhaseRunServer()
    postRequest.mockReset()
    postRequest.mockImplementation(async (url: string, body: Record<string, unknown>) => {
      const answer = runServer.post(url, body)
      if (answer) return answer
      throw new Error(`unexpected request ${url}`)
    })
    getRequest.mockReset()
    getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
      const canvas = /^\/api\/v1\/flows\/([^/]+)\/canvas$/.exec(url)
      if (canvas) {
        const id = decodeURIComponent(canvas[1])
        return { data: { ...flowCanvasResponse(id, 'graph'), run: runServer.runs.get(id) ?? null } }
      }
      if (url === '/api/v1/me/ai-roles') return { data: { setup_completed: true, selectable_count: 0, roles: {} } }
      if (url.endsWith('/archive')) return { data: { archived: false, eligible: false, reason: 'run_not_completed' } }
      const run = runServer.get(url)
      if (run) return run
      if (/^\/api\/v1\/projects\/[^/]+\/groups\/tree$/.test(url)) return { data: { data: { nodes: TREE } } }
      if (/^\/api\/v1\/projects\/[^/]+\/git\/status$/.test(url)) {
        return { data: { ok: true, status: { enabled: true, base_branch: 'v0.2', base_branch_configured: true, slots: [] } } }
      }
      if (url === '/api/v1/ai-invoke/active') return { data: { ok: true, active: false, group_id: params.group_id } }
      throw new Error(`unexpected request ${url}`)
    })
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    setCurrentUser(null)
  })

  async function settle() {
    for (let i = 0; i < 6; i += 1) await flushPromises()
  }

  async function doubleClickRow(w: VueWrapper<any>, label: string) {
    const row = w
      .findAll('[data-explorer-row]')
      .find((r) => r.find('[data-test="project-explorer-label"]').text() === label)!
      .find('.explorer-row')
    for (const [type, detail] of [['click', 1], ['click', 2], ['dblclick', 2]] as const) {
      row.element.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, detail }))
      await nextTick()
    }
    await settle()
  }

  /** The Shell with the Flow open on an idle run the server tells as it is given. */
  async function mountShell(run: Record<string, unknown>) {
    runServer.runs.set('p.ui.0001', {
      run_id: 'run_1', flow_id: 'p.ui.0001', revision_no: 1, status: 'idle', mode: 'full', open_node_keys: ['gn_1'],
      last_event_seq: 17, idle_reason: 'needs_intervention', ...run,
    } as never)
    const pinia = createPinia()
    setActivePinia(pinia)
    useProjectStore().setCurrentProject(project('p', 'Alpha'))
    wrapper = mount(DesignerShell, { attachTo: document.body, global: { plugins: [i18n, pinia] } })
    await settle()
    await doubleClickRow(wrapper, 'UI 개선')
    await doubleClickRow(wrapper, 'Canvas')
    return wrapper
  }

  const runButtons = (w: VueWrapper<any>) =>
    w.findAll('[data-test="designer-tool-run"]').map((b) => [b.attributes('data-command'), b.attributes('disabled') === undefined])

  async function runMenu(w: VueWrapper<any>) {
    await w.get('[data-test="designer-menu"][data-menu="run"]').trigger('click')
    const pop = w.get('[data-test="designer-menu-pop"][data-menu="run"]')
    return Object.fromEntries(pop.findAll('[data-command]').map((item) => [item.attributes('data-command'), item.attributes('disabled') === undefined]))
  }


  const centerOf = (w: VueWrapper<any>) => w.vm.$.setupState.center
  const toastMessages = (w: VueWrapper<any>) => w.vm.$.setupState.shownToasts.map((t:any)=>t.message)
  const failure = (seq=1) => ({kind:'run',code:'mystery',status:503,message:'',approval:null,refusal:null,seq})

  it('C7 same original re-delivery makes only one toast in the same observation', async()=>{
    const w=await mountShell({status:'running'})
    const c=centerOf(w), f='p.ui.0001'
    postRequest.mockRejectedValue({response:{status:503,data:{error:{code:'mystery',message:'opaque'}}}})
    await c.pauseRun(f); await settle()
    const state=reactive(toRaw(c.state.runs[f]))
    const original=state.error
    state.error={...original}; await settle()
    expect(toastMessages(w)).toHaveLength(1)
    state.error=null; await settle()
    state.error=original; await settle()
    expect(toastMessages(w)).toHaveLength(1)
  })
  it('C6 independent same-text pause errors each produce a toast (existing preservation)',async()=>{
    const w=await mountShell({status:'running'})
    const c=centerOf(w), f='p.ui.0001'
    postRequest.mockRejectedValue({response:{status:503,data:{error:{code:'mystery',message:'opaque'}}}})
    await c.pauseRun(f); await settle()
    await c.pauseRun(f); await settle()
    expect(toastMessages(w)).toHaveLength(2)
    expect(c.state.runs[f].run.status).toBe('running')
  })
  it('C1 real pause unknown error says pause and frozen Flow name',async()=>{
    const w=await mountShell({status:'running'})
    const c=centerOf(w), f='p.ui.0001'
    postRequest.mockRejectedValue({response:{status:503,data:{error:{code:'mystery',message:'opaque'}}}})
    await c.pauseRun(f); await settle()
    expect(toastMessages(w)[0]).toContain('일시정지')
    expect(toastMessages(w)[0]).toContain('Canvas')
  })

  it.each([
    ['busy',{kind:'busy'},'이미 실행 중'],
    ['running',{kind:'failed',error:{response:{status:423,data:{code:'GROUP_AI_RUN_LOCKED'}}}},'이미 실행 중'],
    ['disposed',{kind:'failed',error:{response:{status:410,data:{code:'group_disposed'}}}},'폐기된'],
    ['forbidden',{kind:'failed',error:{response:{status:403,data:{}}}},'권한'],
    ['other',{kind:'failed',error:new Error('opaque')},'실행하지 못'],
    ['success',{kind:'done',started:1},null],['cancel',{kind:'cancelled',started:0},null],
  ])('C12 v0.1 %s uses run.next/run.all -> v01RunErrorKey -> menuToast',async(_name,outcome,expected)=>{
    for(const mode of ['next','all']) {
      sessionStorage.clear()
      const w=await mountShell({status:'idle',idle_reason:'completed'})
      const c=centerOf(w)
      vi.spyOn(c,'isV01Flow').mockReturnValue(true)
      const run=vi.spyOn(c,'runV01').mockResolvedValue(outcome)
      await w.get(`[data-test="designer-tool-run"][data-command="run.${mode}"]`).trigger('click');await settle()
      expect(run).toHaveBeenCalledWith('p.ui.0001',mode)
      const messages=toastMessages(w)
      if(expected) {expect(messages).toHaveLength(1);expect(messages[0]).toContain(expected);expect(w.vm.$.setupState.shownToasts[0]).toMatchObject({tone:'danger',duration:4000})}
      else expect(messages).toHaveLength(0)
      wrapper?.unmount();wrapper=null
    }
  })
  it('C4 actual Shell start response loss shows unconfirmed and does not arm result watch',async()=>{
    const w=await mountShell({status:'idle',idle_reason:'completed'})
    const c=centerOf(w)
    postRequest.mockImplementation(async(url:string)=>{
      if(url.endsWith('/runs')) {
        const r=runServer.runs.get('p.ui.0001')!;runServer.runs.set('p.ui.0001',{...r,status:'running'} as never)
        throw new Error('offline')
      }
      throw new Error('unexpected')
    })
    await w.get('[data-test="designer-tool-run"][data-command="run.all"]').trigger('click');await settle()
    expect(c.state.runs['p.ui.0001'].run.status).toBe('running')
    expect(toastMessages(w)).toHaveLength(1);expect(toastMessages(w)[0]).toContain('처리 결과를 확인하지 못했습니다') // replaced below
    expect(Object.keys(w.vm.$.setupState.runResult.pending)).toHaveLength(0)
    expect(postRequest.mock.calls.filter(([url]:any)=>url.endsWith('/runs'))).toHaveLength(1)
  })
  it('C9 frozen target survives rename/inactive switch; round trip retains reappearance policy; X is separate',async()=>{
    const w=await mountShell({status:'running'})
    const c=centerOf(w),f='p.ui.0001'
    postRequest.mockRejectedValue({response:{status:503,data:{error:{code:'mystery',message:'opaque'}}}})
    await c.pauseRun(f);await settle()
    const first=toastMessages(w)[0]
    c.openFlow({projectId:'p',flowId:f,flowName:'Renamed'})
    c.openFlow({projectId:'p',flowId:'p.ui.0002',flowName:'Other'})
    await settle();expect(toastMessages(w)[0]).toBe(first)
    c.activateFlow(f);await settle();expect(toastMessages(w)).toHaveLength(2)
    const id=w.vm.$.setupState.shownToasts[1].id
    w.vm.$.setupState.dismissToast(id);await settle()
    const raw=reactive(toRaw(c.state.runs[f]));raw.error={...raw.error};await settle()
    expect(toastMessages(w)).toHaveLength(1)
  })
  it('C9 late failure while another Flow is active waits for its own target and keeps the requested name',async()=>{
    const w=await mountShell({status:'running'})
    const c=centerOf(w),f='p.ui.0001'
    let reject!:(reason:unknown)=>void
    postRequest.mockReturnValue(new Promise((_resolve,j)=>{reject=j}))
    const action=c.pauseRun(f);await settle()
    c.openFlow({projectId:'p',flowId:'p.ui.0002',flowName:'Other'});await settle()
    reject({response:{status:503,data:{error:{code:'unknown',message:'opaque'}}}})
    await action;await settle()
    expect(toastMessages(w)).toHaveLength(0)
    c.activateFlow(f);await settle()
    expect(toastMessages(w)).toHaveLength(1);expect(toastMessages(w)[0]).toContain('Canvas')
    expect(toastMessages(w)[0]).not.toContain('Other')
  })
  it('C10 actual Shell blocks allowlisted raw text and snapshots contain no response',async()=>{
    const w=await mountShell({status:'running'})
    const secret='Bearer fake-private https://private.test/?token=fake at C:\\private\\file'
    postRequest.mockRejectedValue({response:{status:409,headers:{'x-request-id':'server-id'},data:{error:{code:'graph_run_locked',message:secret,params:{detail:secret}}}},cause:secret})
    await centerOf(w).pauseRun('p.ui.0001');await settle()
    expect(toastMessages(w)).toHaveLength(1)
    expect(JSON.stringify(toastMessages(w))).not.toContain(secret)
    expect(JSON.stringify({...localStorage,...sessionStorage})).not.toContain(secret)
    expect(toastMessages(w)[0]).toContain('편집할 수 없습니다')
  })
})
