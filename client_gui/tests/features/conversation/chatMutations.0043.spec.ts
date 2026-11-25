import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import ComposerSettings from '@/features/conversation/ComposerSettings.vue'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { mergeTurns } from '@/features/conversation/timeline'
registerAppMessages(i18n)
const turn = { seq: 1, speaker: 'user', participantKey: 'user:me', body: 'original', createdAt: null, messageVersion: 1 }

describe('0043 message changes', () => {
  it('a deletion wins over a late original page', () => {
    const deleted = { ...turn, body: '', messageVersion: 2, deletedAt: 'now' }
    expect(mergeTurns([turn], [deleted])[0]).toEqual(deleted)
    expect(mergeTurns([deleted], [turn])[0]).toEqual(deleted)
  })
  it('offers inline edits only to the author and retains failed drafts', async () => {
    const w = mount(TimelineMessage, { props: { turn, pending: false, failed: false, myParticipantKey: 'user:other' }, global: { plugins: [i18n] } })
    expect(w.find('[data-test="tl-more"]').exists()).toBe(false)
    await w.setProps({ myParticipantKey: 'user:me' })
    await w.get('[data-test="tl-more"]').trigger('click')
    await w.get('[data-test="tl-edit"]').trigger('click')
    await w.get('[data-test="tl-edit-input"]').setValue('edited')
    await w.get('[data-test="tl-mutate-save"]').trigger('click')
    const [seq, body, done] = w.emitted('mutate')![0] as [number, string, (ok:boolean, error?: string) => void]
    expect([seq, body]).toEqual([1, 'edited'])
    done(false, 'conflict')
    await w.vm.$nextTick()
    expect(w.text()).toContain('conflict')
    expect(w.find('[data-test="tl-edit-input"]').exists()).toBe(true)
    await w.setProps({ turn: { ...turn, body: '', messageVersion: 2, deletedAt: 'now' } })
    expect(w.find('[data-test="tl-copy"]').exists()).toBe(false)
    expect(w.find('[data-test="tl-deleted"]').exists()).toBe(true)
    w.unmount()
  })
  it('preserves old custom 30 while the hybrid starts at 5', async () => {
    const w = mount(ComposerSettings, { props: { settings: { ...DEFAULT_CHAT_SETTINGS, contextMode: 'recent', contextTurns: 30 }, saving: false, hasProviders: true }, global: { plugins: [i18n] } })
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    expect((w.get('[data-test="conv-setting-context"]').element as HTMLSelectElement).value).toBe('custom')
    await w.get('[data-test="conv-setting-context"]').setValue('checkpoint_recent')
    expect((w.get('[data-test="conv-setting-turns"]').element as HTMLInputElement).value).toBe('5')
    // F96-8: the pick is saved at once (no 저장 button).
    expect(w.emitted('save')![0][0]).toMatchObject({ context_mode: 'checkpoint_recent', checkpoint_max_turns: 5 })
    expect(w.emitted('save')![0][0]).not.toHaveProperty('context_turns')
    w.unmount()
  })
})


it('changed events and reconnects refresh already visible revisions', async () => {
  const { chatEventRefresh } = await import('@/features/conversation/conversationEvents')
  const { FULL_CHAT_REFRESH } = await import('@/features/conversation/conversationSession')
  const target = { projectId: 'p', groupId: 'p.m.1', chatId: 'p.m.1.1-CH' }
  expect(chatEventRefresh({ type: 'conversation_turn_appended', groupId: target.groupId, docId: target.chatId, payload: { changed: true } }, target)).toEqual({ turns: true, revised: true })
  expect(FULL_CHAT_REFRESH.revised).toBe(true)
})
