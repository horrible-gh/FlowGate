import { mount } from '@vue/test-utils'
import { describe,expect,it,vi } from 'vitest'
import SecuritySessionsView from '../src/settings/views/SecuritySessionsView.vue'
vi.mock('@shared/api',()=>({getRequest:vi.fn(async()=>({data:{sessions:[{session_id:'s1',device_label:null,ip_display:null,created_at:'2026-01-01T00:00:00Z',last_used_at:'2026-01-01T00:00:00Z',is_current:true}]}})),deleteRequest:vi.fn(),postRequest:vi.fn()}))
vi.mock('vue-i18n',()=>({useI18n:()=>({locale:{value:'en'}})}))
describe('SecuritySessionsView',()=>{it('renders fallback and hides current revoke button',async()=>{const w=mount(SecuritySessionsView);await new Promise(r=>setTimeout(r,0));expect(w.text()).toContain('Unknown device');expect(w.text()).toContain('Current session');expect(w.findAll('article button')).toHaveLength(0)})})
