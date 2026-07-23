<template>
  <div class="conv-view">
    <div ref="scrollEl" class="conv-scroll">
      <div v-if="loading" class="conv-state">{{ t('common.loading') }}</div>
      <template v-else>
        <p v-if="turns.length === 0" class="conv-state conv-empty">
          <AppIcon name="chats" />
          {{ t('main.conversation_view.empty') }}
        </p>
        <div
          v-for="(turn, i) in turns"
          :key="i"
          class="conv-row"
          :class="turn.speaker === 'user' ? 'conv-row--user' : 'conv-row--ai'"
        >
          <div class="conv-bubble">
            <div class="conv-meta">
              <span class="conv-speaker">
                <AppIcon :name="turn.speaker === 'user' ? 'user' : 'robot'" />
                {{ turn.speaker === 'user' ? t('main.conversation_view.speaker_user') : t('main.conversation_view.speaker_ai') }}
              </span>
              <!-- 0293 R0001: which AI answered. Drawn only when the turn header recorded
                   it — a missing provider is absent information, not a warning state. -->
              <span v-if="turn.provider" class="conv-provider" :title="turn.provider">{{ turn.provider }}</span>
              <span v-if="turn.ts" class="conv-ts">{{ formatTs(turn.ts) }}</span>
            </div>
            <div class="conv-body">{{ turn.body }}</div>
          </div>
        </div>
      </template>
    </div>

    <!-- Group 0235 (D0005 / P0007 / L0008): the composer's helper row now separates
         TWO independent features: (1) a "send-time action" radio — what happens
         automatically on a successful send (copy mention / call AI / nothing) — and
         (2) the manual [Copy mention] / [Call AI] buttons. Chat AI calls run
         immediately with the header-selected provider (no settings dialog); the
         SEND button carries that run's progress (0235 R0001: no dialog for chat) and,
         since 0264 R0001, carries it as a STOP button rather than a passive spinner —
         the manual [Call AI] button still spins in lockstep. -->
    <form class="conv-composer" @submit.prevent="send">
      <!-- Inline manual-copy panel (B0001 / group 0240 — third recurrence of "a dialog
           covers the whole screen in chat"). A failed mention copy used to surface through
           notifyCopyFailure() → ClipboardFallbackModal, a fixed inset:0 overlay. On this
           HTTP LAN origin the manual [Copy mention] click fails just as reliably as the
           auto copy (token round-trip spends the click's activation before execCommand),
           so that modal appeared on essentially every attempt. R0001 asked for chat to
           stay dialog-free, so CH recovers HERE instead: same affordance as the modal —
           pre-selected text + a fresh-click [Copy again] that succeeds because the text is
           already in hand — but in the composer's flow, covering nothing. Other document
           surfaces keep the modal (0235 R0001: only CH is dialog-free). -->
      <div v-if="manualCopyText" class="conv-manualcopy">
        <div class="conv-manualcopy-hd">
          <span class="conv-manualcopy-title">
            <AppIcon name="warning" />
            {{ t('main.conversation_view.manual_copy_title') }}
          </span>
          <button type="button" class="conv-assist-btn" @click="onManualCopyAgain">
            <AppIcon name="copy" />
            {{ t('main.conversation_view.manual_copy_again') }}
          </button>
          <button type="button" class="conv-assist-btn" @click="emit('manual-copy-dismiss')">
            <AppIcon name="x" />
            {{ t('common.close') }}
          </button>
        </div>
        <p class="conv-manualcopy-hint">{{ t('main.conversation_view.manual_copy_hint') }}</p>
        <textarea
          ref="manualCopyEl"
          class="conv-manualcopy-text"
          readonly
          spellcheck="false"
          :value="manualCopyText"
          @focus="selectManualCopy"
        ></textarea>
      </div>
      <div class="conv-assist">
        <!-- Send-time action (D0005 §3-2). Replaces the old "auto-copy" checkbox.
             "Call AI" is disabled when no provider is available (single source of
             truth: aiProvider store). -->
        <div
          class="conv-sendaction"
          role="radiogroup"
          :aria-label="t('main.conversation_view.send_action_label')"
        >
          <span class="conv-sendaction-label">{{ t('main.conversation_view.send_action_label') }}</span>
          <label class="conv-radio">
            <input type="radio" value="copy_mention" v-model="sendAction" />
            {{ t('main.conversation_view.send_action_copy') }}
          </label>
          <label
            class="conv-radio"
            :class="{ 'is-disabled': !invokeSelectable }"
            :title="!invokeSelectable ? t('main.conversation_view.send_action_invoke_disabled_hint') : ''"
          >
            <input type="radio" value="invoke_ai" v-model="sendAction" :disabled="!invokeSelectable" />
            {{ t('main.conversation_view.send_action_invoke') }}
          </label>
          <label class="conv-radio">
            <input type="radio" value="none" v-model="sendAction" />
            {{ t('main.conversation_view.send_action_none') }}
          </label>
        </div>
        <div class="conv-assist-btns">
          <!-- Manual delivery fallback — copy a chat-only mention and paste it to the
               AI worker. Always available (D0005 §3-3: [Copy mention] never hidden). -->
          <button
            type="button"
            class="conv-assist-btn"
            :title="t('main.conversation_view.copy_mention_hint')"
            @click="emit('copy-mention')"
          >
            <AppIcon name="copy" />
            {{ t('main.conversation_view.copy_mention') }}
          </button>
          <!-- Manual immediate AI call. Hidden (not disabled) when no provider exists
               (D0005 §3-3); spinner while a run is in flight (중복 실행 방지). -->
          <button
            v-if="hasProviders"
            type="button"
            class="conv-assist-btn"
            :disabled="invoking"
            :title="invoking ? t('main.conversation_view.invoke_ai_running') : t('main.conversation_view.invoke_ai_hint')"
            @click="invokeAi('manual')"
          >
            <AppIcon :name="invoking ? 'spinner' : 'robot'" :spin="invoking" />
            {{ invoking ? t('main.conversation_view.invoke_ai_running') : t('main.conversation_view.invoke_ai') }}
          </button>
        </div>
      </div>
      <div class="conv-pill">
        <AiProviderSelect
          class="conv-provider-select"
          :providers="providerStore.providers"
          :model-value="providerStore.selectedProviderId"
          :loading="providersResolving"
          :errored="Boolean(providerStore.error)"
          hide-label
          @update:model-value="providerStore.selectProvider"
        />
        <textarea
          ref="inputEl"
          v-model="draft"
          class="conv-input"
          :placeholder="t('main.conversation_view.placeholder')"
          :disabled="sending"
          rows="1"
          @input="onDraftInput"
          @keydown.enter.exact.prevent="send"
        ></textarea>
        <!-- 0264 R0001: a chat AI run can hold this surface for up to 20 minutes, and a
             bare spinner left the user with nothing to do but wait ("멍때리는"). So the
             send button is a TWO-MODE button: the plane sends, and while a run is in
             flight it turns into a STOP button that cancels the run. Chat progress still
             lives on the send button with no dialog (0235 R0001) — the passive spinner is
             simply replaced by an actionable control.
             The short turn POST (sending) keeps the old spinner: it is sub-second, has no
             cancel endpoint, and is already committed server-side, so there is nothing to
             stop. Only the AI run (invoking) is cancellable. -->
        <button
          :type="stopMode ? 'button' : 'submit'"
          class="conv-send"
          :class="{ 'is-sending': sending && !stopMode, 'is-stop': stopMode }"
          :title="sendButtonTitle"
          :aria-label="stopMode ? t('main.ai_invoke_dialog.btn_cancel_run') : t('main.conversation_view.send')"
          :disabled="stopMode ? cancelling : sending || draft.trim().length === 0"
          @click="onSendButtonClick"
        >
          <AppIcon :name="sendButtonIcon" :spin="sending && !stopMode" />
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { useAiProviderStore } from '../stores/aiProvider'
import { consumeLastFailedCopyText, copyToClipboard } from '../utils/clipboard'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'

interface ConvTurn {
  speaker: string // 'user' | 'ai' | raw label
  ts: string
  body: string
  provider?: string // 0293: header's parenthesized provider; absent = not recorded
}

const props = defineProps<{
  docId: string
  projectId?: string | null
  // Text of a mention copy that failed to reach the clipboard (B0001 / group 0240). The
  // parent (MainPanel.onConversationCopyMention) hands it over instead of opening the
  // full-screen fallback modal; null/empty hides the inline panel.
  manualCopyText?: string | null
}>()

const emit = defineEmits<{
  // TR0044.0010 rev3: ask the parent (MainPanel) to copy an edit-scope mention for
  // this CH doc — manual turn delivery until live chat lands.
  // 0085: the optional payload carries { auto: true } when the copy was triggered
  // automatically by a send (vs. a manual button click), so the parent can stay quiet.
  'copy-mention': [opts?: { auto?: boolean }]
  // Group 0223: run the chat turn through the in-app AI provider instead of the
  // manual copy-paste loop. Kept for backward-compat; group 0235 moved the actual
  // immediate run in-component (invokeAi), so this is no longer fired for CH.
  'invoke-ai': []
  // Group 0240: the inline manual-copy panel is done (copied or dismissed) — the parent
  // owns the failed text, so it clears it.
  'manual-copy-dismiss': []
}>()

const { t } = useI18n()
const { showToast } = useToast()
const providerStore = useAiProviderStore()

interface AccessTokenPayload {
  username?: string
  sub?: string
  user_id?: string
}

function getDraftUserId(): string {
  try {
    const token = window.__accessToken__
    if (!token) return 'guest'
    const payload = JSON.parse(atob(token.split('.')[1])) as AccessTokenPayload
    return String(payload.username ?? payload.sub ?? payload.user_id ?? 'guest')
  } catch {
    return 'guest'
  }
}

function draftStorageKey(docId: string): string {
  return 'flowgate.user.' + getDraftUserId() + '.chat.drafts.' + docId
}

function loadDraft(docId: string): string {
  if (!docId) return ''
  try {
    return localStorage.getItem(draftStorageKey(docId)) ?? ''
  } catch {
    return ''
  }
}

function persistDraft(docId: string, value: string): void {
  if (!docId) return
  try {
    const key = draftStorageKey(docId)
    if (value === '') localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    /* Draft persistence is best-effort; chat input and sending must keep working. */
  }
}

// ── Send-time action (D0005 §3-2, P0007 §0-1, L0008 §2-1) ────────────────────
// One global user preference: what to do automatically on a successful send.
// Stored under a new key; the old boolean "auto-copy" key is migrated once on read
// ('1' → copy_mention, else → none) then removed so it never re-migrates.
const SEND_ACTION_KEY = 'flowgate.chat.sendAction'
const LEGACY_AUTOCOPY_KEY = 'flowgate.chat.autoCopyMention'
const SEND_ACTIONS = ['copy_mention', 'invoke_ai', 'none'] as const
type SendAction = (typeof SEND_ACTIONS)[number]

function readSendAction(): SendAction {
  try {
    const v = localStorage.getItem(SEND_ACTION_KEY)
    if (v && (SEND_ACTIONS as readonly string[]).includes(v)) return v as SendAction
    if (v !== null) return 'none' // present but out of domain
    const legacy = localStorage.getItem(LEGACY_AUTOCOPY_KEY)
    if (legacy === null) return 'none' // new user
    const migrated: SendAction = legacy === '1' ? 'copy_mention' : 'none'
    localStorage.setItem(SEND_ACTION_KEY, migrated)
    localStorage.removeItem(LEGACY_AUTOCOPY_KEY)
    return migrated
  } catch {
    return 'none'
  }
}

const sendAction = ref<SendAction>(readSendAction())
watch(sendAction, (v) => {
  try {
    localStorage.setItem(SEND_ACTION_KEY, v)
  } catch {
    /* ignore — best-effort persistence */
  }
})

// ── Provider availability (D0005 §3-3, L0008 §2-4) — single source of truth ──
// The gating project is the tab's projectId when the parent supplies one, but CH
// tabs opened from the group tree (GroupExplorer.openDocument) carry NO projectId,
// so fall back to the project code embedded in the doc id
// (project.module.group.seq-TYPE). Without this fallback the provider store never
// loaded for those tabs and [Call AI] stayed hidden/disabled even when a provider
// WAS registered. Kept in lockstep with invokeAi(), which resolves the same code.
const projectCode = computed(() => props.projectId || props.docId.split('.')[0] || '')
const providersResolving = computed(
  () => providerStore.loading || providerStore.loadedProjectId !== projectCode.value,
)
const hasProviders = computed(
  () => providerStore.loadedProjectId === projectCode.value && providerStore.providers.length > 0,
)
// "Call AI" (radio + manual button) is selectable only when a provider exists.
const invokeSelectable = computed(() => hasProviders.value)

// Fallback (L0008 §4-3): once the provider list has resolved to EMPTY, a stale
// "invoke_ai" selection reverts to "none". Never fires while still resolving, so a
// transient empty list during load can't wipe a valid selection.
watch([providersResolving, hasProviders], () => {
  if (!providersResolving.value && !hasProviders.value && sendAction.value === 'invoke_ai') {
    sendAction.value = 'none'
  }
})

const turns = ref<ConvTurn[]>([])
const draft = ref(loadDraft(props.docId))
const loading = ref(false)
const sending = ref(false)
const invoking = ref(false)
// 0264 R0001: the run id the STOP button cancels. Set by pollRun — the single choke
// point every run reaches, whether it came from our own start, from the 409
// run_in_progress adoption, or from adoptActiveRun() after a tab switch / F5 — so the
// button can stop a run this component did not itself start.
const activeRunId = ref<string | null>(null)
const cancelling = ref(false)
// One turn at a time: a new send is blocked while EITHER the turn is posting OR a chat
// AI call is running. This guards send() only — the button's own appearance is driven by
// sending/stopMode, which distinguish the two states the user can act on.
const busy = computed(() => sending.value || invoking.value)
// The button is a STOP button exactly while a cancellable AI run is in flight.
const stopMode = computed(() => invoking.value)
const sendButtonIcon = computed(() => {
  if (stopMode.value) return 'prohibit'
  return sending.value ? 'spinner' : 'paper-plane-tilt'
})
const sendButtonTitle = computed(() => {
  if (cancelling.value) return t('main.ai_invoke_dialog.cancelling')
  if (stopMode.value) return t('main.ai_invoke_dialog.btn_cancel_run')
  return t('main.conversation_view.send')
})
const scrollEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)
let disposed = false

// ── Inline manual-copy recovery (B0001 / group 0240) ─────────────────────────
const manualCopyEl = ref<HTMLTextAreaElement | null>(null)

// Pre-select the text when the panel opens so a bare Ctrl+C already works — the one
// recovery that never depends on transient activation.
watch(
  () => props.manualCopyText,
  async (text) => {
    if (!text) return
    await nextTick()
    manualCopyEl.value?.focus()
    manualCopyEl.value?.select()
  },
)

function selectManualCopy() {
  manualCopyEl.value?.select()
}

async function onManualCopyAgain() {
  const text = props.manualCopyText ?? ''
  if (!text) return
  // The text is already in hand, so the write happens in this click's synchronous stack —
  // no token round-trip in between. That is exactly why the modal's [Copy again] worked
  // where the original deferred write failed, and it works the same inline.
  const ok = await copyToClipboard(text)
  if (ok) {
    showToast(t('main.conversation_view.manual_copy_copied'), 'success')
    emit('manual-copy-dismiss')
    return
  }
  // Still failed: keep the panel open with the text selected for a hand copy, and discard
  // the recorded failed text so an unrelated later failure elsewhere (which pulls the last
  // failed text when it has none of its own) can't resurface this mention in the modal.
  consumeLastFailedCopyText()
  showToast(t('main.conversation_view.manual_copy_retry_failed'), 'warning')
  manualCopyEl.value?.focus()
  manualCopyEl.value?.select()
}

// ── Wire-format parser — the render side of L0044.0008 §6. Mirrors the server's
// conversation.parse_conversation: lines matching the turn header are boundaries,
// everything before the first boundary is the intro (turn 0), and header-like body
// lines are unescaped. The server is the single source of truth for the format; this
// only reads it.
// R0127.0001: the leading emoji is OPTIONAL on parse, so a hand-typed header
// ("## AI · …", "## 사용자 · …", or "##  AI · …" with extra spaces) is still read as
// a turn. Kept in lockstep with the server (conversation.py is the source of truth).
// 0293 R0001: the AI label may carry the provider in parentheses —
// "## 🤖 AI(claude-opus-4-8) · …". Optional, so every pre-0293 turn parses unchanged.
// 0306 NR0003 발견 1: the user label is localized — ko 사용자 / en User / ja ユーザー,
// emoji still optional — and all normalize to 'user' (speakerKey). The server writes
// each new user turn in its author's locale; this side only needs to READ every
// locale's header so a mixed-language CH renders every turn with the right role.
const USER_NAMES = ['사용자', 'User', 'ユーザー']
const SPEAKER_ALT = '(?:🧑 )?(?:사용자|User|ユーザー)|(?:🤖 )?AI(?:\\([^)]*\\))?'
const HEADER_RE = new RegExp(`^##\\s+(${SPEAKER_ALT}) · (\\S+)\\s*$`)
const HEADERLIKE_RE = new RegExp(`^\\\\*##\\s+(?:${SPEAKER_ALT}) · \\S+\\s*$`)
const PROVIDER_RE = /^(.*?)\(([^)]*)\)$/

