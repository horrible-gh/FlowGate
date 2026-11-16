import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiRoleMatrix from '@/features/ai/groups/AiRoleMatrix.vue'
import * as api from '@/features/ai/api'
import type { AiRoleProfile, AiRoleProfiles, RoleProfileReviewCount } from '@/features/ai/types'

registerAppMessages(i18n)
const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value))
const profile = (type_key: string): AiRoleProfile => ({ type_key, position: type_key === 'common' ? 0 : 1,
  roles: Object.fromEntries(['instruction', 'execution', 'review'].map(role => [role, { kind: type_key === 'common' ? 'unset' : 'inherit' }])) as AiRoleProfile['roles'] })
const choices = { types: [{ type_key: 'T', label: '작업' }, { type_key: 'D', label: '기본 설계' }],
  providers: [{ provider_id: 'p1', provider_name: 'Model One' }], review_count_choices: [0, 1, 2, 3], review_count_default: 0 }
function deferred<T>() { let resolve!: (value: T) => void
  const promise = new Promise<T>(yes => { resolve = yes }); return { promise, resolve } }

// A tiny stand-in for the server: stores the raw value and answers the effective one the way the API contract says
// (type value, else common value, else 0; a stored value outside the current choices is effective 0 / default).
let stored: Record<string, number | null>
let maxChoice: number
const inChoices = (n: number | null) => n !== null && (n === -1 || (n >= 0 && n <= maxChoice))
function effective(key: string): RoleProfileReviewCount {
  const own = stored[key] ?? null, shared = stored.common ?? null
  const pick = key === 'common' ? own : (own ?? shared)
  const source = key !== 'common' && own !== null ? 'type' : pick !== null ? 'common' : 'default'
  if (pick === null || !inChoices(pick)) return { review_count: own, effective_review_count: 0, source: 'default' }
  return { review_count: own, effective_review_count: pick, source: key === 'common' && pick !== null ? 'common' : source }
}
const snapshot = (id = 'roles'): AiRoleProfiles => ({ ai_group_id: id, items: Object.keys(stored).map(profile),
  review_counts: Object.fromEntries(Object.keys(stored).map(key => [key, effective(key)])) })
const wrappers: ReturnType<typeof mount>[] = []
function matrix(id = 'roles', canEdit = true) {
  const wrapper = mount(AiRoleMatrix, { props: { groupId: id, canEdit }, global: { plugins: [i18n] } })
  wrappers.push(wrapper); return wrapper
}
const select = (wrapper: ReturnType<typeof mount>, type: string) => wrapper.get(`[data-type="${type}"] [data-role="review_count"]`)
const value = (wrapper: ReturnType<typeof mount>, type: string) => (select(wrapper, type).element as HTMLSelectElement).value
const labels = (wrapper: ReturnType<typeof mount>, type: string) => select(wrapper, type).findAll('option').map(o => o.text())

beforeEach(() => {
  vi.restoreAllMocks()
  i18n.global.locale.value = 'ko'
  setCurrentUser({ username: 'admin', email: null, isAdmin: true })
  stored = { common: null, T: null }
  maxChoice = 3
  vi.spyOn(api, 'getAiRoleProfiles').mockImplementation(async id => snapshot(id))
  vi.spyOn(api, 'getAiRoleProfileOptions').mockImplementation(async () => ({ ...copy(choices), review_count_choices: Array.from({ length: maxChoice + 1 }, (_, n) => n) }))
  vi.spyOn(api, 'setAiRoleProfileCell').mockImplementation(async () => snapshot())
  vi.spyOn(api, 'setAiRoleProfileReviewCount').mockImplementation(async (id, key, count) => { stored[key] = count; return snapshot(id) })
})
afterEach(async () => {
  wrappers.splice(0).forEach(wrapper => wrapper.unmount())
  setCurrentUser(null)
  document.body.innerHTML = ''
  await flushPromises()
})

