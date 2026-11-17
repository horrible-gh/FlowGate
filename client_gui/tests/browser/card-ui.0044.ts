import { createApp, h, ref, nextTick } from 'vue'
import axios from 'axios'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiRoleSelect from '@/features/ai/roles/AiRoleSelect.vue'
import AiRoleMatrix from '@/features/ai/groups/AiRoleMatrix.vue'
import QuestionPolicyPanel from '@/features/ai/policy/QuestionPolicyPanel.vue'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import '@/app/styles/theme.css'
const providers = Array.from({ length: 40 }, (_, i) => ({ id: 'p' + i, name: 'Model ' + i }))
axios.defaults.adapter = async config => {
  const path = config.url ?? ''
  let data: any = { ok: true }
  if (path.endsWith('/role-profile-options')) data = { types: [], providers: providers.map(p => ({ provider_id: p.id, provider_name: p.name })), review_count_choices: [0, 1, 2], review_count_default: 1 }
  else if (path.endsWith('/role-profiles')) data = { ai_group_id: 'g', items: [{ type_key: 'common', position: 0, roles: { instruction: { kind: 'assigned', provider_id: 'p0' }, execution: { kind: 'assigned', provider_id: 'p0' }, review: { kind: 'not_used' } } }] }
  else if (path.endsWith('/assistant/providers')) data = { providers }
  else if (path.endsWith('/question-policy')) data = { body: '', updated_at: null }
  return { config, data, status: 200, statusText: 'OK', headers: {} }
}
registerAppMessages(i18n); i18n.global.locale.value = 'en'
setCurrentUser({ username: 'admin', email: null, isAdmin: true })
const docs = ref(Array.from({ length: 12 }, (_, i) => ({ docId: 'doc' + i, docType: i % 2 ? 'TR' : 'T', title: 'Document ' + i, ownerSlot: i % 2 ? 'report' : 'instruction', report: !!(i % 2) })))
const opened: string[] = [], changes: unknown[] = []
createApp({ render: () => h('main', { style: 'padding:24px;color:var(--fg2-text);background:#081421;min-height:900px' }, [
  h('h2', 'AI selectors and card documents'),
  ...['Inspector 32px', 'Placement 36px', 'Defaults 36px'].map((name, i) => h('section', { class: i ? 'host36' : 'host32', style: 'width:300px;margin-bottom:16px' }, [h('label', name), h(AiRoleSelect, { modelValue: { kind: 'assigned', providerId: 'p0' }, providers, label: name, 'onUpdate:modelValue': (value: unknown) => changes.push(value) })])),
  h('section', { style: 'width:720px' }, [h(AiRoleMatrix, { groupId: 'g', canEdit: true })]),
  h('section', { style: 'width:480px' }, [h(QuestionPolicyPanel)]),
  h('section', { id: 'card-fixture', style: 'position:absolute;left:800px;top:50px' }, [h(FlowNodeCard, { id: 'node', data: { kind: 'task', title: 'Long card title for wrapping', state: null, selected: false, documents: docs.value, approvable: true, gateState: { key: 'waiting_confirm', labelKey: 'gui.designer.center.card_states.waiting_confirm' } as any }, onOpenDocument: (event: any) => opened.push(event.docId) })]),
]) }).use(i18n).mount('#app')
const style = document.createElement('style'); style.textContent = '.host32 select { height:32px;font-size:12px } .host36 select {height:36px}';document.head.append(style)
;(window as any).__fixture = { opened, changes, clear: () => docs.value = [] }
await nextTick(); (window as any).__harnessReady = true
