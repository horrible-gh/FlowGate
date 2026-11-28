import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { emitAiProvidersChanged, subscribeAiProvidersChanged, useAiProvidersChanged } from '@shared/aiProvidersChanged'
import { createConversationSession } from '@/features/conversation/conversationSession'
import { createFakeSource } from '../features/conversation/fakeConversationSource'

const stops: (() => void)[] = []
afterEach(() => { stops.splice(0).forEach(stop => stop()); vi.unstubAllGlobals() })

class Channel {
  static instances: Channel[] = []
  listener?: (event: { data: unknown }) => void
  postMessage = vi.fn()
  close = vi.fn()
  constructor(public name: string) { Channel.instances.push(this) }
  addEventListener(_type: string, listener: Channel['listener']) { this.listener = listener }
}

describe('AI list changes without reloading the tab', () => {
  it('delivers locally once, receives another tab without an echo, and releases subscriptions', () => {
    Channel.instances = []
    vi.stubGlobal('BroadcastChannel', Channel)
    const listener = vi.fn()
    const stop = subscribeAiProvidersChanged(listener)
    stops.push(stop)
    const channel = Channel.instances[0]
    emitAiProvidersChanged()
    expect(listener).toHaveBeenCalledTimes(1)
    expect(channel.postMessage).toHaveBeenCalledTimes(1)
    channel.listener!({ data: { type: 'fg:ai-providers-changed' } })
    expect(listener).toHaveBeenCalledTimes(2)
    expect(channel.postMessage).toHaveBeenCalledTimes(1)
    channel.listener!({ data: { type: 'other' } })
    expect(listener).toHaveBeenCalledTimes(2)
    stop(); stop()
    expect(channel.close).toHaveBeenCalledTimes(1)
    window.dispatchEvent(new CustomEvent('fg:ai-providers-changed'))
    expect(listener).toHaveBeenCalledTimes(2)
  })

  it('keeps same-tab updates when BroadcastChannel cannot be constructed and disposes a Vue scope', () => {
    vi.stubGlobal('BroadcastChannel', class { constructor() { throw new Error('blocked') } })
    const listener = vi.fn()
    const scope = effectScope()
    scope.run(() => useAiProvidersChanged(listener))
    stops.push(() => scope.stop())
    expect(() => emitAiProvidersChanged()).not.toThrow()
    expect(listener).toHaveBeenCalledTimes(1)
    scope.stop()
    emitAiProvidersChanged()
    expect(listener).toHaveBeenCalledTimes(1)
  })

  it('refreshes chat additions and renames, keeps a valid pick, falls back on removal, and stops after dispose', async () => {
    const { source } = createFakeSource()
    const session = createConversationSession({ source })
    stops.push(() => session.dispose())
    await session.setScope({ projectId: 'p', groupId: 'p.m.0001' })
    await flushPromises()
    session.selectProvider('sonnet')
    source.listProviders.mockResolvedValue({ providers: [{ id: 'sonnet', name: 'Renamed' }, { id: 'new', name: 'New AI' }], defaultProviderId: 'new' })
    emitAiProvidersChanged()
    await flushPromises()
    expect(session.state.providers.map(p => p.name)).toEqual(['Renamed', 'New AI'])
    expect(session.state.selectedProviderId).toBe('sonnet')
    source.listProviders.mockResolvedValue({ providers: [{ id: 'new', name: 'New AI' }], defaultProviderId: 'new' })
    emitAiProvidersChanged()
    await flushPromises()
    expect(session.state.selectedProviderId).toBe('new')
    expect(session.state.notice).toMatchObject({ code: 'chat_ai_fallback', detail: 'Renamed' })
    session.dispose()
    const reads = source.listProviders.mock.calls.length
    emitAiProvidersChanged()
    await flushPromises()
    expect(source.listProviders).toHaveBeenCalledTimes(reads)
  })

  it('ignores an older chat list response after a second change', async () => {
    const { source } = createFakeSource()
    const session = createConversationSession({ source })
    stops.push(() => session.dispose())
    await session.setScope({ projectId: 'p', groupId: 'p.m.0001' })
    await flushPromises()
    let resolve!: (value: any) => void
    source.listProviders.mockImplementationOnce(() => new Promise(done => { resolve = done }))
    emitAiProvidersChanged()
    source.listProviders.mockResolvedValue({ providers: [{ id: 'latest', name: 'Latest' }], defaultProviderId: 'latest' })
    emitAiProvidersChanged()
    await flushPromises()
    resolve({ providers: [{ id: 'old', name: 'Old' }], defaultProviderId: 'old' })
    await flushPromises()
    expect(session.state.providers.map(p => p.id)).toEqual(['latest'])
  })
})
