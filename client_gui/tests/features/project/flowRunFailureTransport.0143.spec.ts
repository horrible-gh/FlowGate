import {describe,it,expect,vi} from 'vitest'
import {reactive} from 'vue'
import {flushPromises} from '@vue/test-utils'
const postRequest=vi.hoisted(()=>vi.fn())
const getRequest=vi.hoisted(()=>vi.fn())
vi.mock('@shared/api',()=>({postRequest,getRequest,extractApiErrorMessage:(_:unknown,f:string)=>f}))
import {toGraphError,FlowGraphApiError} from '@/features/project/flowGraphApi'
import {checkFlowRevisionAdoption,fetchCurrentFlowRun,startFlowRun,stopFlowRun,resumeFlowRun} from '@/features/project/flowRunApi'
import {createServerFlowGraphSource} from '@/features/designer/center/graph/serverFlowGraphSource'
import {createFlowRunControl,type FlowRunState} from '@/features/designer/center/run/flowRunControl'
describe('0143 server request identity transport',()=>{
  it.each([
    [{headers:{'x-request-id':'header-1'}},'header-1'],
    [{headers:{'X-Request-ID':'header-2'}},'header-2'],
    [{headers:{get:(key:string)=>key==='x-request-id'?'axios-id':null}},'axios-id'],
    [{headers:{'x-correlation-id':'correlation-id'}},'correlation-id'],
    [{data:{error:{request_id:'envelope-id'}}},'envelope-id'],
    [{data:{request_id:'body-id'}},'body-id'],
    [{headers:{'x-canvas-client-id':'tab-id'}},null],
    [{headers:{'x-request-id':'Bearer fake-secret'}},null],
    [{},null],
  ])('C1 header/envelope source %j -> %s',(response,expected)=>{
    expect(toGraphError({response},'fallback').serverRequestId).toBe(expected)
  })
  it('C2 revision assessment keeps status/code/request ID without double wrapping',async()=>{
    getRequest.mockResolvedValue({headers:{'x-request-id':'assessment-id'},data:{adoptable:false,error:{code:'revision_no',message:'private',status:409}}})
    const e=await checkFlowRevisionAdoption('f')
    expect(e).toMatchObject({status:409,code:'revision_no',serverRequestId:'assessment-id'})
    expect(toGraphError(e,'fallback')).toBe(e)
  })
  it('C1 real default server adapter transports API identity into failure',async()=>{
    postRequest.mockRejectedValue({response:{status:503,headers:{'x-request-id':'real-id'},data:{error:{code:'new_code',message:'private'}}}})
    getRequest.mockResolvedValue({data:{run:null}})
    const source=createServerFlowGraphSource({loadFlowCanvas:vi.fn(),saveFlowCanvasLayout:vi.fn(),loadFlowRun:fetchCurrentFlowRun,startFlowRun,stopFlowRun,resumeFlowRun})
    const states=reactive<Record<string,FlowRunState>>({})
    const ctl=createFlowRunControl({source,projectId:()=> 'p',isOpen:()=>true,prepare:()=>true,refresh:()=>{}},states)
    expect(await ctl.run('f','all')).toBe(false);await flushPromises()
    expect(states.f.error).toMatchObject({status:503,code:'new_code',serverRequestId:'real-id',action:'start',mode:'all',targetRunId:null})
  })
})
