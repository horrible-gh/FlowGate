import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import AiSettingsView from '@/features/ai/AiSettingsView.vue'
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
const choices = { types: [{ type_key: 'T', label: '작업' }, { type_key: 'D', label: '기본 설계' }],
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
    await wrapper.get('.add-kind-select').setValue('D'); await flushPromises()
    expect(wrapper.get('.add-kind-select').attributes('disabled')).toBeDefined()
    await wrapper.get('[data-type="T"] .remove-kind').trigger('click'); await flushPromises()
    expect(wrapper.find('.add-kind-select option[value="T"]').exists()).toBe(true)
    expect(wrapper.get('.add-kind-select').attributes('disabled')).toBeUndefined()
  })

  it.each(['ko','ja','en'] as const)('uses the palette names and order without confirmation or other documents (%s)', async language => {
    const { DEFAULT_PALETTE_ITEMS } = await import('@/features/designer/palette/paletteCatalog')
    i18n.global.locale.value = language
    const documentCards = DEFAULT_PALETTE_ITEMS.filter(item => item.docType)
    vi.mocked(api.getAiRoleProfileOptions).mockResolvedValue({
      ...choices,
      types: [...documentCards].reverse().map(item => ({ type_key: item.docType!, label: 'catalog label' }))
        .concat([{ type_key: 'R', label: 'Requirements' }, { type_key: 'confirm', label: 'Confirm' }, { type_key: 'TR', label: 'Report' }]),
    })
    const wrapper = matrix(); await flushPromises()
    const offered = wrapper.findAll('.add-kind-select option').slice(1)
    expect(offered.map(option => option.attributes('value'))).toEqual(documentCards.map(item => item.docType))
    expect(offered.map(option => option.text())).toEqual(documentCards.map(item => i18n.global.t(`gui.designer.palette.items.${item.labelKey}`)))
    await wrapper.get('.add-kind-select').setValue('D'); await flushPromises()
    expect(wrapper.get('[data-type="D"] .role-kind strong').text()).toBe(i18n.global.t('gui.designer.palette.items.basic_design'))
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
    await wrapper.get('.add-kind-select').setValue('D'); await flushPromises()
    expect(wrapper.find('[data-type="D"]').exists()).toBe(false)
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
    await wrapper.findAll('.group-index-item')[0].trigger('click'); await flushPromises()
    await wrapper.get('.head-actions .danger-icon').trigger('click'); await flushPromises()
    ;(document.querySelector('.ui-confirm-actions .ui-btn--danger') as HTMLButtonElement).click(); await flushPromises()
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
  })

  it.each([false, true])('retains a completed OFF update after selecting another group (reload fails=%s)', async reloadFails => {
    const pending = deferred<AiGroup>()
    const refreshed = deferred<AiGroup[]>()
    const updated = { ...team('general','general'), enabled: false }
    vi.spyOn(api,'setAiGroupState').mockReturnValue(pending.promise)
    const list = vi.spyOn(api,'listAiGroups').mockReturnValue(refreshed.promise)
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: [team('general','general'),team('roles','role')] }, global: { plugins: [i18n] } })
    wrappers.push(wrapper)
    await wrapper.findAll('.group-index-item')[0].trigger('click'); await flushPromises()
    await wrapper.get('.head-actions .ui-toggle').trigger('click')
    await wrapper.findAll('.group-index-item')[1].trigger('click'); await flushPromises()
    pending.resolve(updated); await flushPromises()
    // The write response must update A and notify its parent before the GET finishes.
    expect(wrapper.findAll('.group-index-item')[0].classes()).toContain('off')
    expect(wrapper.findAll('.group-index-item')[0].get('.off-tag').text()).toBe('OFF')
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(list).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
    expect(wrapper.find('.role-matrix').exists()).toBe(true)
    if (reloadFails) refreshed.reject(new Error('refresh unavailable'))
    else refreshed.resolve([updated,team('roles','role')])
    await flushPromises()
    expect(wrapper.findAll('.group-index-item')[0].classes()).toContain('off')
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
    expect(wrapper.find('.role-matrix').exists()).toBe(true)
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it.each(['write', 'refresh'])('refreshes default flags while preserving selection changed during %s', async phase => {
    const pending = deferred<AiGroup>()
    const refreshed = deferred<AiGroup[]>()
    const previous = { ...team('previous','general'), is_default: true }
    const updated = { ...team('general','general'), is_default: true }
    vi.spyOn(api,'setAiGroupState').mockReturnValue(pending.promise)
    const list = vi.spyOn(api,'listAiGroups').mockReturnValue(refreshed.promise)
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: [team('general','general'),previous,team('roles','role')] }, global: { plugins: [i18n] } })
    wrappers.push(wrapper); await flushPromises()
    await wrapper.findAll('.group-index-item')[0].trigger('click')
    await wrapper.get('.head-actions button[title="기본팀으로"]').trigger('click')
    if (phase === 'write') await wrapper.findAll('.group-index-item')[1].trigger('click')
    pending.resolve(updated); await flushPromises()
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(list).toHaveBeenCalledTimes(1)
    if (phase === 'refresh') await wrapper.findAll('.group-index-item')[1].trigger('click')
    refreshed.resolve([updated,{ ...previous, is_default:false },team('roles','role')]); await flushPromises()
    expect(wrapper.findAll('.group-index-item')[0].find('.star').exists()).toBe(true)
    expect(wrapper.findAll('.group-index-item')[1].find('.star').exists()).toBe(false)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('previous')
    expect(wrapper.find('.team-badge').exists()).toBe(false)
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
  })})


