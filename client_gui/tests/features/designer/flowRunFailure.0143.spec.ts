import { flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi } from 'vitest'
import { reactive } from 'vue'
import { createRunFailurePublisher, runErrorText } from '@/features/designer/shell/runFailureToast'
import { recentRunFailureDiagnostics } from '@/features/designer/center/run/runFailureDiagnostic'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
registerAppMessages(i18n)
i18n.global.locale.value='ko'
const words=(e:any)=>runErrorText(e,(k,v)=>v?i18n.global.t(k,v):i18n.global.t(k),k=>i18n.global.te(k))
import { createFlowRunControl, createFlowRunState, type FlowRunState, type FlowRunControlDeps } from '@/features/designer/center/run/flowRunControl'
import type { FlowGraphSource, FlowRunInfo } from '@/features/designer/center/graph/flowGraphSource'

const info = (status = 'running', runId = 'run_original'): FlowRunInfo => ({runId, status, openNodeKeys: []})
const refused = (status: number | null = 503, code: string | null = 'unfamiliar') => Object.assign(new Error('opaque response'), {status, code, serverRequestId: status === null ? null : 'server_req_1'})
function harness(initial: FlowRunInfo | null = null, project = 'p', flow = 'f', extra: Partial<FlowRunControlDeps> = {}) {
  let current = initial
  let pid = project
  let open = true
  const source: FlowGraphSource = {
    isSample: false, loadGraph: vi.fn(), loadRun: vi.fn(async () => current),
    startRun: vi.fn(async () => info()), pauseRun: vi.fn(async () => info('paused')),
    resumeRun: vi.fn(async () => info()), stopRun: vi.fn(async () => info('stopping')),
  }
  const states = reactive<Record<string, FlowRunState>>({ [flow]: { ...createFlowRunState(),run:initial,readState:'read'} })
  const deps = {source, projectId:()=>pid, isOpen:(p:string)=>open && p===pid, prepare:vi.fn(async()=>true),refresh:vi.fn(),...extra}
  const ctl = createFlowRunControl(deps, states)
  return {ctl,states,source,deps,flow, setCurrent:(r:FlowRunInfo|null)=>{current=r},setProject:(p:string)=>{pid=p},close:()=>{open=false},reopen:()=>{open=true}}
}

describe('0143 T-C baseline and independent failures', () => {
  it.each(['next','all','pause','resume','stop'] as const)('C1 %s retains action and original target after finally', async action => {
    const h=harness(action==='next'||action==='all'?null:info(action==='resume'?'paused':'running'))
    const call= action==='next'||action==='all'?'startRun':`${action}Run` as 'pauseRun'
    vi.mocked(h.source[call]!).mockRejectedValue(refused())
    expect(await (action==='next'||action==='all'?h.ctl.run('f',action):h.ctl[action]('f'))).toBe(false)
    const error=h.states.f.error as any
    expect(h.states.f.action).toBeNull()
    expect(error).toMatchObject({action: action==='next'||action==='all'?'start':action,projectId:'p',flowId:'f',status:503,code:'unfamiliar',serverRequestId:'server_req_1'})
    expect(error.publicationId).toBeTruthy()
    expect(error.targetRunId).toBe(action==='next'||action==='all'?null:'run_original')
  })
  it('C4 response loss rereads running but stays false and uncertain', async()=>{
    const h=harness()
    vi.mocked(h.source.startRun!).mockRejectedValue(refused(null,null))
    h.setCurrent(info())
    expect(await h.ctl.run('f','all')).toBe(false)
    await flushPromises()
    expect(h.source.startRun).toHaveBeenCalledTimes(1)
    expect(h.source.loadRun).toHaveBeenCalledTimes(1)
    expect(h.states.f.run?.status).toBe('running')
    expect(h.states.f.error).toMatchObject({outcome:'unconfirmed',targetRunId:null,baselineRunId:null,stage:'start'})
  })
  it('C6 independent pause 503 twice and stop remain independent while running (existing preservation)',async()=>{
    const h=harness(info())
    vi.mocked(h.source.pauseRun!).mockRejectedValue(refused())
    vi.mocked(h.source.stopRun!).mockRejectedValue(refused())
    await h.ctl.pause('f'); await flushPromises(); const a=h.states.f.error!
    await h.ctl.pause('f'); await flushPromises(); const b=h.states.f.error!
    await h.ctl.stop('f'); await flushPromises(); const c=h.states.f.error!
    expect([a.seq,b.seq,c.seq]).toEqual([1,2,3])
    expect(h.states.f.run?.status).toBe('running')
    expect(h.source.pauseRun).toHaveBeenCalledTimes(2)
    expect(h.source.stopRun).toHaveBeenCalledTimes(1)
  })
})


