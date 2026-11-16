// flowgate.v02gui.0031 T0005 §3 — isolated browser fixture for the connection recovery scenarios
// (connection_recovery_v02gui0031.py). The product pieces run unchanged: ConversationPanel with its HTTP source, the
// shared axios client (token, retries, refresh) and the project event hub (EventSource). Only the server is a fake the
// scenario script runs. Two Flows (groups) with their own chats: the scenario switches chats and Flows like a user.
// The non-chat Designer reads go through the product's own read functions on the same client: the Flow list (group
// tree), the open Flow's graph / cards (Canvas), the Inspector document and settings reads, and the project Git status.
// A Canvas model kept like the Designer keeps it (project hub event or resync -> read again) shows whether a card
// change missed during an outage comes back without F5.
// Harness-only settings: the client timeout is cut to FIXTURE_TIMEOUT_MS so a hung server is seen in seconds.
import { createApp, defineComponent, h, reactive, ref } from 'vue'
import i18n from '@shared/i18n'
import api, { startTokenAutoRefresh } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import { registerAppMessages } from '@/app/i18n'
import ConversationPanel from '@/features/conversation/ConversationPanel.vue'
import type { ConversationSession } from '@/features/conversation/conversationSession'
import { subscribeProjectEvents } from '@/features/project/projectEvents'
import { fetchProjectGitStatus, fetchProjectGroupTree } from '@/features/project/api'
import { fetchFlowCanvas } from '@/features/project/flowCanvasApi'
import { documentRead } from '@/features/document/api/document'
import { settingsRootRead } from '@/features/document/api/settings'
import '@/app/styles/theme.css'

const FIXTURE_TIMEOUT_MS = 2500
const params = new URLSearchParams(location.search)
const PROJECT = 'p'
const FLOWS = ['p.m.0001', 'p.m.0002'] as const
// The Inspector's document of each Flow (its first card's document).
const flowDoc = (flowId: string) => `${flowId}.0001-B`

// Session as the login leaves it (tokens come from the scenario's fake server). The URL seeds it once; an F5 keeps the
// tab's session (the tokens rotated since), as the app's own reload does. The tokens then leave the URL; the open Flow
// stays in it so an F5 comes back to the same Flow.
if (params.get('access') && !sessionStorage.getItem('fg_access_token')) {
  sessionStorage.setItem('fg_access_token', params.get('access')!)
  sessionStorage.setItem('fg_refresh_token', params.get('refresh') ?? '')
}
;(window as unknown as { __accessToken__?: string }).__accessToken__ = sessionStorage.getItem('fg_access_token') ?? ''
const keepFlowInUrl = (flowId: string) => {
  const url = new URL(location.href)
  url.searchParams.delete('access')
  url.searchParams.delete('refresh')
  url.searchParams.set('flow', flowId)
  history.replaceState(null, '', url)
}
api.defaults.timeout = FIXTURE_TIMEOUT_MS
registerAppMessages(i18n)
i18n.global.locale.value = 'ko'

// Every EventSource the hub makes, with its state (the URL's token is never exposed: only whether it changed).
const streams: EventSource[] = []
const NativeEventSource = window.EventSource
class RecordedEventSource extends NativeEventSource {
  constructor(url: string | URL, init?: EventSourceInit) {
    super(url, init)
    streams.push(this)
  }
}
window.EventSource = RecordedEventSource as unknown as typeof EventSource

const counters = { hubResync: 0, tokenSignals: 0, online: 0, offline: 0, canvasEvents: 0 }
window.addEventListener('fg:access_token_refreshed', () => { counters.tokenSignals += 1 })
window.addEventListener('online', () => { counters.online += 1 })
window.addEventListener('offline', () => { counters.offline += 1 })
startTokenAutoRefresh()

// The open Flow (the panel's group): a switch is a Flow switch in the Designer (the panel re-scopes its chats).
const flow = ref<string>(params.get('flow') && FLOWS.includes(params.get('flow') as never) ? params.get('flow')! : FLOWS[0])
keepFlowInUrl(flow.value)

/** What a failed read says: its HTTP status, `network`, or `error` (anything else, e.g. an unreadable answer). */
function failureOf(error: unknown): number | string {
  const e = error as { cause?: unknown; status?: unknown; network?: unknown }
  if (typeof e?.status === 'number') return e.status
  const normalized = normalizeApiError(e?.cause ?? error)
  if (normalized.status !== null) return normalized.status
  const code = (e?.cause as { code?: unknown } | undefined)?.code ?? (error as { code?: unknown })?.code
  if (code === 'ECONNABORTED') return 'timeout'
  return normalized.network || e?.network === true ? 'network' : 'error'
}

// The Canvas as the Designer keeps it: read when the Flow opens, again on its hub event and on every resync.
const canvas = reactive({ flowId: '' as string, cards: '' as string, reads: 0, failures: 0, lastError: null as number | string | null })
let canvasRead = 0
async function readCanvas(): Promise<void> {
  const flowId = flow.value
  const read = ++canvasRead
  try {
    const graph = await fetchFlowCanvas(flowId)
    if (read !== canvasRead || flowId !== flow.value) return
    canvas.flowId = flowId
    canvas.cards = graph.nodes.map((n) => `${n.nodeKey}:${n.nodeRun?.status ?? n.state ?? ''}`).join(',')
    canvas.reads += 1
    canvas.lastError = null
  } catch (error) {
    if (read !== canvasRead) return
    canvas.failures += 1
    canvas.lastError = failureOf(error)
  }
}
subscribeProjectEvents(PROJECT, {
  events: ['graph_canvas_changed'],
  event: (frame) => {
    if (frame.groupId !== flow.value) return
    counters.canvasEvents += 1
    void readCanvas()
  },
  resync: () => {
    counters.hubResync += 1
    void readCanvas()
  },
})
void readCanvas()