function stripSpeakerDecorations(label: string): { bare: string; provider?: string } {
  let s = label
  for (const prefix of ['🧑 ', '🤖 ']) {
    if (s.startsWith(prefix)) { s = s.slice(prefix.length); break }
  }
  const m = PROVIDER_RE.exec(s)
  if (!m) return { bare: s }
  const provider = m[2].trim()
  return { bare: m[1], ...(provider ? { provider } : {}) }
}

// The provider suffix MUST be stripped here, not just ignored by the renderer. The chat
// AI-call success test counts turns with speaker === 'ai' before and after the run
// (pollRun); a label that kept its parentheses would still render as an AI bubble but
// never be counted, so every successful reply would be reported as "no reply".
function speakerKey(label: string): string {
  const { bare } = stripSpeakerDecorations(label)
  if (USER_NAMES.includes(bare)) return 'user'
  if (bare === 'AI') return 'ai'
  return label
}

// undefined = not recorded (pre-0293 turn, or a model that does not know its own name).
// The badge is simply omitted — absence of information, not a warning.
function speakerProvider(label: string): string | undefined {
  const { bare, provider } = stripSpeakerDecorations(label)
  return bare === 'AI' || USER_NAMES.includes(bare) ? provider : undefined
}

function unescapeLine(line: string): string {
  if (line.startsWith('\\') && HEADERLIKE_RE.test(line.slice(1))) return line.slice(1)
  return line
}