describe('0120 first entry and role creation recovery', () => {
  function panel(groups: AiGroup[] = [team('general', 'general')]) {
    const wrapper = mount(AiGroupsPanel, { props: { providers: [], initialGroups: groups }, attachTo: document.body, global: { plugins: [i18n] } })
    wrappers.push(wrapper); return wrapper
  }
  async function submitRole(wrapper: ReturnType<typeof panel>) {
    await wrapper.get('.role-entry button').trigger('click'); await flushPromises()
    const dialog = wrapper.findAllComponents(AiGroupEditDialog)[0]
    const input = document.querySelector('#ai-group-edit-name') as HTMLInputElement
    input.value = 'My roles'; input.dispatchEvent(new Event('input', { bubbles: true })); await flushPromises()
    expect((document.querySelector('#ai-group-edit-type') as HTMLSelectElement).value).toBe('role')
    expect(dialog.props('initial')).toBeNull()
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click(); await flushPromises()
  }
  const conflict = () => new api.AiRequestError('role exists', 'ai_role_group_exists', [])

  it('prefers the real role type regardless of list order or group name', async () => {
    const wrapper = panel([{ ...team('general','general'), name: 'AI 역할' }, team('actual','role')]); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('actual')
    expect(wrapper.findAll('.role-matrix-row')).toHaveLength(1)
    expect(wrapper.find('.role-entry').exists()).toBe(false)
    expect(api.getAiRoleProfiles).toHaveBeenCalledWith('actual')
  })

  it.each([false, true])('offers role creation without writing on entry (empty=%s)', async empty => {
    const create = vi.spyOn(api,'createAiGroup')
    const wrapper = panel(empty ? [] : [team('general','general')]); await flushPromises()
    expect(wrapper.get('.role-entry').text()).toContain('AI 역할 그룹이 없습니다.')
    expect(wrapper.get('.role-entry button').text()).toBe('AI 역할 그룹 만들기')
    expect(create).not.toHaveBeenCalled()
    expect(api.getAiRoleProfiles).not.toHaveBeenCalled()
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    if (!empty) expect(wrapper.findAll('.block')).toHaveLength(2)
  })

  it('selects delayed initial role data but preserves a clicked general group across refreshes', async () => {
    const wrapper = panel([])
    await wrapper.setProps({ initialGroups: [team('a','general')] })
    await wrapper.setProps({ initialGroups: [team('a','general'),team('roles','role')] }); await flushPromises()
    expect(wrapper.find('.role-matrix').exists()).toBe(true)
    await wrapper.findAll('.group-index-item')[0].trigger('click')
    await wrapper.setProps({ initialGroups: [team('roles','role'),team('a','general')] }); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('a')
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    // Only a vanished selection permits automatic fallback again.
    await wrapper.setProps({ initialGroups: [team('b','general'),team('roles','role')] }); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
  })

  it('treats a click on the already selected general group as an explicit choice', async () => {
    const wrapper = panel()
    await wrapper.get('.group-index-item').trigger('click')
    await wrapper.setProps({ initialGroups: [team('general','general'),team('roles','role')] }); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('general')
  })

  it('creates once, waits for the real response ID, then mounts only the common row', async () => {
    const pending = deferred<AiGroup>()
    const create = vi.spyOn(api,'createAiGroup').mockReturnValue(pending.promise)
    const update = vi.spyOn(api,'updateAiGroup')
    const original = team('general','general')
    const wrapper = panel([original]); await submitRole(wrapper)
    expect(create).toHaveBeenCalledTimes(1)
    expect(create).toHaveBeenCalledWith({ name: 'My roles', group_type: 'role' })
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    expect((document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).disabled).toBe(true)
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click(); await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    pending.resolve(team('server-created-id','role')); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('server-created-id')
    expect(wrapper.findAll('.role-matrix-row')).toHaveLength(1)
    expect(document.querySelector('#ai-group-edit-name')).toBeNull()
    expect(wrapper.emitted('changed')).toHaveLength(1)
    expect(update).not.toHaveBeenCalled()
    expect(original).toEqual(team('general','general'))
  })

  it('keeps ordinary creation general even after opening the role creation path', async () => {
    const create = vi.spyOn(api,'createAiGroup').mockResolvedValue(team('new-general','general'))
    const wrapper = panel()
    await wrapper.get('.role-entry button').trigger('click'); await flushPromises()
    ;(document.querySelector('.dialog-actions .ui-btn--secondary') as HTMLButtonElement).click(); await flushPromises()
    await wrapper.get('.detail-actions button').trigger('click'); await flushPromises()
    expect((document.querySelector('#ai-group-edit-type') as HTMLSelectElement).value).toBe('general')
    const input = document.querySelector('#ai-group-edit-name') as HTMLInputElement
    input.value='Ordinary'; input.dispatchEvent(new Event('input',{bubbles:true})); await flushPromises()
    ;(document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).click(); await flushPromises()
    expect(create).toHaveBeenCalledWith({name:'Ordinary',group_type:'general'})
    expect(wrapper.get('.group-detail-head h2').text()).toBe('new-general')
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
  })

  it.each([
    new Error('network unavailable'),
    new api.AiRequestError('forbidden', 'forbidden', []),
    new api.AiRequestError('name conflict', 'ai_group_name_exists', []),
  ])('keeps a failed creation visible without treating other errors as role conflicts: %s', async failure => {
    vi.spyOn(api,'createAiGroup').mockRejectedValue(failure)
    const list = vi.spyOn(api,'listAiGroups')
    const wrapper = panel(); await submitRole(wrapper)
    expect(document.querySelector('.form-error')?.textContent).toContain(failure.message)
    expect((document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).disabled).toBe(false)
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    expect(wrapper.emitted('changed')).toBeUndefined()
    expect(list).not.toHaveBeenCalled()
  })

  it('recovers an explicit duplicate from the server and opens the existing role', async () => {
    const create = vi.spyOn(api,'createAiGroup').mockRejectedValue(conflict())
    vi.spyOn(api,'listAiGroups').mockResolvedValue([team('general','general'),team('existing','role')])
    const wrapper = panel(); await submitRole(wrapper)
    expect(create).toHaveBeenCalledTimes(1)
    expect(api.listAiGroups).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('existing')
    expect(wrapper.findAll('.role-matrix-row')).toHaveLength(1)
    expect(document.querySelector('.form-error')).toBeNull()
  })

  it.each(['failed','missing'])('retries only GET after duplicate recovery is %s', async mode => {
    const create = vi.spyOn(api,'createAiGroup').mockRejectedValue(conflict())
    const list = vi.spyOn(api,'listAiGroups')
    if (mode === 'failed') list.mockRejectedValueOnce(new Error('lookup offline'))
    else list.mockResolvedValueOnce([team('general','general')])
    list.mockResolvedValueOnce([team('general','general'),team('existing','role')])
    const wrapper = panel(); await submitRole(wrapper)
    expect(wrapper.get('.role-entry[role="alert"]').text()).toContain(mode === 'failed' ? 'lookup offline' : '다시 조회')
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
    // The normal dialog cannot offer another role POST while recovery is pending.
    await wrapper.get('.detail-actions button').trigger('click'); await flushPromises()
    expect((document.querySelector('#ai-group-edit-type option[value="role"]') as HTMLOptionElement).disabled).toBe(true)
    ;(document.querySelector('.dialog-actions .ui-btn--secondary') as HTMLButtonElement).click(); await flushPromises()
    await wrapper.get('.role-entry button').trigger('click'); await flushPromises()
    expect(create).toHaveBeenCalledTimes(1)
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('existing')
  })

  it('keeps a general selection made while duplicate recovery is loading', async () => {
    vi.spyOn(api,'createAiGroup').mockRejectedValue(conflict())
    const pending = deferred<AiGroup[]>()
    vi.spyOn(api,'listAiGroups').mockReturnValue(pending.promise)
    const wrapper = panel(); await submitRole(wrapper)
    expect(wrapper.get('.role-entry[role="status"] button').attributes('disabled')).toBeDefined()
    await wrapper.get('.group-index-item').trigger('click')
    pending.resolve([team('general','general'),team('existing','role')]); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('general')
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
  })

  it('adds a delayed created group without overriding a later general selection', async () => {
    const pending = deferred<AiGroup>()
    vi.spyOn(api,'createAiGroup').mockReturnValue(pending.promise)
    const wrapper = panel(); await submitRole(wrapper)
    await wrapper.get('.group-index-item').trigger('click')
    pending.resolve(team('new-role','role')); await flushPromises()
    expect(wrapper.findAll('.group-index-item')).toHaveLength(2)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('general')
    await wrapper.setProps({ initialGroups:[team('general','general'),team('new-role','role')] }); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('general')
    expect(wrapper.emitted('changed')).toHaveLength(1)
  })

  it('preserves explicit general selection through save and GET refresh', async () => {
    const refreshed = [{...team('general','general'), enabled:false},team('roles','role')]
    vi.spyOn(api,'setAiGroupState').mockResolvedValue(refreshed[0])
    vi.spyOn(api,'listAiGroups').mockResolvedValue(refreshed)
    const wrapper = panel([team('general','general'),team('roles','role')]); await flushPromises()
    await wrapper.findAll('.group-index-item')[0].trigger('click')
    await wrapper.get('.head-actions .ui-toggle').trigger('click'); await flushPromises()
    await wrapper.setProps({initialGroups:copy(refreshed)}); await flushPromises()
    expect(wrapper.get('.group-detail-head h2').text()).toBe('general')
    expect(wrapper.findAll('.block')).toHaveLength(2)
    expect(wrapper.find('.role-matrix').exists()).toBe(false)
  })

  it.each([false,true])('does not expose creation to non-administrators (empty=%s)', async empty => {
    setCurrentUser({username:'reader',email:null,isAdmin:false})
    const create = vi.spyOn(api,'createAiGroup')
    const wrapper = panel(empty ? [] : [team('general','general')]); await flushPromises()
    expect(wrapper.get('.role-entry').text()).toContain('AI 역할 그룹이 없습니다.')
    expect(wrapper.find('.role-entry button').exists()).toBe(false)
    expect(wrapper.find('.detail-actions').exists()).toBe(false)
    expect(create).not.toHaveBeenCalled()
  })

  it('blocks a role submit if another role appears while the creation dialog is open', async () => {
    const create=vi.spyOn(api,'createAiGroup')
    const wrapper=panel()
    await wrapper.get('.role-entry button').trigger('click'); await flushPromises()
    const input=document.querySelector('#ai-group-edit-name') as HTMLInputElement
    input.value='Roles'; input.dispatchEvent(new Event('input',{bubbles:true})); await flushPromises()
    await wrapper.setProps({initialGroups:[team('general','general'),team('roles','role')]}); await flushPromises()
    expect((document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement).disabled).toBe(true)
    expect(create).not.toHaveBeenCalled()
  })
})