const panel = ref<{ session: ConversationSession } | null>(null)
const tokenOf = (url: string) => new URL(url).searchParams.get('token') ?? ''

/**
 * One round of the Designer's non-chat reads for the open Flow, each through the product read function:
 * `status` (200 or what failed) and `data` (a short, body-free signature to compare before / after an outage and F5).
 */
async function designerProbe(): Promise<{ flow: string; status: Record<string, number | string>; data: Record<string, unknown> }> {
  const flowId = flow.value
  const status: Record<string, number | string> = {}
  const data: Record<string, unknown> = {}
  const one = async (name: string, read: () => Promise<unknown>) => {
    try {
      data[name] = await read()
      status[name] = 200
    } catch (error) {
      status[name] = failureOf(error)
      data[name] = null
    }
  }
  await Promise.all([
    one('flows', async () => (await fetchProjectGroupTree(PROJECT, { includeTerminal: true, force: true }))
      .filter((n) => n.kind === 'group').map((n) => n.id).join(',')),
    one('canvas', async () => (await fetchFlowCanvas(flowId)).nodes
      .map((n) => `${n.nodeKey}:${n.nodeRun?.status ?? n.state ?? ''}`).join(',')),
    one('document', async () => {
      const record = await documentRead(flowDoc(flowId))
      return `${record.docId}@${record.revisionNo ?? ''}`
    }),
    one('settings', async () => (await settingsRootRead(flowId)).ownership),
    one('git', async () => {
      const git = await fetchProjectGitStatus(PROJECT)
      return git ? git.slots.map((s) => `${s.groupId}:${s.status}`).join(',') : null
    }),
  ])
  return { flow: flowId, status, data }
}

function snapshot() {
  const s = panel.value?.session
  const state = s?.state
  const last = streams.at(-1)
  return {
    flow: flow.value,
    scopeGroup: state?.scope?.groupId ?? null,
    chats: (state?.chats ?? []).map((c) => c.id),
    chatsStatus: state?.chatsStatus ?? null,
    activeChatId: state?.activeChatId ?? null,
    turnsStatus: state?.turnsStatus ?? null,
    sending: state?.sending ?? null,
    runId: state?.runId ?? null,
    running: s?.running.value ?? null,
    sendAction: state?.settings.sendAction ?? null,
    providerId: state?.selectedProviderId ?? null,
    // The last notice the panel showed (its code and tone only).
    notice: state?.notice ? { code: state.notice.code, tone: state.notice.tone } : null,
    turns: (state?.turns ?? []).map((t) => t.body),
    pending: (state?.pending ?? []).map((p) => ({ localId: p.localId, body: p.body, failed: !!p.failed })),
    // The run history the chat shows (commands of each run, by run id and state; no command text).
    activities: (state?.activities ?? []).filter((a) => a.kind === 'command')
      .map((a) => a as { runId?: string | null; state?: string | null })
      .map((a) => ({ runId: a.runId ?? null, state: a.state ?? null })),
    sse: { count: streams.length, readyState: last ? last.readyState : null,
      open: streams.filter((es) => es.readyState !== EventSource.CLOSED).length,
      tokenChanged: streams.length > 1 ? tokenOf(last!.url) !== tokenOf(streams[0].url) : false,
      tokenIsCurrent: last ? tokenOf(last.url) === (window as unknown as { __accessToken__?: string }).__accessToken__ : null },
    canvas: { ...canvas },
    counters: { ...counters },
    draft: (document.querySelector('textarea') as HTMLTextAreaElement | null)?.value ?? null,
  }
}

const app = defineComponent({
  setup() {
    Object.assign(window, {
      __fixture: {
        ready: true,
        snapshot,
        designerProbe,
        session: () => panel.value?.session,
        async send(text: string) { return panel.value!.session.send(text) },
        async retryFailed() {
          const s = panel.value!.session
          const failed = s.state.pending.find((p) => p.failed)
          return failed ? s.retry(failed.localId) : null
        },
        /** Opens a chat of the open Flow (its first read goes to the server: a chat switch). */
        async openChat(chatId: string) {
          return panel.value!.session.selectChat(chatId)
        },
        /** Switches the Designer to another Flow: the panel re-scopes its chats, the Canvas reads that Flow. */
        switchFlow(flowId: string) {
          if (!FLOWS.includes(flowId as never)) return false
          flow.value = flowId
          keepFlowInUrl(flowId)
          void readCanvas()
          return true
        },
        setDraft(text: string) {
          const area = document.querySelector('textarea') as HTMLTextAreaElement | null
          if (!area) return false
          area.value = text
          area.dispatchEvent(new Event('input', { bubbles: true }))
          return true
        },
      },
    })
    return () => h(ConversationPanel, {
      ref: panel,
      projectId: PROJECT,
      groupId: flow.value,
      subscribeEvents: subscribeProjectEvents,
      active: true,
    })
  },
})
createApp(app).use(i18n).mount('#app')