function parseConversation(content: string): { intro: string; turns: ConvTurn[] } {
  const introLines: string[] = []
  const parsed: ConvTurn[] = []
  let curSpeaker: string | null = null
  let curTs = ''
  let curProvider: string | undefined
  let curBody: string[] = []
  let started = false

  const flush = () => {
    if (curSpeaker === null) return
    const body = [...curBody]
    if (body.length && body[body.length - 1] === '') body.pop()
    parsed.push({
      speaker: curSpeaker,
      ts: curTs,
      body: body.join('\n'),
      ...(curProvider ? { provider: curProvider } : {}),
    })
  }

  for (const line of content.split('\n')) {
    const m = HEADER_RE.exec(line)
    if (m) {
      flush()
      curSpeaker = speakerKey(m[1])
      curProvider = speakerProvider(m[1])
      curTs = m[2]
      curBody = []
      started = true
    } else if (!started) {
      introLines.push(line)
    } else {
      curBody.push(unescapeLine(line))
    }
  }
  flush()
  return { intro: introLines.join('\n').replace(/\n+$/, ''), turns: parsed }
}

function formatTs(ts: string): string {
  const d = new Date(ts)
  if (isNaN(d.getTime())) return ts
  return d.toLocaleString()
}

// Auto-grow the input pill as the user types (capped by max-height in CSS) so a long
// message expands smoothly instead of scrolling inside a fixed two-line box.
function autoGrow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 140)}px`
}

function onDraftInput() {
  persistDraft(props.docId, draft.value)
  autoGrow()
}

function resetInputHeight() {
  const el = inputEl.value
  if (el) el.style.height = 'auto'
}

function describeErrorDetail(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(describeErrorDetail).filter(Boolean).join('; ')
  }
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>
    const loc = Array.isArray(record.loc)
      ? record.loc.join('.')
      : typeof record.loc === 'string'
        ? record.loc
        : ''
    const msg = typeof record.msg === 'string'
      ? record.msg
      : typeof record.message === 'string'
        ? record.message
        : ''
    const type = typeof record.type === 'string' ? ` (${record.type})` : ''
    if (loc && msg) return `${loc}: ${msg}${type}`
    if (msg) return `${msg}${type}`
    try {
      return JSON.stringify(value)
    } catch {
      return String(value)
    }
  }
  return String(value)
}
function scrollToBottom() {
  const pin = () => {
    const el = scrollEl.value
    if (el) el.scrollTop = el.scrollHeight
  }
  void nextTick(() => {
    pin()
    // TR0044.0010 rev8: the flex height chain and wrapped bubbles may not have
    // their final layout at microtask (nextTick) time, so a single pin left the
    // log stuck at the TOP after a new turn ("it updates to the top"). Re-pin after the
    // browser has actually painted (two rAFs) so the newest message is always the
    // one in view at the bottom.
    requestAnimationFrame(() => requestAnimationFrame(pin))
  })
}

async function load(): Promise<ConvTurn[]> {
  if (!props.docId) return []
  loading.value = true
  try {
    const res = await getRequest<{ content: string }>(
      `/api/v1/documents/content?doc_id=${encodeURIComponent(props.docId)}`,
    )
    const content = (res.data as any)?.content ?? ''
    const p = parseConversation(content)
    turns.value = p.turns
    scrollToBottom()
    return p.turns
  } catch {
    // A fresh CH doc may have no file yet (404) — treat as an empty conversation.
    turns.value = []
    return []
  } finally {
    loading.value = false
  }
}

// Back to IDLE: no run is in flight, so the button is a send button again.
function releaseRun(): void {
  invoking.value = false
  cancelling.value = false
  activeRunId.value = null
}

// ── STOP (0264 R0001) ────────────────────────────────────────────────────────
// The run lives in a server-side worker thread, not in this request, so stopping it
// means asking the server to kill it — there is no XHR to abort and no token stream to
// interrupt. Mirrors aiInvokeRuns.cancel(), including the race where the run finishes
// naturally between our click and the server handling it.
async function cancelRun(): Promise<void> {
  const runId = activeRunId.value
  if (!runId || cancelling.value) return
  cancelling.value = true
  try {
    await postRequest(`/api/v1/ai-invoke/${encodeURIComponent(runId)}/cancel`, {})
    // 200 with status 'cancelling' OR 'finished' (cancel raced the natural finish):
    // either way the run is settled server-side. Let pollRun observe the finish and
    // release the button, so the terminal payload drives the outcome exactly once.
  } catch (e: any) {
    const status = e?.response?.status
    if (status === 404 || status === 410) {
      // The run is already gone (server restart / evicted). Nothing to kill; pollRun
      // hits the same 404 and releases the button.
      return
    }
    // The run is still up — let the user try again rather than stranding the button
    // in a cancelling state that will never resolve.
    cancelling.value = false
    showToast(t('main.ai_invoke_dialog.error_cancel_failed'), 'danger')
  }
}

// The send button submits the form in send mode; in stop mode it is type="button", so
// this is the only path that fires and it cannot fall through to a send.
function onSendButtonClick(): void {
  if (stopMode.value) void cancelRun()
}

// ── Chat immediate AI call (D0005 §3-1, L0008 §2-3 / §3) ─────────────────────
// Runs the header-selected provider directly (no settings dialog). Owns the spinner
// state and prevents duplicate runs; the server also enforces one run per group
// (409 run_in_progress), which we adopt rather than restart.
async function invokeAi(trigger: 'manual' | 'auto'): Promise<void> {
  if (invoking.value) return // 중복 실행 방지 (client guard)
  if (!hasProviders.value) {
    if (trigger === 'manual') {
      showToast(t('main.conversation_view.send_action_invoke_disabled_hint'), 'warning')
    }
    return
  }
  const parts = props.docId.split('.')
  if (parts.length < 4) {
    showToast(t('main.conversation_view.invoke_ai_failed', { detail: props.docId }), 'danger')
    return
  }
  const project = projectCode.value || parts[0]
  const moduleCode = parts[1]
  const groupCode = parts[2]
  invoking.value = true
  try {
    const res = await postRequest<{ ok: boolean; run_id?: string }>('/api/v1/ai-invoke/start', {
      project,
      module: moduleCode,
      group: groupCode,
      doc_ref: props.docId,
      action_scope: 'chat',
      mode: 'single',
      provider_id: providerStore.selectedProviderId || undefined,
    })
    const runId = (res.data as any)?.run_id
    if (runId) void pollRun(runId, turns.value.filter((turn) => turn.speaker === 'ai').length)
    else releaseRun()
  } catch (e: any) {
    const data = e?.response?.data
    // 409 run_in_progress: a run already exists for this group — adopt it and keep
    // the stop button rather than surfacing an error or restarting (L0008 §5).
    if (data?.code === 'run_in_progress' && data?.run_id) {
      void pollRun(data.run_id, turns.value.filter((turn) => turn.speaker === 'ai').length)
      return
    }
    releaseRun()
    const detail = describeErrorDetail(data?.detail ?? data ?? e)
    showToast(t('main.conversation_view.invoke_ai_failed', { detail }), 'danger')
  }
}

// Re-attach to a chat AI call that is still running server-side but whose poll loop died
// with a previous instance of this component — a tab switch or an F5 unmounts the card
// outright, and the spinner state lives only here (0251 B0001 / NR0003 §5, B안). Without
// this the surface comes back idle and the docs_reached==0 notice is lost, until the user
// happens to press [Call AI] again and adopts the run through the 409 path.
// Only this chat's own run is adopted (doc_ref match) — the group-scoped runs of other
// surfaces already have their own indicator in AiInvokeInline.
async function adoptActiveRun(): Promise<void> {
  if (invoking.value) return
  const parts = props.docId.split('.')
  if (parts.length < 4) return
  const groupId = `${projectCode.value || parts[0]}.${parts[1]}.${parts[2]}`
  try {
    const res = await getRequest<{
      active?: boolean
      run_id?: string
      status?: string
      doc_ref?: string
    }>('/api/v1/ai-invoke/active', { group_id: groupId })
    const data = res.data as any
    if (disposed || invoking.value) return
    if (!data?.active || !data?.run_id) return
    if (data.doc_ref !== props.docId) return
    if (data.status === 'finished') return
    void pollRun(data.run_id, turns.value.filter((turn) => turn.speaker === 'ai').length)
  } catch {
    // Best effort: an idle surface is the status quo, and a manual [Call AI] still adopts
    // the run through the 409 run_in_progress path.
  }
}

// 0278 NR0003: the concrete per-provider cause (spawn_failed plus the OS error text)
// already rides in the finished payload as fallback_history, but nothing on the chat
// surface ever showed it -- which is what made 0278 R0001 undiagnosable from a user
// report alone.
function firstFallbackDetail(data: Record<string, any>): string {
  const history = Array.isArray(data.fallback_history) ? data.fallback_history : []
  for (const item of history) {
    const detail = typeof item?.detail === 'string' ? item.detail.trim() : ''
    if (detail) return detail.length > 200 ? `${detail.slice(0, 200)}...` : detail
  }
  return ''
}

function chatRunFailureDetail(data: Record<string, any>): string {
  const registerErrors = Array.isArray(data.register_errors) ? data.register_errors : []
  if (registerErrors.length > 0) {
    return registerErrors
      .map((error: any) => `${error?.reason || 'registration error'}${error?.status ? ` (HTTP ${error.status})` : ''}`)
      .join('; ')
  }
  if (data.turn_limit_exhausted) return t('main.ai_invoke_dialog.turn_limit_exhausted')
  if (Number(data.tool_call_misses) > 0) {
    return t('main.ai_invoke_dialog.tool_not_called', { count: Number(data.tool_call_misses) })
  }
  if (data.end_reason === 'cancelled') return t('main.ai_invoke_dialog.end_cancelled')
  if (data.end_reason === 'timeout') return t('main.ai_invoke_dialog.end_timeout')
  // 0278 NR0003: the server stamps end_reason='all_providers_failed' (ai_invoke_service
  // _worker). Comparing only against the legacy 'all_failed' never matched, so a provider
  // chain that never started fell through to the last_message_none branch below and
  // reported "no message received" for what is actually a startup failure. AiInvokeInline
  // already accepts both spellings -- keep the two surfaces in agreement.
  if (data.end_reason === 'all_providers_failed' || data.end_reason === 'all_failed') {
    const label = t('main.ai_invoke_dialog.end_all_failed')
    const cause = firstFallbackDetail(data)
    return cause ? `${label} (${cause})` : label
  }
  if (data.exit_code != null && Number(data.exit_code) !== 0) return `exit code ${data.exit_code}`
  if (!data.last_message_received) return t('main.ai_invoke_dialog.last_message_none')
  // A chat run never registers a document, so the document-flavoured wording never applied
  // to this last-resort branch (0259 B0001).
  return t('main.ai_invoke_dialog.oracle_no_result')
}

async function pollRun(runId: string, baselineAiTurns: number): Promise<void> {
  invoking.value = true
  // Every route to a live run funnels through here (own start / 409 adoption /
  // adoptActiveRun), so this is where the STOP button gets its target.
  activeRunId.value = runId
  // docs_reached counts newly created documents and is not a chat success signal. Chat
  // success is verified against the existing CH document: at least one new AI turn must
  // appear after this run started. If it does not, keep the toast and include the run's
  // concrete terminal cause instead of hiding the failure or showing a false generic one.
  for (let i = 0; i < 480 && !disposed; i++) {
    await new Promise((r) => setTimeout(r, 2500))
    if (disposed) return
    try {
      const res = await getRequest<Record<string, any>>(
        `/api/v1/ai-invoke/${encodeURIComponent(runId)}`,
      )
      const data = res.data as any
      if (data?.status === 'finished') {
        // A stop kills the worker, so the run ends with no new AI turn — the exact shape
        // of a failed run. The terminal payload, not our local cancelling flag, decides:
        // a cancel that LOST the race to a natural finish leaves end_reason untouched and
        // the reply still lands, so keying off the flag would report a delivered reply as
        // cancelled. The server always stamps end_reason='cancelled' on a real kill.
        const stopped = data?.end_reason === 'cancelled'
        releaseRun()
        const loadedTurns = await load()
        const aiTurns = loadedTurns.filter((turn) => turn.speaker === 'ai').length
        // A run the user stopped on purpose is not a failure (0264 R0001).
        if (stopped) {
          showToast(t('main.ai_invoke_dialog.end_cancelled'), 'info')
          return
        }
        if (aiTurns <= baselineAiTurns) {
          showToast(
            t('main.conversation_view.invoke_ai_no_docs', { detail: chatRunFailureDetail(data) }),
            'danger',
          )
        }
        return
      }
    } catch (e: any) {
      // Only a gone run is terminal: 404 (server restart / run evicted) or 410. A
      // transient network error or a one-off 5xx must NOT release the spinner — keep
      // polling (L0008 §3: RUNNING→IDLE only on a finished run). The conversation also
      // still reloads on any turn via SSE.
      const status = e?.response?.status
      if (status === 404 || status === 410) {
        releaseRun()
        return
      }
    }
  }
  releaseRun()
}

async function send(): Promise<void> {
  const text = draft.value.trim()
  // Block a new send while the send button is busy — either already posting a turn
  // OR spinning through a chat AI call (R0001: one turn at a time, progress on send).
  if (!text || busy.value) return
  sending.value = true
  try {
    const res = await postRequest<{ content: string; carried_over_doc_id?: string }>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/conversation/turn`,
      { body: text, speaker: 'user' },
    )
    const data = res.data as any
    const p = parseConversation(data?.content ?? '')
    turns.value = p.turns
    draft.value = ''
    persistDraft(props.docId, '')
    resetInputHeight()
    scrollToBottom()
    if (data?.carried_over_doc_id) {
      showToast(
        t('main.conversation_view.carried_over', { doc: data.carried_over_doc_id }),
        'info',
      )
    }
    // D0005 §3-2: dispatch the send-time action only AFTER the turn was accepted
    // (turns refreshed above). Never fires on a failed send (catch below).
    const action = sendAction.value
    if (action === 'copy_mention') {
      emit('copy-mention', { auto: true })
    } else if (action === 'invoke_ai') {
      void invokeAi('auto')
    }
    void nextTick(() => inputEl.value?.focus())
  } catch (e: any) {
    const detail = describeErrorDetail(e?.response?.data?.detail ?? e?.response?.data ?? e)
    showToast(t('main.conversation_view.send_failed', { detail }), 'danger')
  } finally {
    sending.value = false
  }
}

