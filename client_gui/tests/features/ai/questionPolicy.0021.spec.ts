import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import Panel from '@/features/ai/policy/QuestionPolicyPanel.vue'
import * as api from '@/features/ai/policy/api'
vi.mock('@/features/ai/policy/api')
beforeEach(() => {
  vi.resetAllMocks(); i18n.global.locale.value = 'ko'
  vi.mocked(api.loadPolicy).mockResolvedValue({ body: '기존 방침', updated_at: null })
  vi.mocked(api.loadProviders).mockResolvedValue([{ id: 'expensive', name: 'AI A' }, { id: 'chosen', name: 'AI B' }])
  vi.mocked(api.startAssistant).mockResolvedValue({ request_id: 'r' })
  vi.mocked(api.assistantResult).mockResolvedValue({ status: 'completed', reply: { message: '이해했습니다', choices: [], policy: 'UI만 질문' } })
  vi.mocked(api.savePolicy).mockImplementation(async body => ({ body }))
})
const mountPanel = () => mount(Panel, { global: { plugins: [i18n], stubs: {
  ModelessShell: { props: ['open'], template: `<div v-if="open"><button data-test="close" @click="$emit('close')">×</button><slot /></div>` },
} } })
it('requires an explicit AI, resumes after close, confirms into unsaved text then saves', async () => {
  const w = mountPanel(); await flushPromises()
  await w.find('header button').trigger('click')
  expect(w.find('select').element.value).toBe('')
  expect(api.startAssistant).not.toHaveBeenCalled()
  await w.find('select').setValue('chosen')
  await w.findAll('button').find(b => b.text() === '대화 시작')!.trigger('click'); await flushPromises()
  expect(api.startAssistant).toHaveBeenCalledWith('chosen', '기존 방침', [], 'ko')
  await w.find('[data-test="close"]').trigger('click'); await w.find('header button').trigger('click')
  expect(w.text()).toContain('이해했습니다')
  expect(api.startAssistant).toHaveBeenCalledTimes(1)
  await w.findAll('button').find(b => b.text() === '맞음')!.trigger('click')
  expect(w.find('textarea').element.value).toBe('UI만 질문')
  expect(w.text()).toContain('아직 저장 안 됨'); expect(api.savePolicy).not.toHaveBeenCalled()
  await w.find('footer button').trigger('click'); await flushPromises()
  expect(api.savePolicy).toHaveBeenCalledWith('UI만 질문')
  expect(w.find('select').exists()).toBe(false); w.unmount()
})
it('0004-CH: chat bubbles, choices only as buttons, prior choices sent back, a failure says why', async () => {
  vi.mocked(api.assistantResult)
    .mockResolvedValueOnce({ status: 'completed', reply: { message: '어디까지 맡길까요?', choices: [{ label: '구현은 알아서', recommended: true }, { label: '전부 질문', recommended: false }], policy: null } })
    .mockResolvedValueOnce({ status: 'failed', error_code: 'timeout' })
  const w = mountPanel(); await flushPromises()
  await w.find('header button').trigger('click'); await w.find('select').setValue('chosen')
  await w.findAll('button').find(b => b.text() === '대화 시작')!.trigger('click'); await flushPromises()
  const bubbles = w.findAll('[data-test="tl-message"]')
  expect(bubbles).toHaveLength(1); expect(bubbles[0].attributes('data-speaker')).toBe('ai')
  expect(bubbles[0].text()).toContain('어디까지 맡길까요?'); expect(bubbles[0].text()).not.toContain('전부 질문')
  expect(bubbles[0].find('[data-test="tl-provider"]').text()).toBe('AI B')
  const choices = w.findAll('.qp-choice')
  expect(choices.map(c => c.classes('is-recommended'))).toEqual([true, false])
  expect(choices[0].find('.qp-badge').text()).toBe('추천')
  await choices[1].trigger('click'); await flushPromises()
  expect(api.startAssistant).toHaveBeenLastCalledWith('chosen', '기존 방침', [
    { role: 'assistant', content: '어디까지 맡길까요?\n(choices: 구현은 알아서 / 전부 질문)' },
    { role: 'user', content: '전부 질문' }], 'ko')
  expect(w.findAll('[data-test="tl-message"]').map(b => b.attributes('data-speaker'))).toEqual(['ai', 'user'])
  expect(w.find('.qp-choices').exists()).toBe(false)
  expect(w.find('[role="alert"]').text()).toContain('AI 답이 너무 오래 걸려')
  w.unmount()
})
it('does not discard the draft or close when save fails', async () => {
  vi.mocked(api.savePolicy).mockRejectedValue(new Error('offline'))
  const w = mountPanel(); await flushPromises()
  await w.find('textarea').setValue('수정'); await w.find('header button').trigger('click')
  await w.find('footer button').trigger('click'); await flushPromises()
  expect(w.find('textarea').element.value).toBe('수정'); expect(w.find('select').exists()).toBe(true)
  expect(w.find('[role="alert"]').exists()).toBe(true); w.unmount()
})
