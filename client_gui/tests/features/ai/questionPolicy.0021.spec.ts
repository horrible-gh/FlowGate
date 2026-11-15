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
it('does not discard the draft or close when save fails', async () => {
  vi.mocked(api.savePolicy).mockRejectedValue(new Error('offline'))
  const w = mountPanel(); await flushPromises()
  await w.find('textarea').setValue('수정'); await w.find('header button').trigger('click')
  await w.find('footer button').trigger('click'); await flushPromises()
  expect(w.find('textarea').element.value).toBe('수정'); expect(w.find('select').exists()).toBe(true)
  expect(w.find('[role="alert"]').exists()).toBe(true); w.unmount()
})