describe('0143 T-C all required failure boundaries',()=>{
  const actions=['next','all','pause','resume','stop'] as const
  for (const action of actions) {
    it.each([[409,'new_code'],[503,'new_code'],[null,null]] as const)(`C1 ${action} status=%s code=%s retains mode stage and request identity`,async(status,code)=>{
      const start=action==='next'||action==='all'
      const h=harness(start?null:info(action==='resume'?'paused':'running'))
      const call=start?'startRun':`${action}Run` as 'pauseRun'
      vi.mocked(h.source[call]!).mockRejectedValue(refused(status,code))
      const publish=createRunFailurePublisher()
      expect(await(start?h.ctl.run('f',action):h.ctl[action]('f'))).toBe(false)
      await flushPromises()
      const e=h.states.f.error!
      expect(e).toMatchObject({mode:start?action:null,stage:start?'start':action,status,code})
      expect(e.localRequestId).toBeTruthy(); expect(e.scope).toBeTruthy()
      expect(e.localRequestId).not.toBe(e.serverRequestId)
      expect(publish.accept(e)).toBe(true); expect(publish.accept({...e})).toBe(false)
      expect(words(e)).toContain(start?(action==='next'?'다음 단계':'전체 실행'):action==='pause'?'일시정지':action==='resume'?'재개':'중지')
      expect(words(e)).not.toContain('new_code')
    })
  }

  it.each(['before_prepare','save_draft','prepare','revision','target_read'] as const)('C2 failure stage %s never invents an unsent POST',async stage=>{
    const h=harness(stage==='save_draft'||stage==='revision'?info('idle'):null)
    const reject=vi.fn(async()=>{throw refused(409,'revision_refused')})
    if(stage==='before_prepare') Object.assign(h.deps,{beforePrepare:reject})
    if(stage==='prepare') h.deps.prepare=reject
    if(stage==='save_draft'||stage==='revision') {
      Object.assign(h.deps,{hasDraftToConfirm:()=>true,saveDraftBeforeCheck:stage==='save_draft'?reject:async()=>true})
      h.source.checkRevisionAdoption=vi.fn(async()=>refused(409,'revision_refused'))
    }
    if(stage==='target_read') vi.mocked(h.source.loadRun!).mockRejectedValue(refused())
    expect(await(stage==='target_read'?h.ctl.pause('f'):h.ctl.run('f','all'))).toBe(false)
    await flushPromises()
    expect(h.states.f.error).toMatchObject({stage,targetRunId:null,action:stage==='target_read'?'pause':'start'})
    expect(h.source.startRun).not.toHaveBeenCalled(); expect(h.source.pauseRun).not.toHaveBeenCalled()
    expect(words(h.states.f.error)).toContain(stage==='revision'?'revision 평가':stage==='target_read'?'제어 대상 조회':'실행 준비')
  })

  it.each(['before','prepare','save','guard','locked'] as const)('C3 %s false or guard does not emit failure',async part=>{
    const h=harness(part==='save'?info('idle'):part==='locked'?info():null)
    if(part==='before') Object.assign(h.deps,{beforePrepare:async()=>false})
    if(part==='prepare') h.deps.prepare.mockResolvedValue(false)
    if(part==='guard') Object.assign(h.deps,{canProceed:()=>false})
    if(part==='save') { Object.assign(h.deps,{hasDraftToConfirm:()=>true,saveDraftBeforeCheck:async()=>false}); h.source.checkRevisionAdoption=vi.fn() }
    expect(await h.ctl.run('f','next')).toBe(false)
    expect(h.states.f.error).toBeNull(); expect(h.source.startRun).not.toHaveBeenCalled()
  })
  it('C3 revision read outage falls back to actual start success',async()=>{
    const h=harness(info('idle'), 'p','f',{hasDraftToConfirm:()=>true})
    h.source.checkRevisionAdoption=vi.fn(async()=>{throw refused()})
    expect(await h.ctl.run('f','next')).toBe(true)
    expect(h.source.startRun).toHaveBeenCalledTimes(1); expect(h.states.f.error).toBeNull()
  })

  it.each(['failed','null','idle','other','previous_running'] as const)('C5 response loss follow-up %s remains evidence only',async variant=>{
    const h=harness(variant==='previous_running'?info():info('idle'))
    vi.mocked(h.source.pauseRun!).mockRejectedValue(refused(null,null))
    vi.mocked(h.source.startRun!).mockRejectedValue(refused(null,null))
    if(variant==='failed') vi.mocked(h.source.loadRun!).mockRejectedValue(refused(403,'read_failure'))
    else h.setCurrent(variant==='null'?null:variant==='idle'?info('idle'):info('running',variant==='other'?'run_other':'run_original'))
    expect(await(variant==='previous_running'?h.ctl.pause('f'):h.ctl.run('f','all'))).toBe(false)
    await flushPromises()
    const e=h.states.f.error!
    expect(e).toMatchObject({outcome:'unconfirmed',status:null,code:null,serverRequestId:null,seq:1})
    expect(e.targetRunId).toBe(variant==='previous_running'?'run_original':null)
    expect(e.baselineRunId).toBe('run_original')
    expect(e.observation).toEqual(variant==='failed'?{state:'failed',runId:null,status:null}:variant==='null'?{state:'read',runId:null,status:null}:{state:'read',runId:variant==='other'?'run_other':'run_original',status:variant==='idle'?'idle':'running'})
    expect(h.states.f.readState).toBe(variant==='failed'?'failed':'read')
    expect(h.source.startRun).toHaveBeenCalledTimes(variant==='previous_running'?0:1)
    expect(h.source.pauseRun).toHaveBeenCalledTimes(variant==='previous_running'?1:0)
  })
  it('C6 same server ID does not merge two pause failures or another action',async()=>{
    const h=harness(info()),pub=createRunFailurePublisher()
    vi.mocked(h.source.pauseRun!).mockRejectedValue(refused()); vi.mocked(h.source.stopRun!).mockRejectedValue(refused())
    const errors=[]
    for(const action of ['pause','pause','stop'] as const) { await h.ctl[action]('f'); await flushPromises(); errors.push(h.states.f.error!) }
    expect(new Set(errors.map(e=>e.publicationId)).size).toBe(3)
    expect(new Set(errors.map(e=>e.localRequestId)).size).toBe(3)
    expect(new Set(errors.map(e=>e.serverRequestId)).size).toBe(1)
    expect(errors.map(e=>pub.accept(e))).toEqual([true,true,true])
    expect(h.states.f.run?.status).toBe('running')
  })
  it('C7 original proxy clone and follow-up are one publication',async()=>{
    const h=harness(),pub=createRunFailurePublisher()
    vi.mocked(h.source.startRun!).mockRejectedValue(refused())
    await h.ctl.run('f','all'); const original=h.states.f.error!
    expect(pub.accept(original)).toBe(true)
    await flushPromises()
    expect(pub.accept(h.states.f.error!)).toBe(false)
    expect(pub.accept(reactive({...original}))).toBe(false)
  })
  it('C8 equal seq across Project Flow and control instances remains independent; ID-less distinct objects remain independent',async()=>{
    const pub=createRunFailurePublisher(),errors=[]
    for(const [p,f] of [['p','f'],['q','f'],['p','g'],['p','f']]) {
      const h=harness(null,p,f);vi.mocked(h.source.startRun!).mockRejectedValue(refused());await h.ctl.run(f,'all');errors.push(h.states[f].error!)
    }
    expect(errors.map(e=>e.seq)).toEqual([1,1,1,1]);expect(errors.map(e=>pub.accept(e))).toEqual([true,true,true,true])
    const legacy={kind:'run' as const,seq:1,status:null,code:null,message:'',approval:null,refusal:null}
    expect(pub.accept(legacy)).toBe(true);expect(pub.accept(legacy)).toBe(false);expect(pub.accept({...legacy})).toBe(true)
  })

  it.each(['close','reopen','project','late_success','late_read'] as const)('C9 %s cannot affect replacement state or refresh',async variant=>{
    const h=harness(info())
    let done!:(value:FlowRunInfo)=>void,fail!:(e:unknown)=>void
    const pending=new Promise<FlowRunInfo>((r,j)=>{done=r;fail=j})
    if(variant==='late_read') {
      vi.mocked(h.source.loadRun!).mockReturnValue(pending);h.ctl.read('f');h.ctl.forget('f');h.states.f=createFlowRunState();done(info('idle'));await flushPromises()
    } else {
      vi.mocked(h.source.pauseRun!).mockReturnValue(pending);const result=h.ctl.pause('f');await flushPromises()
      if(variant==='project') {h.setProject('q');h.ctl.clear()}
      else {h.close();h.ctl.forget('f')}
      if(variant!=='close'){h.reopen();h.states.f=createFlowRunState()}
      if(variant==='late_success') done(info('paused')); else fail(refused())
      expect(await result).toBe(false);await flushPromises()
    }
    expect(h.states.f?.error??null).toBeNull();expect(h.states.f?.run??null).toBeNull()
    expect(h.deps.refresh).not.toHaveBeenCalled()
  })

  it('C10 raw response/cause/config never enters failure or diagnostics; sink exceptions do not block',async()=>{
    const secret='Bearer fake-secret https://private.test/?token=fake at stack C:\\private\\file'
    const log=vi.spyOn(console,'log'),warn=vi.spyOn(console,'warn'),error=vi.spyOn(console,'error')
    try {
      for(const code of ['unknown','graph_run_locked','graph_git_required','graph_attempt_inactive','graph_hold_done_node','graph_time_machine_blocked','graph_engine_owned']){
        const sink=vi.fn(()=>{throw new Error('sink failed')})
        const h=harness(info(),'p','f',{diagnose:sink})
        vi.mocked(h.source.pauseRun!).mockRejectedValue(Object.assign(new Error(secret),{code,status:409,params:{secret},cause:{secret},serverRequestId:secret,config:{headers:{Authorization:secret}}}))
        expect(await h.ctl.pause('f')).toBe(false);await flushPromises()
        const e=h.states.f.error!
        expect(e.message).toBe('');expect(e.serverRequestId).toBeNull()
        expect(JSON.stringify(e)).not.toContain(secret)
        expect(words(e)).not.toContain(secret)
        expect(JSON.stringify(sink.mock.calls)).not.toContain(secret)
      }
      expect(JSON.stringify([...log.mock.calls,...warn.mock.calls,...error.mock.calls])).not.toContain(secret)
    }finally{log.mockRestore();warn.mockRestore();error.mockRestore()}
  })
  it('C10 default diagnostic buffer is bounded and has only fixed safe fields',async()=>{
    const h=harness(info());vi.mocked(h.source.pauseRun!).mockRejectedValue(refused())
    for(let i=0;i<30;i++) {await h.ctl.pause('f');await flushPromises()}
    const records=recentRunFailureDiagnostics()
    expect(records.length).toBeLessThanOrEqual(50)
    expect(records.at(-1)).toMatchObject({action:'pause',stage:'pause',serverRequestId:'server_req_1',observation:{state:'read',status:'running'}})
    expect(Object.keys(records.at(-1)!)).not.toContain('message')
  })
  it('C11 explicit refusal stays rejected beside subsequent running; background read never creates request failure',async()=>{
    const h=harness(info())
    vi.mocked(h.source.pauseRun!).mockRejectedValue(refused(409,'graph_run_locked'))
    await h.ctl.pause('f');await flushPromises()
    const e=h.states.f.error!
    expect(e.outcome).toBe('rejected');expect(words(e)).toContain('편집할 수 없습니다')
    expect(h.states.f.run?.status).toBe('running')
    vi.mocked(h.source.loadRun!).mockRejectedValue(refused())
    h.ctl.read('f');await flushPromises()
    expect(h.states.f.error?.seq).toBe(e.seq);expect(h.states.f.readState).toBe('failed')
    const clean=harness();vi.mocked(clean.source.loadRun!).mockRejectedValue(refused());clean.ctl.read('f');await flushPromises();expect(clean.states.f.error).toBeNull()
  })
})