describe('0027 role table review-count column', () => {
  it('adds one column to the existing table with the Inspector choices and no -1', async () => {
    const wrapper = matrix(); await flushPromises()
    expect(wrapper.findAll('.role-matrix-head span').map(s => s.text())).toEqual(['카드 유형', '지시 AI', '실행 AI', '검수 AI', '검수 횟수'])
    expect(wrapper.findAll('[data-type="T"] select')).toHaveLength(4)
    expect(labels(wrapper, 'common')).toEqual(['기본 · 안 함', '안 함', '1회', '2회', '3회'])
    expect(labels(wrapper, 'T')).toEqual(['공용 · 안 함', '안 함', '1회', '2회', '3회'])
    expect(value(wrapper, 'common')).toBe('unset')
    expect(value(wrapper, 'T')).toBe('inherit')
    expect(select(wrapper, 'common').find('option[value="count:-1"]').exists()).toBe(false)
  })

  it('saves, rereads and survives a reload for common and a type row, and returns a type row to inherit', async () => {
    const wrapper = matrix(); await flushPromises()
    await select(wrapper, 'common').setValue('count:2'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'common', 2)
    expect(value(wrapper, 'common')).toBe('count:2')
    expect(labels(wrapper, 'T')[0]).toBe('공용 · 2회')
    await select(wrapper, 'T').setValue('count:3'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'T', 3)
    expect(value(wrapper, 'T')).toBe('count:3')
    // F5: a fresh mount reads the stored values back.
    const again = matrix(); await flushPromises()
    expect(value(again, 'common')).toBe('count:2')
    expect(value(again, 'T')).toBe('count:3')
    await select(again, 'T').setValue('inherit'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'T', null)
    expect(value(again, 'T')).toBe('inherit')
    expect(stored.T).toBeNull()
    const reloaded = matrix(); await flushPromises()
    expect(value(reloaded, 'T')).toBe('inherit')
    // 0 is an explicit "안 함", not the same as inheriting.
    await select(reloaded, 'T').setValue('count:0'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'T', 0)
    expect(value(reloaded, 'T')).toBe('count:0')
    await select(reloaded, 'common').setValue('unset'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'common', null)
    expect(api.setAiRoleProfileCell).not.toHaveBeenCalled()
  })

  it('keeps a stored -1 shown without offering it to other rows', async () => {
    stored = { common: -1, T: null }
    const wrapper = matrix(); await flushPromises()
    expect(value(wrapper, 'common')).toBe('count:-1')
    expect(select(wrapper, 'common').find('option[value="count:-1"]').text()).toBe('통과할 때까지')
    expect(labels(wrapper, 'T')[0]).toBe('공용 · 통과할 때까지')
    expect(select(wrapper, 'T').find('option[value="count:-1"]').exists()).toBe(false)
    expect(api.setAiRoleProfileReviewCount).not.toHaveBeenCalled()
  })

  it('keeps a stored value above the current maximum shown and allows picking it again', async () => {
    maxChoice = 3
    stored = { common: 5, T: null }
    const wrapper = matrix(); await flushPromises()
    expect(value(wrapper, 'common')).toBe('count:5')
    expect(labels(wrapper, 'common')).toContain('5회')
    // A new card gets 0 now (server's effective value), so the inherited label says so.
    expect(labels(wrapper, 'T')[0]).toBe('공용 · 안 함')
    expect(select(wrapper, 'T').find('option[value="count:5"]').exists()).toBe(false)
    await select(wrapper, 'common').setValue('count:5'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenLastCalledWith('roles', 'common', 5)
  })

  it('offers more counts when the server maximum is higher', async () => {
    maxChoice = 5
    const wrapper = matrix(); await flushPromises()
    expect(labels(wrapper, 'common')).toEqual(['기본 · 안 함', '안 함', '1회', '2회', '3회', '4회', '5회'])
  })

  it('restores the last confirmed value and reports the error when saving fails', async () => {
    stored = { common: 1, T: null }
    vi.mocked(api.setAiRoleProfileReviewCount).mockRejectedValueOnce(new Error('count refused'))
    const wrapper = matrix(); await flushPromises()
    await select(wrapper, 'common').setValue('count:3'); await flushPromises()
    expect(value(wrapper, 'common')).toBe('count:1')
    expect(wrapper.get('[role="alert"]').text()).toContain('count refused')
    expect(stored.common).toBe(1)
  })

  it('distinguishes a committed save from a failed confirmation and retries only the read', async () => {
    const wrapper = matrix(); await flushPromises()
    vi.mocked(api.getAiRoleProfiles).mockRejectedValueOnce(new Error('read failed'))
    await select(wrapper, 'common').setValue('count:2'); await flushPromises()
    expect(wrapper.get('[role="alert"]').text()).toContain('저장 확인 실패')
    expect(value(wrapper, 'common')).toBe('count:2')
    await wrapper.get('[role="alert"] button').trigger('click'); await flushPromises()
    expect(api.setAiRoleProfileReviewCount).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  })

  it('does not let a late answer for another group overwrite the current group', async () => {
    const pending = deferred<AiRoleProfiles>()
    vi.mocked(api.getAiRoleProfiles).mockImplementationOnce(() => pending.promise)
    const wrapper = matrix('old'); await flushPromises()
    stored = { common: 1, T: null }
    await wrapper.setProps({ groupId: 'new' }); await flushPromises()
    expect(value(wrapper, 'common')).toBe('count:1')
    pending.resolve({ ai_group_id: 'old', items: [profile('common'), profile('T')],
      review_counts: { common: { review_count: 3, effective_review_count: 3, source: 'common' }, T: { review_count: 2, effective_review_count: 2, source: 'type' } } })
    await flushPromises()
    expect(value(wrapper, 'common')).toBe('count:1')
    expect(value(wrapper, 'T')).toBe('inherit')
  })

  it('does not apply a late save answer after the group changed', async () => {
    const pending = deferred<AiRoleProfiles>()
    const wrapper = matrix('old'); await flushPromises()
    vi.mocked(api.setAiRoleProfileReviewCount).mockImplementationOnce(() => pending.promise)
    await select(wrapper, 'common').setValue('count:3'); await flushPromises()
    await wrapper.setProps({ groupId: 'new' }); await flushPromises()
    pending.resolve({ ai_group_id: 'old', items: [profile('common')], review_counts: { common: { review_count: 3, effective_review_count: 3, source: 'common' } } })
    await flushPromises()
    expect(value(wrapper, 'common')).toBe('unset')
    expect(wrapper.find('[data-type="T"]').exists()).toBe(true)
  })

  it('is read-only for a non-administrator', async () => {
    const wrapper = matrix('roles', false); await flushPromises()
    expect(select(wrapper, 'common').attributes('disabled')).toBeDefined()
    expect(select(wrapper, 'T').attributes('disabled')).toBeDefined()
  })

  it.each([['en', 'Review rounds', ['Common · None', 'None', '2 time(s)']], ['ja', '検収回数', ['共通 · しない', 'しない', '2回']]] as const)(
    'has the column text in %s', async (language, header, texts) => {
      i18n.global.locale.value = language
      stored = { common: 2, T: null }
      const wrapper = matrix(); await flushPromises()
      expect(wrapper.findAll('.role-matrix-head span').at(-1)!.text()).toBe(header)
      expect(labels(wrapper, 'T')[0]).toBe(texts[0].replace('None', '2 time(s)').replace('しない', '2回'))
      expect(labels(wrapper, 'common')).toContain(texts[1])
      expect(labels(wrapper, 'common')).toContain(texts[2])
    })
})
