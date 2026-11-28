import { onScopeDispose } from 'vue'

const EVENT = 'fg:ai-providers-changed'
const CHANNEL = 'fg_ai_providers'
let channel: BroadcastChannel | null = null
let subscribers = 0

function dispatch(): void {
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent(EVENT))
}

function openChannel(): BroadcastChannel | null {
  if (channel || typeof window === 'undefined' || typeof BroadcastChannel === 'undefined') return channel
  try {
    channel = new BroadcastChannel(CHANNEL)
    channel.addEventListener('message', (event: MessageEvent) => {
      // Re-read with this tab's credentials; never transmit providers or rebroadcast a received signal.
      if (event.data?.type === EVENT) dispatch()
    })
  } catch {
    // Same-tab updates still work when cross-tab messaging is unavailable.
  }
  return channel
}

function closeUnusedChannel(): void {
  if (subscribers === 0) {
    channel?.close()
    channel = null
  }
}

export function emitAiProvidersChanged(): void {
  dispatch()
  try { openChannel()?.postMessage({ type: EVENT }) } catch { /* A save already succeeded. */ }
  closeUnusedChannel()
}

export function subscribeAiProvidersChanged(listener: () => void): () => void {
  if (typeof window === 'undefined') return () => {}
  subscribers += 1
  window.addEventListener(EVENT, listener)
  openChannel()
  let stopped = false
  return () => {
    if (stopped) return
    stopped = true
    window.removeEventListener(EVENT, listener)
    subscribers -= 1
    closeUnusedChannel()
  }
}

export function useAiProvidersChanged(listener: () => void): void {
  onScopeDispose(subscribeAiProvidersChanged(listener))
}