// Live updates: the server broadcasts a DOCUMENT_EXPLORER_REFRESH on every turn
// (§8). useFlowGateSse re-dispatches it as fg:document_content_changed, so an AI
// reply (or a turn from another tab) reloads the chat without an F5 — the same
// bridge MdViewer uses.
function onContentChanged(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string | null } | undefined
  if (detail?.doc_id !== props.docId) return
  void load()
}

watch(() => props.docId, (docId) => {
  draft.value = loadDraft(docId)
  void nextTick(autoGrow)
  void load()
})

// Load the provider list whenever the RESOLVED project changes — either the
// projectId prop or the doc id it is derived from (single source of truth, L0008 §2-4).
watch(projectCode, (code) => {
  if (code) void providerStore.ensureLoaded(code)
})

onMounted(() => {
  void load()
  void nextTick(autoGrow)
  if (projectCode.value) void providerStore.ensureLoaded(projectCode.value)
  void adoptActiveRun()
  window.addEventListener('fg:document_content_changed', onContentChanged)
})

onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('fg:document_content_changed', onContentChanged)
})

// scrollToBottom is exposed for the CH full view (0263 R0001): teleporting this component
// between the card and the dialog detaches and re-attaches .conv-scroll, and a re-attached
// element comes back at scrollTop 0 — the log stuck at the TOP, the same symptom rev8 fixed
// for new turns. The mover re-pins once the node lands.
defineExpose({ load, scrollToBottom })
</script>

