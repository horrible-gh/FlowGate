// 0126 T#4: component tests use a settings transport fixture; these are not browser F5 evidence.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
const http = vi.hoisted(() => ({ get: vi.fn(), patch: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, patchRequest: http.patch }))
import SettingsTab from '@/features/designer/inspector/features/settings/SettingsTab.vue'
import AiTab from '@/features/designer/inspector/features/ai/AiTab.vue'
import { toInspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { cardSettingsDisabledReason } from '@/features/designer/inspector/features/settings/cardSettingsModel'
import { prepareInspectorI18n } from './support/inspectorSupport'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'

const context = { run_id: 'run_1', graph_revision_no: 1, draft_version: null }
const node: FlowGraphNode = { id:'gn_a', kind:'task', title:'A', docType:'T', x:0, y:0,
  definition:{ nodeType:'document', docType:'T', paletteItemId:null, label:'A', assignee:null, rootBound:false },
  nodeRun:{ status:'review', nodeRunning:false, openedAt:null, finishedAt:null, resultDocId:null } }
const graph: FlowGraph = { flowId:'f', nodes:[node], edges:[], ownership:'graph', viewMode:'revision', viewRevisionNo:1,
  run:{ runId:'run_1', status:'idle', revisionNo:1 } }
const target = (extra: Partial<FlowGraph> = {}, selected = node) => toInspectorTarget({ projectId:'p', graph:{ ...graph,...extra }, node:selected })!
const i18n = prepareInspectorI18n()
let raw: any
let wrapper: VueWrapper<any> | null = null
const field = (value: unknown, choices: unknown[] | null = null) => ({ value, choices, stored:true, default:null })
beforeEach(() => {
  vi.clearAllMocks()
  raw = { card_kind:'run', settings_version:4, context, locked:false, node_running:false,
    settings_capability:{ editable:true, reason:null },
    fields:{ instruction:field('use',['use','omit']),review_count:field(1,[0,1,2,3]) },
    ai_roles:{ options_state:'ok',options:[{id:'a',name:'A'},{id:'b',name:'B'}],roles:Object.fromEntries(
      ['instruction','execution','review'].map(role=>[role,{ selected:{kind:'assigned',provider_id:'a'},state:'assigned_ok',outcome:'ai',provider_id:'a',display_name:'A' }])) } }
  http.get.mockImplementation(async (url: string) => {
    if (url.endsWith('/settings')) return { data:structuredClone(raw) }
    if (url.endsWith('/providers')) return { data:{providers:[]} }
    throw new Error('fixture: no document operation')
  })
  http.patch.mockImplementation(async (_url: string, body: any) => {
    expect(body.expected_context).toEqual(context)
    expect(body.expected_settings_version).toBe(raw.settings_version)
    if (body.field.endsWith('_role')) raw.ai_roles.roles[body.field.replace('_role','')].selected=body.value
    else raw.fields[body.field].value=body.value
    raw.settings_version++
    return { data:structuredClone(raw) }
  })
})
afterEach(() => { wrapper?.unmount();wrapper=null })
async function open(component: any = AiTab, selected = target()) {
  wrapper=mount(component,{ props:{ target:selected },global:{ plugins:[i18n] } })
  await flushPromises()
  return wrapper
}
describe('settings-only current revision capability A1/A2/A5', () => {
  it('keeps global revision protection and distinguishes historical/unconfirmed/conflict context', () => {
    expect(target().readOnly).toBe('revision')
    expect(target().settingsReadOnly).toBeNull()
    expect(target().settingsContext).toEqual(context)
    // flowgate.v02.0130: the view never turns the card's settings off; the server's capability decides.
    expect(target({ viewRevisionNo:0 }).settingsReadOnly).toBeNull()
    expect(target({ run:undefined }).settingsReadOnly).toBeNull()
    expect(target({ run:{runId:'run_1',status:'idle'} }).settingsReadOnly).toBeNull()
    expect(target({ viewMode:'draft' }).readOnly).toBeNull()
    expect(target({ viewMode:'draft' }).settingsReadOnly).toBeUndefined()
    expect(target({ ownership:'conflict' }).settingsReadOnly).toBe('conflict')
    expect(cardSettingsDisabledReason({ readOnly:'revision',settingsReadOnly:null,placing:false,read:true,locked:false,blocked:null })).toBeNull()
    expect(cardSettingsDisabledReason({ readOnly:'revision',placing:false,read:true,locked:false,blocked:null })).toBe('reason.read_only_revision')
  })
  it('writes three roles and count with context/CAS, then rereads on a component remount and reselection', async () => {
    let w=await open()
    for (const role of ['instruction','execution','review']) {
      expect(w.get(`[data-role="${role}"] select`).attributes('disabled')).toBeUndefined()
      await w.get(`[data-role="${role}"] select`).setValue('assigned:b'); await flushPromises()
    }
    await w.get('[data-test="inspector-run-settings-review_count"]').setValue('3'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(4)
    expect(raw.settings_version).toBe(8)
    wrapper!.unmount(); wrapper=null
    w=await open()
    expect((w.get('[data-role="execution"] select').element as HTMLSelectElement).value).toBe('assigned:b')
    expect((w.get('[data-test="inspector-run-settings-review_count"]').element as HTMLSelectElement).value).toBe('3')
    await w.setProps({target:target({}, {...node,id:'gn_b'})}); await flushPromises()
    await w.setProps({target:target()});await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(4)
    expect(http.get).toHaveBeenCalledWith('/api/v1/flows/f/nodes/gn_a/settings',{context:JSON.stringify(context)})
  })
  it('opens instruction settings in an older revision view too (0130: the server decides)', async () => {
    const w=await open(SettingsTab)
    expect(w.get('[data-test="settings-instruction-switch"] button').attributes('disabled')).toBeUndefined()
    await w.setProps({target:target({viewRevisionNo:0})});await flushPromises()
    expect(w.get('[data-test="settings-instruction-switch"] button').attributes('disabled')).toBeUndefined()
    expect(http.patch).not.toHaveBeenCalled()
  })
  it.each([
    ['draft view', { viewMode:'draft', draft:{ version:7 } } as any, { run_id:'run_1', graph_revision_no:null, draft_version:7 }],
    ['older revision view', { viewRevisionNo:0 }, { run_id:'run_1', graph_revision_no:0, draft_version:null }],
    ['newer revision view', { viewRevisionNo:2 }, { run_id:'run_1', graph_revision_no:2, draft_version:null }],
    ['no Canvas run', { run:undefined }, { run_id:null, graph_revision_no:1, draft_version:null }],
  ])('0130 ToD: %s — the AI tab is on and PATCH sends back the context the server answered', async (_label, extra, screen) => {
    const selected=target(extra)
    expect(selected.settingsContext).toEqual(screen)
    const w=await open(AiTab,selected)
    expect(http.get).toHaveBeenCalledWith('/api/v1/flows/f/nodes/gn_a/settings',{context:JSON.stringify(screen)})
    for (const role of ['instruction','execution','review'])
      expect(w.get(`[data-role="${role}"] select`).attributes('disabled')).toBeUndefined()
    await w.get('[data-role="execution"] select').setValue('assigned:b'); await flushPromises()
    await w.get('[data-test="inspector-run-settings-review_count"]').setValue('3'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(2)              // the mock asserts expected_context === the server's context
    expect(raw.fields.review_count.value).toBe(3)
    expect(raw.ai_roles.roles.execution.selected).toEqual({kind:'assigned',provider_id:'b'})
  })
  it('0130: an unconfirmed Canvas run status no longer holds a card the server answered as editable', async () => {
    const w=await open(AiTab,{...target(),editLock:'unknown' as const})
    expect(w.get('[data-role="execution"] select').attributes('disabled')).toBeUndefined()
    await w.get('[data-test="inspector-run-settings-review_count"]').setValue('2'); await flushPromises()
    expect(http.patch).toHaveBeenCalledTimes(1)
  })
  it.each(['locked','nodeRunning','conflict'])('refuses %s writes', async reason => {
    if(reason==='locked') { raw.locked=true; raw.node_running=true; raw.settings_capability={editable:false,reason:'node_settings_locked'} }
    const selected = reason==='nodeRunning' ? {...target(),nodeRun:{...node.nodeRun!,nodeRunning:true}}
      :reason==='conflict' ? target({ownership:'conflict'}):target()
    const w=await open(AiTab,selected)
    expect(w.findAll('[data-role] select').every(item=>item.attributes('disabled')!==undefined)).toBe(true)
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('does not lock an inactive card because another branch runs', async () => {
    const w=await open(AiTab,target({run:{runId:'run_1',status:'running',revisionNo:1,runningExecutionCount:1}}))
    expect(w.get('[data-role="execution"] select').attributes('disabled')).toBeUndefined()
    expect(http.patch).not.toHaveBeenCalled()
  })
  it('test card settingChange uses settings capability while document commands stay revision protected', async () => {
    raw.card_kind='test'
    const selected=target({}, {...node,docType:'TS',definition:{...node.definition!,docType:'TS'}})
    const w=await open(AiTab,selected)
    expect(w.get('[data-role="execution"] select').attributes('disabled')).toBeUndefined()
  })
})
