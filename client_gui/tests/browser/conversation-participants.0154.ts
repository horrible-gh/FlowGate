// Isolated browser geometry fixture using the product component and theme.
import { createApp, defineComponent, h, nextTick, ref } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import ConversationParticipants from '@/features/conversation/ConversationParticipants.vue'
import type { ConversationParticipant } from '@/features/conversation/types'
import '@/app/styles/theme.css'

registerAppMessages(i18n)
i18n.global.locale.value = 'ko'
const people = (count: number): ConversationParticipant[] => Array.from({ length: count }, (_, i) => ({
  key: `fixture:${i}`, kind: i ? 'ai' : 'user', displayName: i ? `Model ${i}` : '사용자', lastReadSeq: 10 + i,
}))
const app = defineComponent({ setup() {
  const count = Number(sessionStorage.getItem('participant-count') || 9)
  const participants = ref(people(count))
  const width = ref(330)
  const chat = ref(true)
  const draft = ref('fixture message')
  Object.assign(window, { __fixture: {
    async set(size: number, count: number) {
      width.value = size
      participants.value = people(count)
      sessionStorage.setItem('participant-count', String(count))
      await nextTick()
    },
    async longName() {
      participants.value = [{ key: 'fixture:long-key', kind: 'ai', displayName: 'Long model name '.repeat(30), lastReadSeq: 123456 }]
      await nextTick()
    },
    async tab(showChat: boolean) { chat.value = showChat; await nextTick() },
    ready: true,
  } })
  return () => h('main', { style: 'padding:20px' }, [
    h('button', { 'data-test': 'outside', onClick: () => { chat.value = !chat.value } }, 'Fixture tab'),
    h('section', { class: 'conv-panel', style: { width: `${width.value}px`, height: '500px', display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--fg2-bg)', fontFamily: 'var(--fg2-font)' } }, [
      h('header', { style: 'height:42px;flex:none' }, 'Conversation fixture'),
      chat.value ? h(ConversationParticipants, { participants: participants.value }) : null,
      h('div', { 'data-test': 'fixture-timeline', style: 'flex:1;min-height:0;overflow:auto' }, Array.from({ length: 30 }, (_, i) => h('p', `Message ${i}`))),
      h('textarea', { 'data-test': 'fixture-input', value: draft.value, onInput: (event: Event) => { draft.value = (event.target as HTMLTextAreaElement).value }, style: 'height:60px;flex:none' }),
    ]),
  ])
} })
createApp(app).use(i18n).mount('#app')
