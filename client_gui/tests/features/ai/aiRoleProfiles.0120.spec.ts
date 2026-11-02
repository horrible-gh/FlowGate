import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiRoleMatrix from '@/features/ai/groups/AiRoleMatrix.vue'
import AiGroupsPanel from '@/features/ai/groups/AiGroupsPanel.vue'
import AiGroupEditDialog from '@/features/ai/groups/AiGroupEditDialog.vue'
import * as api from '@/features/ai/api'
import type { AiGroup, AiRoleProfile, AiRoleProfiles } from '@/features/ai/types'

registerAppMessages(i18n)
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const profile = (type_key: string): AiRoleProfile => ({ type_key, position: type_key === 'common' ? 0 : 1,
  roles: Object.fromEntries(['instruction','execution','review'].map(role => [role, { kind: type_key === 'common' ? 'unset' : 'inherit' }])) as AiRoleProfile['roles'] })
const initial = (id = 'roles'): AiRoleProfiles => ({ ai_group_id: id, items: [profile('common')] })
const choices = { types: [{ type_key: 'T', label: '작업' }, { type_key: 'R', label: '요구사항' }],
  providers: [{ provider_id: 'p1', provider_name: 'Model One' }, { provider_id: 'p2', provider_name: 'Model Two' }] }
const team = (id: string, group_type: 'general' | 'role'): AiGroup => ({ ai_group_id: id, group_type, name: id,
  description: 'preserved', enabled: true, is_default: false, members: [], roles: [], supervisor: null,
  member_count: 0, role_count: 0, status: 'empty' })
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason: unknown) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const wrappers: ReturnType<typeof mount>[] = []
function matrix(id = 'roles', canEdit = true) {
  const wrapper = mount(AiRoleMatrix, { props: { groupId: id, canEdit }, global: { plugins: [i18n] } })
  wrappers.push(wrapper); return wrapper
}
let saved: AiRoleProfiles

beforeEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'admin', email: null, isAdmin: true })
  saved = initial()
  vi.spyOn(api, 'getAiRoleProfiles').mockImplementation(async id => ({ ...copy(saved), ai_group_id: id }))
  vi.spyOn(api, 'getAiRoleProfileOptions').mockResolvedValue(copy(choices))
  vi.spyOn(api, 'addAiRoleProfile').mockImplementation(async (_, key) => { saved.items.push(profile(key)); return copy(saved) })
  vi.spyOn(api, 'deleteAiRoleProfile').mockImplementation(async (_, key) => { saved.items = saved.items.filter(row => row.type_key !== key); return copy(saved) })
  vi.spyOn(api, 'setAiRoleProfileCell').mockImplementation(async (_, key, role, value) => {
    saved.items.find(row => row.type_key === key)!.roles[role] = copy(value); return copy(saved)
  })
})
afterEach(async () => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  setCurrentUser(null)
  document.body.innerHTML = ''
  await flushPromises()
})

