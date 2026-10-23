import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiGroupsPanel from '@/features/ai/groups/AiGroupsPanel.vue'
import * as api from '@/features/ai/api'
import type { AiGroup, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// AI 그룹 (flowgate.v02.0017): a team of members, user-defined roles and an optional
// Supervisor. The 0015 fixed executor / reviewer / reworker slots were retired.

const providers: ProviderItem[] = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'GPT-4o', exec_type: 'api', kind: 'openai', enabled: true },
  { id: 'p3', name: 'Gemini Pro', exec_type: 'api', kind: 'gemini', enabled: true },
]

const teamA: AiGroup = {
  ai_group_id: 'g1',
  name: '개발팀 A',
  description: '범용 개발 팀',
  enabled: true,
  is_default: true,
  status: 'attention',
  member_count: 2,
  role_count: 1,
  members: [
    {
      member_id: 'm1', provider_id: 'p1', provider_name: 'Claude Sonnet 5', is_available: true, status: 'available',
      role_ids: ['r1'], latest_evaluation: { evaluation_id: 'ev-1', total_score: 86, finished_at: null }, fit_score: 80,
    },
    {
      member_id: 'm2', provider_id: 'p2', provider_name: 'GPT-4o', is_available: false, status: 'unavailable',
      role_ids: [], latest_evaluation: null, fit_score: null,
    },
  ],
  roles: [{ role_id: 'r1', name: '리뷰어', description: null, criteria: [], position: 0, member_ids: ['m1'] }],
  supervisor: { member_id: 'm2', provider_id: 'p2', provider_name: 'GPT-4o', is_available: false, status: 'unavailable' },
}

const teamB: AiGroup = {
  ...teamA,
  ai_group_id: 'g2',
  name: '문서팀',
  description: '문서 작성',
  is_default: false,
  status: 'ok',
  members: [],
  roles: [],
  supervisor: null,
  member_count: 0,
  role_count: 0,
}

function mountPanel(groups: AiGroup[] = [teamA, teamB]) {
  return mount(AiGroupsPanel, {
    props: { providers, initialGroups: groups },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
}

describe('AiGroupsPanel (0015 T0003, teams since 0017)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
  })

  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('renders the team index with the default star and attention mark, and the selected team detail', () => {
    const wrapper = mountPanel()

    const items = wrapper.findAll('.group-index-item')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toContain('개발팀 A')
    expect(items[0].find('.star').exists()).toBe(true)
    expect(items[0].find('.warn-dot').exists()).toBe(true)
    expect(items[1].find('.warn-dot').exists()).toBe(false)

    expect(wrapper.find('.group-detail-head h2').text()).toBe('개발팀 A')
    // Supervisor first, then members in the order they were added.
    const rows = wrapper.findAll('.member-row')
    expect(rows.map((row) => row.find('.name-line b').text())).toEqual(['GPT-4o', 'Claude Sonnet 5'])
    expect(rows[0].find('.supervisor-tag').exists()).toBe(true)
    expect(rows[0].text()).toContain('사용 불가')
    expect(rows[1].text()).toContain('평가 8.6')
    expect(rows[1].find('.role-chip').text()).toContain('리뷰어')
    wrapper.unmount()
  })

  it('filters the team index by search query and switches the detail on selection', async () => {
    const wrapper = mountPanel()

    const search = wrapper.find('.group-index .detail-search input')
    await search.setValue('nonexistent')
    expect(wrapper.findAll('.group-index-item')).toHaveLength(0)

    await search.setValue('문서')
    const items = wrapper.findAll('.group-index-item')
    expect(items).toHaveLength(1)
    await items[0].trigger('click')
    expect(wrapper.find('.group-detail-head h2').text()).toBe('문서팀')
    wrapper.unmount()
  })

  it('is read-only for a non-administrator', () => {
    setCurrentUser({ username: 'member', email: null, isAdmin: false })
    const wrapper = mountPanel()

    expect(wrapper.find('.detail-actions').exists()).toBe(false)
    expect(wrapper.find('.head-actions .ui-toggle').exists()).toBe(false)
    expect(wrapper.find('.member-row .crown').exists()).toBe(false)
    expect(wrapper.find('.member-row .role-chip').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('offers only AI that are not members yet when adding a member', async () => {
    const addSpy = vi.spyOn(api, 'addAiGroupMember').mockResolvedValue(teamA)
    const wrapper = mountPanel()

    const addButton = wrapper.findAll('.block-head .block-action')[0]
    await addButton.trigger('click')
    const options = Array.from(document.querySelectorAll('.menu-item b')).map((el) => el.textContent)
    expect(options).toEqual(['Gemini Pro'])

    ;(document.querySelector('.menu-item') as HTMLButtonElement).click()
    await flushPromises()
    expect(addSpy).toHaveBeenCalledWith('g1', 'p3')
    wrapper.unmount()
  })

  it('deletes a team after the confirm dialog', async () => {
    const deleteSpy = vi.spyOn(api, 'deleteAiGroup').mockResolvedValue()
    const wrapper = mountPanel()

    await wrapper.find('.head-actions .danger-icon').trigger('click')
    const confirmBtn = document.querySelector('.ui-confirm-actions .ui-btn--danger') as HTMLButtonElement
    expect(confirmBtn).toBeTruthy()
    confirmBtn.click()
    await flushPromises()

    expect(deleteSpy).toHaveBeenCalledWith('g1')
    expect(wrapper.findAll('.group-index-item').map((item) => item.find('b').text())).toEqual(['문서팀'])
    wrapper.unmount()
  })
})

// flowgate.v02.0088 F07: the team and role delete confirms name their actions with gui.common.delete / cancel in the
// current language (ConfirmDialog's own default stays as it is); 취소 sends nothing.
describe('AiGroupsPanel delete confirms (0088 F07)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
    i18n.global.locale.value = 'ko'
  })
  const buttons = () => Array.from(document.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions .ui-btn')).map((b) => b.textContent?.trim())

  it.each([
    ['ko', '삭제', '취소'],
    ['ja', '削除', 'キャンセル'],
    ['en', 'Delete', 'Cancel'],
  ] as const)('the team and the role delete say %s: %s / %s, and cancel deletes nothing', async (locale, del, cancel) => {
    i18n.global.locale.value = locale
    const deleteGroup = vi.spyOn(api, 'deleteAiGroup').mockResolvedValue()
    const deleteRole = vi.spyOn(api, 'deleteAiGroupRole').mockResolvedValue(teamA)
    const wrapper = mountPanel()

    await wrapper.find('.head-actions .danger-icon').trigger('click')
    expect(buttons()).toEqual([cancel, del])
    ;(document.querySelector('.ui-confirm-actions .ui-btn--secondary') as HTMLButtonElement).click()
    await flushPromises()
    expect(deleteGroup).not.toHaveBeenCalled()

    const roleDelete = wrapper.get('.role-card-actions .danger-icon')
    await roleDelete.trigger('click')
    expect(buttons()).toEqual([cancel, del])
    ;(document.querySelector('.ui-confirm-actions .ui-btn--secondary') as HTMLButtonElement).click()
    await flushPromises()
    expect(deleteRole).not.toHaveBeenCalled()
    wrapper.unmount()
  })
})
