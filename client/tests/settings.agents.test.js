import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AgentsView from '../src/settings/views/system/AgentsView.vue'

const api = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => api)

const agent = (overrides = {}) => ({
  agent_id:'agt_1',name:'Remote one',enabled:true,location:'remote',connection_mode:'agent_pull',
  local_executable_path:null,configured_capabilities:{ai_cli:true,ai_api:false,storage:true},
  reported_capabilities:{ai_cli:true,ai_api:true,storage:true},effective_capabilities:{ai_cli:true,ai_api:false,storage:true},
  registration_status:'unregistered',credential_status:'none',has_registration_history:false,
  enrollment_status:'none',online_status:'offline',last_seen_at:null,agent_version:null,
  protocol_version:null,protocol_compatibility:'unknown',os:null,architecture:null,...overrides,
})
async function mounted(items){
  api.getRequest.mockResolvedValue({data:{agents:items}})
  const wrapper=mount(AgentsView)
  await flushPromises()
  return wrapper
}

describe('AgentsView',()=>{
  beforeEach(()=>{vi.clearAllMocks();vi.stubGlobal('confirm',vi.fn(()=>true))})

  it('shows only Local executable fields for Local and only Agent Pull guidance for Remote',async()=>{
    const wrapper=await mounted([])
    await wrapper.get('[data-testid="agent-new"]').trigger('click')

    const remoteInputs=wrapper.get('[data-testid="remote-fields"]').findAll('input')
    expect(remoteInputs).toHaveLength(1)
    expect(remoteInputs[0].element.value).toBe('Agent Pull')
    await wrapper.get('[data-testid="agent-location"]').setValue('local')
    expect(wrapper.find('[data-testid="local-fields"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="remote-fields"]').exists()).toBe(false)
  })

  it('immediately PATCHes ordinary fields on change',async()=>{
    const original=agent()
    api.patchRequest.mockResolvedValue({data:{...original,enabled:false}})
    const wrapper=await mounted([original])
    await wrapper.get('.agent-row').trigger('click')
    await wrapper.get('[data-testid="agent-enabled"]').setValue(false)
    await flushPromises()
    expect(api.patchRequest).toHaveBeenCalledWith('/api/v1/system/agents/agt_1',{enabled:false})
  })

  it('shows delete only with no registration history',async()=>{
    const wrapper=await mounted([agent()])
    await wrapper.get('.agent-row').trigger('click')
    expect(wrapper.find('[data-testid="delete-agent"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="delete-protected"]').exists()).toBe(false)
  })

  it('hides delete and explains protection after registration history exists',async()=>{
    const wrapper=await mounted([agent({registration_status:'revoked',credential_status:'revoked',has_registration_history:true})])
    await wrapper.get('.agent-row').trigger('click')
    expect(wrapper.find('[data-testid="delete-agent"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="delete-protected"]').text()).toContain('registration history')
  })

  it('keeps enrollment explicit and displays the returned secret once',async()=>{
    const original=agent()
    api.postRequest.mockResolvedValue({data:{agent_id:'agt_1',enrollment_token:'enr_secret',expires_at:'2026-09-27T12:00:00Z'}})
    const wrapper=await mounted([original])
    await wrapper.get('.agent-row').trigger('click')
    await wrapper.get('[data-testid="issue-enrollment"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenCalled()
    expect(api.postRequest).toHaveBeenCalledWith('/api/v1/system/agents/agt_1/enrollments',{})
    expect(wrapper.get('[data-testid="enrollment-secret"]').text()).toContain('enr_secret')
  })

  it('renders Online and runtime metadata supplied by server TTL evaluation',async()=>{
    const wrapper=await mounted([agent({online_status:'online',last_seen_at:'2026-09-27T11:59:30Z',agent_version:'1.2.3',protocol_version:1,protocol_compatibility:'compatible',os:'linux',architecture:'amd64'})])
    await wrapper.get('.agent-row').trigger('click')
    expect(wrapper.get('[data-testid="online-status"]').text()).toBe('Online')
    expect(wrapper.text()).toContain('1.2.3')
    expect(wrapper.text()).toContain('linux / amd64')
    expect(wrapper.text()).toContain('90-second TTL')
  })
})