describe('0120 role matrix mounted behavior', () => {
  it('starts with just common, updates inherited labels, and keeps not_used independent', async () => {
    const wrapper = matrix(); await flushPromises()
    expect(wrapper.findAll('.role-matrix-row')).toHaveLength(1)
    expect(wrapper.find('.remove-kind').exists()).toBe(false)
    await wrapper.get('.add-kind-select').setValue('T'); await flushPromises()
    expect(wrapper.get('[data-type="T"] [data-role="instruction"] option').text()).toBe('공용 · 미설정')
    await wrapper.get('[data-type="T"] [data-role="review"]').setValue('not_used'); await flushPromises()
    await wrapper.get('[data-type="common"] [data-role="instruction"]').setValue('provider:p1'); await flushPromises()
    expect(wrapper.get('[data-type="T"] [data-role="instruction"] option').text()).toBe('공용 · Model One')
    expect((wrapper.get('[data-type="T"] [data-role="instruction"]').element as HTMLSelectElement).value).toBe('inherit')
    expect((wrapper.get('[data-type="T"] [data-role="review"]').element as HTMLSelectElement).value).toBe('not_used')
    expect(api.setAiRoleProfileCell).toHaveBeenLastCalledWith('roles','common','instruction',{ kind: 'assigned', provider_id: 'p1' })
  })

  it('excludes existing types, disables when all are added, and restores a removed candidate', async () => {
    const wrapper = matrix(); await flushPromises()
    await wrapper.get('.add-kind-select').setValue('T'); await flushPromises()
    expect(wrapper.find('.add-kind-select option[value="T"]').exists()).toBe(false)
    await wrapper.get('.add-kind-select').setValue('R'); await flushPromises()
    expect(wrapper.get('.add-kind-select').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-type="T"] .remove-kind').trigger('click'); await flushPromises()
    expect(wrapper.find('.add-kind-select option[value="T"]').exists()).toBe(true)
    expect(wrapper.get('.add-kind-select').attributes('disabled')).toBeUndefined()
  })

  it('restores the last confirmed cell and keeps rows after failed add/delete', async () => {
    saved.items.push(profile('T'))
    vi.mocked(api.setAiRoleProfileCell).mockRejectedValueOnce(new Error('write refused'))
    vi.mocked(api.deleteAiRoleProfile).mockRejectedValueOnce(new Error('delete refused'))
    vi.mocked(api.addAiRoleProfile).mockRejectedValueOnce(new Error('add refused'))
    const wrapper = matrix(); await flushPromises()
    await wrapper.get('[data-type="T"] [data-role="review"]').setValue('provider:p1'); await flushPromises()
    expect((wrapper.get('[data-type="T"] [data-role="review"]').element as HTMLSelectElement).value).toBe('inherit')
    expect(wrapper.get('[role="alert"]').text()).toContain('write refused')
    await wrapper.get('[data-type="T"] .remove-kind').trigger('click'); await flushPromises()
    expect(wrapper.find('[data-type="T"]').exists()).toBe(true)
    await wrapper.get('.add-kind-select').setValue('R'); await flushPromises()
    expect(wrapper.find('[data-type="R"]').exists()).toBe(false)
  })

  it('distinguishes committed write/read failure and retries only GET', async () => {
    const wrapper = matrix(); await flushPromises()
    vi.mocked(api.getAiRoleProfiles).mockRejectedValueOnce(new Error('read failed'))
    await wrapper.get('[data-type="common"] [data-role="review"]').setValue('not_used'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('저장 확인 실패')
    expect((wrapper.get('[data-role="review"]').element as HTMLSelectElement).value).toBe('not_used')
    await wrapper.get('[role="alert"] button').trigger('click'); await flushPromises()
    expect(api.setAiRoleProfileCell).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('ignores a delayed read when the group changes', async () => {
    const pending = deferred<AiRoleProfiles>()
    vi.mocked(api.getAiRoleProfiles).mockImplementationOnce(() => pending.promise)
    const wrapper = matrix('old'); await flushPromises()
    await wrapper.setProps({ groupId: 'new' }); await flushPromises()
    pending.resolve({ ai_group_id: 'old', items: [profile('common'), profile('R')] }); await flushPromises()
    expect(wrapper.find('[data-type="R"]').exists()).toBe(false)
    expect(api.getAiRoleProfiles).toHaveBeenCalledWith('new')
  })

  it('serializes across unmount/remount and ignores a delayed write from the old screen epoch', async () => {
    const pending = deferred<AiRoleProfiles>()
    const first = matrix(); await flushPromises()
    vi.mocked(api.setAiRoleProfileCell).mockImplementationOnce(() => pending.promise)
    await first.get('[data-type="common"] [data-role="review"]').setValue('not_used'); await flushPromises()
    first.unmount(); wrappers.splice(wrappers.indexOf(first), 1)
    const second = matrix(); await flushPromises()
    expect(api.getAiRoleProfiles).toHaveBeenCalledTimes(1)
    saved.items[0].roles.review = { kind: 'not_used' }
    pending.resolve(copy(saved)); await flushPromises()
    expect(api.getAiRoleProfiles).toHaveBeenCalledTimes(2)
    expect((second.get('[data-role="review"]').element as HTMLSelectElement).value).toBe('not_used')
  })

  it('keeps saved unavailable references visible and does not offer them for new assignments', async () => {
    saved.items[0].roles.review = { kind: 'assigned', provider_id: 'deleted', provider_name: 'Old AI', is_available: false, status: 'broken' }
    const wrapper = matrix(); await flushPromises()
    const selected = wrapper.get('[data-role="review"] option[value="provider:deleted"]')
    expect(selected.text()).toBe('Old AI (사용 불가)')
    expect(selected.attributes('disabled')).toBeDefined()
    expect((wrapper.get('[data-role="review"]').element as HTMLSelectElement).value).toBe('provider:deleted')
    expect(api.setAiRoleProfileCell).not.toHaveBeenCalled()
  })

  it('is read-only for a non-administrator', async () => {
    saved.items.push(profile('T'))
    const wrapper = matrix('roles', false); await flushPromises()
    expect(wrapper.findAll('select').every(select => select.attributes('disabled') !== undefined)).toBe(true)
    expect(wrapper.find('.remove-kind').exists()).toBe(false)
  })
})

describe('0120 group integration and preserved dialog contracts', () => {
  it('shows matrix only in role groups and preserves general group controls', async () => {
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: [team('roles','role'),team('general','general')] }, global: { plugins: [i18n] } })
    wrappers.push(wrapper); await flushPromises()
    expect(wrapper.find('.role-matrix').exists()).toBe(true)
    expect(wrapper.find('.block').exists()).toBe(false)
    expect(wrapper.find('.head-actions').exists()).toBe(false)
    await wrapper.findAll('.group-index-item')[1].trigger('click'); await flushPromises()
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    expect(wrapper.findAll('.block')).toHaveLength(2)
    expect(wrapper.find('.head-actions .ui-toggle').exists()).toBe(true)
  })

  it('locks group type in edit and omits hidden description/supervisor fields', async () => {
    const wrapper = mount(AiGroupEditDialog, { props: { open: true, kind: 'group', initial: { name: 'Roles', group_type: 'role', description: 'keep' } }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(wrapper); await flushPromises()
    expect((document.querySelector('#ai-group-edit-type') as HTMLSelectElement).disabled).toBe(true)
    expect(document.querySelector('#ai-group-edit-desc')).toBeNull()
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click(); await flushPromises()
    expect(wrapper.emitted('save')?.[0]).toEqual([{ name: 'Roles', group_type: 'role' }])
  })

  it('disables role type when it already exists, and retains custom role description/criteria', async () => {
    const wrapper = mount(AiGroupEditDialog, { props: { open: true, kind: 'group', roleGroupExists: true }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(wrapper); await flushPromises()
    expect((document.querySelector('#ai-group-edit-type option[value="role"]') as HTMLOptionElement).disabled).toBe(true)
    await wrapper.setProps({ kind: 'role', initial: { name: 'Reviewer', description: 'checks', criteria: ['verification'] } }); await flushPromises()
    expect(document.querySelector('#ai-group-edit-type')).toBeNull()
    expect((document.querySelector('#ai-group-edit-desc') as HTMLTextAreaElement).value).toBe('checks')
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click(); await flushPromises()
    expect(wrapper.emitted('save')?.[0]).toEqual([{ name: 'Reviewer', description: 'checks', criteria: ['verification'] }])
  })

  it('emits changed after deleting the selected general group', async () => {
    vi.spyOn(api, 'deleteAiGroup').mockResolvedValue()
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: [team('general','general'),team('roles','role')] }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await wrapper.get('.head-actions .danger-icon').trigger('click'); await flushPromises()
    ;(document.querySelector('.ui-confirm-actions .ui-btn--danger') as HTMLButtonElement).click(); await flushPromises()
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
  })

  it('does not switch selection after an older group update completes', async () => {
    const pending = deferred<AiGroup>()
    vi.spyOn(api,'setAiGroupState').mockImplementation(() => pending.promise)
    vi.spyOn(api,'listAiGroups').mockResolvedValue([team('general','general'),team('roles','role')])
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: [team('general','general'),team('roles','role')] }, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await wrapper.get('.head-actions .ui-toggle').trigger('click')
    await wrapper.findAll('.group-index-item')[1].trigger('click'); await flushPromises()
    pending.resolve({ ...team('general','general'), enabled: false }); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
    expect(wrapper.find('.role-matrix').exists()).toBe(true)
  })
})
