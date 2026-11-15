import { flushPromises, mount } from '@vue/test-utils'
import { computed, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { documentRead } from '@/features/document/api/document'
import QuestionTab from '@/features/designer/top/notifications/NotificationQuestionsTab.vue'
import QuestionDocument from '@/features/designer/top/notifications/NotificationQuestionDocument.vue'
import { qaErrorOf } from '@/features/document/api/qa'
import { qnaAiFailureKey } from '@/features/designer/inspector/features/qna/qnaModel'
const mocks = vi.hoisted(() => ({ state: {} as any, open: vi.fn(), request: vi.fn(), metadata: null as any }))
vi.mock('@/features/designer/inspector/useInspectorResource', () => ({ useInspectorResource: (options: any) => options.read === documentRead ? mocks.metadata : ({ data: { value: { content: 'Stored document body', missing: false } }, status: { value: 'ready' }, reload: vi.fn() }) }))
vi.mock('@/features/designer/inspector/features/qna/useQna', () => ({ useQna: () => mocks.state }))
vi.mock('@/features/designer/inspector/features/qna/useQnaWindows', () => ({ openQnaWindow: mocks.open }))
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
registerAppMessages(i18n)
for (const locale of ['ko', 'en', 'ja'] as const) i18n.global.mergeLocaleMessage(locale, { gui: { inspector: { qna: qnaMessages[locale] } } })
const target = { projectId: 'p', flowId: 'p.m.1', docId: 'p.m.1.0001-T' } as any
const row = { doc_id: target.docId, seq: 7, title: 'Question title from open items', type_code: 'T' }
beforeEach(() => {
 vi.clearAllMocks(); i18n.global.locale.value = 'ko'
 mocks.metadata = { data: ref({ title: 'Actual document title' }), status: ref('ready'), reload: vi.fn() }
 mocks.state = {
  items: ref([{ id: 42, seq: 7, title: null, body: 'Stored question body', state: 'unanswered', answerable: true, createdAt: null }]),
  availability: ref({ answer: { visible: true, enabled: true }, ai_request: { visible: true, enabled: true } }),
  providers: ref([{ id: 'ai', name: 'Provider' }]), defaultProviderId: ref('ai'), requesting: ref(null), awaiting: ref(new Set()), aiFailure: ref(null), requestAi: mocks.request, errorKey: ref(null), reload: vi.fn(),
 }
})
const open = () => mount(QuestionDocument, { props: { target, rows: [row], flowName: 'Flow Alpha', refreshKey: 0 }, global: { plugins: [i18n] } })
describe('question notification rows', () => {
 it.each(['ko', 'en', 'ja'])('shows saved question, affiliation and three accessible icons in %s', async locale => {
  i18n.global.locale.value = locale as any
  const w = open()
  expect(w.text()).toContain('Q7 Stored question body'); expect(w.text()).toContain('Flow Alpha · Actual document title'); expect(w.text()).not.toContain(row.title)
  const buttons = w.findAll('.question-actions button'); expect(buttons).toHaveLength(3)
  for (const button of buttons) expect(button.attributes('aria-label')).toBe(button.attributes('title'))
  await buttons[0]!.trigger('click'); expect(mocks.open).toHaveBeenCalledWith(expect.objectContaining({ ...target, title: 'Actual document title', cardDocuments: [expect.objectContaining({ docId: target.docId, title: 'Actual document title', hasBody: true })] }), 42)
  const textarea = document.createElement('textarea'), form = document.createElement('div'); form.dataset.test = 'qna-window'; form.dataset.item = '42'; form.append(textarea); document.body.append(form)
  await buttons[1]!.trigger('click'); expect(document.activeElement).toBe(textarea); form.remove()
  await buttons[2]!.trigger('click'); await w.get('.question-ai button').trigger('click')
  expect(mocks.request).toHaveBeenCalledWith(42, 'ai'); w.unmount()
 })
 it('uses document metadata for several questions of one document, including an untitled question', async () => {
  mocks.state.items.value.push({ ...mocks.state.items.value[0], id: 43, seq: 8, title: 'Second question' })
  const w = mount(QuestionTab, { props: { items: [row, { ...row, seq: 8, title: '' }], projectId: 'p', project: null, refreshKey: 0 }, global: { plugins: [i18n] } })
  expect(w.findAll('[data-test="notification-question"]')).toHaveLength(2)
  expect(w.text().match(/Actual document title/g)).toHaveLength(2)
  expect(w.text()).not.toContain(row.title)
  for (const action of w.findAll('.question-actions button').filter((_, i) => i % 3 === 0)) await action.trigger('click')
  for (const [opened] of mocks.open.mock.calls) {
   expect(opened.title).toBe('Actual document title'); expect(opened.cardDocuments[0].title).toBe('Actual document title')
  }
  mocks.metadata.data.value = { title: 'Renamed document' }; await flushPromises()
  expect(w.text().match(/Renamed document/g)).toHaveLength(2); w.unmount()
 })
 it('uses the document id while metadata is unavailable, never a question title', async () => {
  mocks.metadata.data.value = null; mocks.metadata.status.value = 'error'
  const w = open(); expect(w.text()).toContain(target.docId); expect(w.text()).not.toContain(row.title)
  await w.findAll('.question-actions button')[0]!.trigger('click')
  expect(mocks.open.mock.calls[0]![0].title).toBe(target.docId); w.unmount()
 })
 it('disables requesting without any available provider and blocks unavailable actions', async () => {
  mocks.state.providers.value = []; const w = open()
  await w.findAll('.question-actions button')[2]!.trigger('click')
  expect(w.get('.question-ai button').attributes('disabled')).toBeDefined()
  mocks.state.availability.value.answer.visible = false; mocks.state.availability.value.ai_request.visible = false
  await flushPromises()
  expect(w.findAll('.question-actions button')[1]!.attributes('disabled')).toBeDefined()
  expect(w.findAll('.question-actions button')[2]!.attributes('disabled')).toBeDefined(); w.unmount()
 })
 it('shows AI answering, then answered, and per-document retry failures', async () => {
  const w = open(); mocks.state.awaiting.value = new Set([42]); await flushPromises(); expect(w.text()).toContain('AI 답변 중')
  mocks.state.items.value[0].state = 'answered'; await flushPromises(); expect(w.text()).toContain('답변 완료')
  mocks.state.items.value = []; mocks.state.errorKey.value = 'error.network'; await flushPromises()
  await w.get('button').trigger('click'); expect(mocks.state.reload).toHaveBeenCalled(); w.unmount()
 })
 it.each([['GROUP_AI_RUN_LOCKED', 423, 'running'], ['run_in_progress',409,'running'], ['no_enabled_provider',409,'no_provider'], ['',403,'forbidden'], ['',404,'not_found'], ['',422,'invalid']])('retains existing refusal classification %s', (code,status,kind) => {
  const error = qaErrorOf({ response: { status, data: { code, http_status: status } } })
  expect(error.kind).toBe(kind); expect(qnaAiFailureKey(error.kind)).toBeTruthy()
 })
})
