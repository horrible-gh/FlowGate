import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
const http = vi.hoisted(() => ({ get: vi.fn(), put: vi.fn() }))
vi.mock('@shared/api', async original => ({ ...await original<typeof import('@shared/api')>(), getRequest: http.get, putRequest: http.put }))
import { getMyAiRoles, putMyAiRoles } from '@/features/ai/api'
import AiRoleSelect from '@/features/ai/roles/AiRoleSelect.vue'
import AiRoleDefaultsSection from '@/features/ai/roles/AiRoleDefaultsSection.vue'
import { setCurrentUser } from '@/core/auth/currentUser'
import { prepareInspectorI18n } from '../designer/inspector/support/inspectorSupport'
import { accountRaw, notUsed } from './rolesFixtures.0113'
const i18n = prepareInspectorI18n()
let wrapper: VueWrapper<any> | null = null
beforeEach(() => { vi.clearAllMocks(); i18n.global.locale.value = 'ko'; setCurrentUser({ username: 'one', email: null, isAdmin: false }) })
afterEach(() => { wrapper?.unmount(); wrapper = null })
describe('P-1 account role API and shared controls', () => {
  it('maps unset, count and None without using default-provider APIs', async () => {
    http.get.mockResolvedValue({ data: accountRaw() })
    const answer = await getMyAiRoles()
    expect(http.get).toHaveBeenCalledWith('/api/v1/me/ai-roles')
    expect(answer).toMatchObject({ setupCompleted: false, selectableCount: 1, roles: { instruction: { state: 'unset', value: null } }, options: [{ id: 'a', name: 'None' }] })
  })
  it('writes three object values together and accepts all not_used', async () => {
    http.put.mockResolvedValue({ data: accountRaw(true) })
    expect((await putMyAiRoles(notUsed)).setupCompleted).toBe(true)
    expect(http.put).toHaveBeenCalledWith('/api/v1/me/ai-roles', { roles: { instruction: { kind: 'not_used' }, execution: { kind: 'not_used' }, review: { kind: 'not_used' } } })
    await putMyAiRoles({ ...notUsed, execution: { kind: 'assigned', providerId: 'a' } })
    expect(http.put.mock.calls[1][1].roles.execution).toEqual({ kind: 'assigned', provider_id: 'a' })
  })
  it('keeps deleted identity/name and marks it as missing without auto-selection', async () => {
    const raw = accountRaw(true, 0)
    raw.roles.execution = { state: 'assigned_unavailable', value: { kind: 'assigned', provider_id: 'gone' }, display_name: 'Original', cause: 'deleted' } as any
    http.get.mockResolvedValue({ data: raw })
    expect((await getMyAiRoles()).roles.execution).toMatchObject({ value: { kind: 'assigned', providerId: 'gone' }, displayName: 'Original', cause: 'deleted' })
    wrapper = mount(AiRoleSelect, { props: { modelValue: { kind: 'assigned', providerId: 'gone' }, providers: [], label: 'AI', displayName: 'Original', state: 'assigned_unavailable' }, global: { plugins: [i18n] } })
    expect((wrapper.get('select').element as HTMLSelectElement).value).toBe('assigned:gone')
    expect(wrapper.text()).toContain('Original')
    expect(wrapper.find('[role="img"]').exists()).toBe(true)
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })
  it('uses only a disabled placeholder for unset and returns object values for choices', async () => {
    wrapper = mount(AiRoleSelect, { props: { modelValue: null, providers: [{ id: 'a', name: 'None' }], label: 'AI' }, global: { plugins: [i18n] } })
    expect(wrapper.get('option[value=""]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('option[value="assigned:a"]').text()).toBe('None')
    await wrapper.get('select').setValue('assigned:a')
    await wrapper.get('select').setValue('not_used')
    expect(wrapper.emitted('update:modelValue')).toEqual([[{ kind: 'assigned', providerId: 'a' }], [{ kind: 'not_used' }]])
  })
  it('opening settings only reads; saves only complete values and preserves input on failure', async () => {
    http.get.mockResolvedValue({ data: accountRaw() })
    wrapper = mount(AiRoleDefaultsSection, { global: { plugins: [i18n] } }); await flushPromises()
    expect(wrapper.findAll('select')).toHaveLength(3)
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(http.put).not.toHaveBeenCalled()
    for (const select of wrapper.findAll('select')) await select.setValue('not_used')
    http.put.mockRejectedValue({ response: { status: 409, data: { detail: { code: 'ai_roles_unavailable' } } } })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(wrapper.findAll('select').map(item => (item.element as HTMLSelectElement).value)).toEqual(['not_used','not_used','not_used'])
    expect(wrapper.find('[data-test="inspector-failure-icon"]').exists()).toBe(true)
    http.put.mockResolvedValue({ data: accountRaw(true) })
    await wrapper.get('button').trigger('click'); await flushPromises()
    expect(http.put).toHaveBeenCalledTimes(2)
  })
})
