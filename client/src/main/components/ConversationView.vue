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
         SEND button itself becomes a spinner while that run is in flight (R0001:
         "make the send button a rotating icon" — no dialog for chat), and the manual
         [Call AI] button spins in lockstep. -->
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
        <!-- R0001: for CH docs the AI call must NOT open a settings dialog; its
             progress shows on the SEND button itself ("보내기버튼을 회전아이콘으로").
             So the send button spins for BOTH the turn POST (sending) and the ensuing
             chat AI call (invoking) — a single, unified busy indicator — and stays
             disabled until the in-flight work settles. -->
        <button
          type="submit"
          class="conv-send"
          :class="{ 'is-sending': busy }"
          :title="sendButtonTitle"
          :aria-label="t('main.conversation_view.send')"
          :disabled="busy || draft.trim().length === 0"
        >
          <AppIcon :name="busy ? 'spinner' : 'paper-plane-tilt'" :spin="busy" />
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
// R0001: chat AI progress is shown on the SEND button (no dialog), so the send
// button is "busy" while EITHER the turn is posting OR a chat AI call is running.
const busy = computed(() => sending.value || invoking.value)
const sendButtonTitle = computed(() =>
  invoking.value
    ? t('main.conversation_view.invoke_ai_running')
    : t('main.conversation_view.send'),
)
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
const SPEAKER_ALT = '(?:🧑 )?사용자|(?:🤖 )?AI'
const HEADER_RE = new RegExp(`^##\\s+(${SPEAKER_ALT}) · (\\S+)\\s*$`)
const HEADERLIKE_RE = new RegExp(`^\\\\*##\\s+(?:${SPEAKER_ALT}) · \\S+\\s*$`)

function speakerKey(label: string): string {
  let s = label
  for (const prefix of ['🧑 ', '🤖 ']) {
    if (s.startsWith(prefix)) { s = s.slice(prefix.length); break }
  }
  if (s === '사용자') return 'user'
  if (s === 'AI') return 'ai'
  return label
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
  let curBody: string[] = []
  let started = false

  const flush = () => {
    if (curSpeaker === null) return
    const body = [...curBody]
    if (body.length && body[body.length - 1] === '') body.pop()
    parsed.push({ speaker: curSpeaker, ts: curTs, body: body.join('\n') })
  }

  for (const line of content.split('\n')) {
    const m = HEADER_RE.exec(line)
    if (m) {
      flush()
      curSpeaker = speakerKey(m[1])
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

async function load(): Promise<void> {
  if (!props.docId) return
  loading.value = true
  try {
    const res = await getRequest<{ content: string }>(
      `/api/v1/documents/content?doc_id=${encodeURIComponent(props.docId)}`,
    )
    const content = (res.data as any)?.content ?? ''
    const p = parseConversation(content)
    turns.value = p.turns
    scrollToBottom()
  } catch {
    // A fresh CH doc may have no file yet (404) — treat as an empty conversation.
    turns.value = []
  } finally {
    loading.value = false
  }
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
    if (runId) void pollRun(runId)
    else invoking.value = false
  } catch (e: any) {
    const data = e?.response?.data
    // 409 run_in_progress: a run already exists for this group — adopt it and keep
    // the spinner rather than surfacing an error or restarting (L0008 §5).
    if (data?.code === 'run_in_progress' && data?.run_id) {
      void pollRun(data.run_id)
      return
    }
    invoking.value = false
    const detail = describeErrorDetail(data?.detail ?? data ?? e)
    showToast(t('main.conversation_view.invoke_ai_failed', { detail }), 'danger')
  }
}

async function pollRun(runId: string): Promise<void> {
  invoking.value = true
  // Live SSE already reloads the conversation when the AI turn lands; polling here
  // only drives the spinner's release and the "registered nothing" failure notice.
  for (let i = 0; i < 480 && !disposed; i++) {
    await new Promise((r) => setTimeout(r, 2500))
    if (disposed) return
    try {
      const res = await getRequest<{ status?: string; docs_reached?: number }>(
        `/api/v1/ai-invoke/${encodeURIComponent(runId)}`,
      )
      const data = res.data as any
      if (data?.status === 'finished') {
        invoking.value = false
        void load()
        if ((data?.docs_reached ?? 0) === 0) {
          // The run finished but registered nothing (L0008 §2-3 notify_fail,
          // reason=not_registered) — a dedicated "nothing registered" notice, not the
          // generic call-failed toast with a meaningless {detail}.
          showToast(t('main.conversation_view.invoke_ai_no_docs'), 'danger')
        }
        return
      }
    } catch (e: any) {
      // Only a gone run is terminal: 404 (server restart / run evicted) or 410. A
      // transient network error or a one-off 5xx must NOT release the spinner or
      // swallow the docs_reached==0 notice — keep polling (L0008 §3: RUNNING→IDLE
      // only on a finished run). The conversation also still reloads on any turn via SSE.
      const status = e?.response?.status
      if (status === 404 || status === 410) {
        invoking.value = false
        return
      }
    }
  }
  invoking.value = false
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
  window.addEventListener('fg:document_content_changed', onContentChanged)
})

onBeforeUnmount(() => {
  disposed = true
  window.removeEventListener('fg:document_content_changed', onContentChanged)
})

defineExpose({ load })
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

.conv-send:active:not(:disabled) {
  transform: scale(0.94);
}

.conv-send:disabled {
  opacity: 0.45;
  cursor: default;
}
</style>