<style scoped>
.conv-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
}

.conv-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.conv-state {
  text-align: center;
  opacity: 0.6;
  padding: 24px;
}

.conv-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  font-size: .85rem;
}

.conv-empty i {
  font-size: 1.6rem;
}

.conv-row {
  display: flex;
}

.conv-row--user {
  justify-content: flex-end;
}

.conv-row--ai {
  justify-content: flex-start;
}

.conv-bubble {
  max-width: 78%;
  border-radius: 12px;
  padding: 8px 12px;
  border: 1px solid var(--border);
}

.conv-row--user .conv-bubble {
  background: #e0f2fe;
  border-color: #bae6fd;
  border-bottom-right-radius: 3px;
}

.conv-row--ai .conv-bubble {
  background: var(--bg-card, #fff);
  border-bottom-left-radius: 3px;
}

.conv-meta {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 3px;
}

.conv-speaker {
  font-size: .72rem;
  font-weight: 700;
  color: var(--text-m);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* Provider badge (0293). Deliberately quieter than the speaker label: it answers
   "which AI", it is not the thing you read first. Truncates rather than wrapping so a
   long model id cannot push the timestamp out of the meta row. */
.conv-provider {
  font-size: .64rem;
  font-weight: 600;
  color: var(--text-m);
  background: var(--bg-subtle, #f1f5f9);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 999px;
  padding: 0 6px;
  max-width: 14rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-ts {
  font-size: .66rem;
  color: var(--text-m);
  opacity: 0.75;
}

.conv-body {
  font-size: .82rem;
  line-height: 1.6;
  color: var(--text-s);
  white-space: pre-wrap;
  word-break: break-word;
}

/* Modern chat composer (rev5): a subtle helper row above a single rounded message
   pill that holds the textarea + a circular send button. Group 0235: the helper row
   splits into a send-time action radio group (left) and the manual buttons (right). */
.conv-composer {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px 14px;
  border-top: 1px solid var(--border);
  background: var(--bg-card, #fff);
}

.conv-assist {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

/* Inline manual-copy panel (B0001 / group 0240) — in the composer's flow, NOT an
   overlay: no position:fixed, no inset:0, no backdrop. It pushes the composer up a
   little and the conversation stays readable and interactive behind/above it. */
.conv-manualcopy {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid var(--warning, #f59e0b);
  border-radius: 8px;
  background: color-mix(in srgb, var(--warning, #f59e0b) 8%, transparent);
}

.conv-manualcopy-hd {
  display: flex;
  align-items: center;
  gap: 6px;
}

.conv-manualcopy-title {
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: .72rem;
  font-weight: 700;
  color: var(--text-m);
}

.conv-manualcopy-title i {
  color: var(--warning, #f59e0b);
}

.conv-manualcopy-hint {
  margin: 0;
  font-size: .68rem;
  line-height: 1.5;
  color: var(--text-m);
}

.conv-manualcopy-text {
  width: 100%;
  height: 84px;
  resize: vertical;
  font-family: 'JetBrains Mono', monospace;
  font-size: .7rem;
  line-height: 1.45;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg, #fff);
  color: var(--text);
  white-space: pre;
}

/* Send-time action radio group (D0005 §3-2). */
.conv-sendaction {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: .7rem;
  color: var(--text-m);
}

.conv-sendaction-label {
  font-weight: 700;
  opacity: 0.85;
}

.conv-radio {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  cursor: pointer;
  user-select: none;
}

.conv-radio input {
  cursor: pointer;
  margin: 0;
}

.conv-radio.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.conv-radio.is-disabled input {
  cursor: not-allowed;
}

.conv-assist-btns {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.conv-assist-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  font-size: .7rem;
  font-family: inherit;
  color: var(--text-m);
  background: transparent;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background .12s, color .12s;
}

.conv-assist-btn:hover:not(:disabled) {
  background: var(--bg, #f1f5f9);
  color: var(--primary);
}

.conv-assist-btn:disabled {
  opacity: 0.6;
  cursor: default;
}

/* The message pill: textarea + send, framed as one rounded control. */
.conv-pill {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 6px 6px 6px 14px;
  border: 1px solid var(--border);
  border-radius: 22px;
  background: var(--bg, #fff);
  transition: border-color .12s, box-shadow .12s;
}

.conv-pill:focus-within {
  border-color: var(--primary);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary) 14%, transparent);
}

.conv-provider-select {
  flex: 0 1 160px;
  min-width: 96px;
  max-width: 180px;
}

.conv-provider-select :deep(.aip-select-input) {
  width: 100%;
  min-width: 0;
}

@media (max-width: 640px) {
  .conv-provider-select {
    flex-basis: 112px;
    max-width: 112px;
  }
}

.conv-input {
  flex: 1;
  min-width: 0;
  resize: none;
  min-height: 24px;
  max-height: 140px;
  padding: 5px 0;
  font-size: .84rem;
  font-family: inherit;
  line-height: 1.5;
  border: none;
  background: transparent;
  color: var(--text);
  overflow-y: auto;
}

.conv-input:focus {
  outline: none;
}

/* Circular send button — primary action, icon only. */
.conv-send {
  flex: 0 0 auto;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  padding: 0;
  font-size: .9rem;
  color: #fff;
  background: var(--primary, #2563eb);
  border: none;
  border-radius: 50%;
  cursor: pointer;
  transition: background .12s, transform .08s, opacity .12s;
}

.conv-send:hover:not(:disabled) {
  background: var(--primary-dark, #1d4ed8);
}

/* Stop mode (0264 R0001): reads as an interrupt, not as another way to send. */
.conv-send.is-stop {
  background: var(--danger, #dc2626);
}

.conv-send.is-stop:hover:not(:disabled) {
  background: #b91c1c;
}

.conv-send:active:not(:disabled) {
  transform: scale(0.94);
}

.conv-send:disabled {
  opacity: 0.45;
  cursor: default;
}
</style>