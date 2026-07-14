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

    <!-- TR0044.0010 rev5: a clean, modern chat composer. The manual-delivery helper
         ([Copy mention]) sits on its own subtle row above, so it no longer competes with the
         primary input. The message bar itself is a single rounded "pill" holding the
         textarea and a circular send button — input / copy-mention / send are visually
         distinct layers instead of three controls jammed into one strip. -->
    <form class="conv-composer" @submit.prevent="send">
      <div class="conv-assist">
        <!-- 0085: when "auto-copy" is checked, every successful send also copies the
             mention so the user no longer has to click "Copy mention" each turn. The
             checkbox sits to the LEFT of the copy button (per R0001). -->
        <label class="conv-auto" :title="t('main.conversation_view.auto_copy_hint')">
          <input type="checkbox" v-model="autoCopy" />
          {{ t('main.conversation_view.auto_copy') }}
        </label>
        <!-- Real-time chat isn't wired yet, so the AI's turn is delivered by hand —
             copy a chat-only mention and paste it to the AI worker, which reads this
             conversation and posts its reply. -->
        <button
          type="button"
          class="conv-assist-btn"
          :title="t('main.conversation_view.copy_mention_hint')"
          @click="emit('copy-mention')"
        >
          <AppIcon name="copy" />
          {{ t('main.conversation_view.copy_mention') }}
        </button>
        <!-- Group 0223: same chat-only mention, but fed to an in-app provider run —
             the copy button stays as the external-AI fallback (병행, not either/or). -->
        <button
          type="button"
          class="conv-assist-btn"
          :title="t('main.conversation_view.invoke_ai_hint')"
          @click="emit('invoke-ai')"
        >
          <AppIcon name="robot" />
          {{ t('main.conversation_view.invoke_ai') }}
        </button>
      </div>
      <div class="conv-pill">
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
        <button
          type="submit"
          class="conv-send"
          :class="{ 'is-sending': sending }"
          :title="t('main.conversation_view.send')"
          :aria-label="t('main.conversation_view.send')"
          :disabled="sending || draft.trim().length === 0"
        >
          <AppIcon :name="sending ? 'spinner' : 'paper-plane-tilt'" :spin="sending" />
        </button>
      </div>
    </form>
  </div>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import AppIcon from '@shared/AppIcon.vue'

interface ConvTurn {
  speaker: string // 'user' | 'ai' | raw label
  ts: string
  body: string
}

const props = defineProps<{
  docId: string
  projectId?: string | null
}>()

const emit = defineEmits<{
  // TR0044.0010 rev3: ask the parent (MainPanel) to copy an edit-scope mention for
  // this CH doc — manual turn delivery until live chat lands.
  // 0085: the optional payload carries { auto: true } when the copy was triggered
  // automatically by a send (vs. a manual button click), so the parent can stay quiet.
  'copy-mention': [opts?: { auto?: boolean }]
  // Group 0223: run the chat turn through the in-app AI provider instead of the
  // manual copy-paste loop. Explicit click only — never fired by a send.
  'invoke-ai': []
}>()

const { t } = useI18n()
const { showToast } = useToast()

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

// 0085: "auto-copy mention" toggle. When on, a successful send() also fires the same
// copy-mention event the manual button does. Persisted as a single global user
// preference in localStorage (matches the existing UI-toggle persistence pattern), so
// "check once and it stays on" survives reloads and applies to every chat.
const AUTO_COPY_KEY = 'flowgate.chat.autoCopyMention'
function loadAutoCopy(): boolean {
  try {
    return localStorage.getItem(AUTO_COPY_KEY) === '1'
  } catch {
    return false
  }
}
const autoCopy = ref(loadAutoCopy())
watch(autoCopy, (v) => {
  try {
    localStorage.setItem(AUTO_COPY_KEY, v ? '1' : '0')
  } catch {
    /* ignore — best-effort persistence */
  }
})

const turns = ref<ConvTurn[]>([])
const draft = ref(loadDraft(props.docId))
const loading = ref(false)
const sending = ref(false)
const scrollEl = ref<HTMLElement | null>(null)
const inputEl = ref<HTMLTextAreaElement | null>(null)

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

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || sending.value) return
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
    // 0085: auto-copy the mention only AFTER the turn was accepted (turns refreshed
    // above), so the worker reads the latest conversation. Reuses the exact same
    // copy-mention path as the manual button; never fires on a failed send (catch below).
    if (autoCopy.value) emit('copy-mention', { auto: true })
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

onMounted(() => {
  void load()
  void nextTick(autoGrow)
  window.addEventListener('fg:document_content_changed', onContentChanged)
})

onBeforeUnmount(() => {
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

/* Modern chat composer (rev5): a subtle helper row (Copy mention) above a single rounded
   message pill that holds the textarea + a circular send button. */
.conv-composer {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 10px 14px 14px;
  border-top: 1px solid var(--border);
  background: var(--bg-card, #fff);
}

/* Helper row — manual delivery shortcut, kept quiet so it doesn't compete with the input.
   0085: holds the auto-copy checkbox immediately to the LEFT of the copy button. */
.conv-assist {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 10px;
}

.conv-auto {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: .7rem;
  color: var(--text-m);
  cursor: pointer;
  user-select: none;
}

.conv-auto input {
  cursor: pointer;
  margin: 0;
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

.conv-assist-btn:hover {
  background: var(--bg, #f1f5f9);
  color: var(--primary);
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