describe('0120 group entry loading boundary', () => {
  it('does not claim no role before GET completes, and retries a failed initial load', async () => {
    const pending=deferred<AiGroup[]>()
    vi.spyOn(api,'fetchVisibleSystemAi').mockResolvedValue({providers:[]} as Awaited<ReturnType<typeof api.fetchVisibleSystemAi>>)
    const list=vi.spyOn(api,'listAiGroups').mockReturnValueOnce(pending.promise)
      .mockResolvedValueOnce([team('general','general'),team('roles','role')])
    const create=vi.spyOn(api,'createAiGroup')
    const router=createRouter({history:createMemoryHistory(),routes:[{path:'/ai',component:AiSettingsView}]})
    await router.push('/ai?tab=groups'); await router.isReady()
    const wrapper=mount(AiSettingsView,{global:{plugins:[i18n,router]}})
    wrappers.push(wrapper); await flushPromises()
    expect(wrapper.find('.view-loading').exists()).toBe(true)
    expect(wrapper.find('.role-entry').exists()).toBe(false)
    pending.reject(new Error('initial groups offline')); await flushPromises()
    expect(wrapper.get('.view-error').text()).toContain('initial groups offline')
    expect(wrapper.find('.role-entry').exists()).toBe(false)
    await wrapper.get('.view-error button').trigger('click'); await flushPromises()
    expect(list).toHaveBeenCalledTimes(2)
    expect(wrapper.get('.group-detail-head h2').text()).toBe('roles')
    expect(wrapper.findAll('.role-matrix-row')).toHaveLength(1)
    expect(create).not.toHaveBeenCalled()
  })
})
