<template>
  <div class="conv-view">
    <div ref="scrollEl" class="conv-scroll" @scroll.passive="onScroll">
      <div v-if="loading" class="conv-state">{{ t('common.loading') }}</div>
      <template v-else>
        <!-- Background (D0002 §6): the document intro and, for a conversation that was
             split by the retired carry-over, where it continues from. Drawn once at the
             very top — it is the same on every request, so it never re-renders. -->
        <div v-if="head?.carried_over_from" class="conv-continued">
          <AppIcon name="arrow-up" />
          {{ t('main.conversation_view.continued_from', { doc: head.carried_over_from }) }}
        </div>
        <!-- Earlier turns are fetched on demand (D0002 §6: scrolling up loads the previous
             section and continues from there). The whole conversation is never pulled in one go. -->
        <button
          v-if="hasMoreBefore"
          type="button"
          class="conv-older"
          :disabled="loadingOlder"
          @click="loadOlder()"
        >
          <AppIcon :name="loadingOlder ? 'spinner' : 'arrow-up'" :spin="loadingOlder" />
          {{ loadingOlder ? t('main.conversation_view.loading_older') : t('main.conversation_view.load_older') }}
        </button>

        <p v-if="turns.length === 0" class="conv-state conv-empty">
          <AppIcon name="chats" />
          {{ t('main.conversation_view.empty') }}
        </p>

        <template v-for="turn in turns" :key="turn.localId ?? turn.seq">
          <!-- Read boundary (D0002 §6): drawn immediately above the first turn this user
               has not seen, so re-entering a conversation shows where the new talk starts. -->
          <div v-if="isBoundaryBefore(turn)" class="conv-boundary">
            <span>{{ t('main.conversation_view.read_boundary') }}</span>
          </div>
          <div
            class="conv-row"
            :class="[
              turn.speaker === 'user' ? 'conv-row--user' : 'conv-row--ai',
              { 'is-pending': turn.pending, 'is-failed': turn.failed },
            ]"
            :data-seq="turn.seq || undefined"
          >
            <div class="conv-bubble">
              <div class="conv-meta">
                <span class="conv-speaker">
                  <AppIcon :name="turn.speaker === 'user' ? 'user' : 'robot'" />
                  {{ turn.speaker === 'user' ? t('main.conversation_view.speaker_user') : t('main.conversation_view.speaker_ai') }}
                </span>
                <!-- 0293 R0001 / P0003 §0-2: the model name is a BADGE only. Identity is
                     participant_key, so a renamed model is still the same participant. -->
                <span v-if="turn.display_name" class="conv-provider" :title="turn.display_name">
                  {{ turn.display_name }}
                </span>
                <span v-if="turn.created_at" class="conv-ts">{{ formatTs(turn.created_at) }}</span>
                <span v-if="turn.pending" class="conv-ts">{{ t('main.conversation_view.sending') }}</span>
              </div>
              <div class="conv-body">{{ turn.body }}</div>
              <!-- Crossed talk (P0003 scenario 12): this reply was written without having
                   seen the turn above it. Nothing was overwritten — order preserved it —
                   but the reader needs to know the reply is not answering that turn. -->
              <p v-if="turn.stale_since_seq" class="conv-stale">
                <AppIcon name="warning" />
                {{ t('main.conversation_view.stale_notice', { seq: turn.stale_since_seq }) }}
              </p>
              <!-- A failed optimistic bubble is kept, not dropped: resending reuses the
                   SAME idempotency key, so a turn that did reach the server replays
                   instead of doubling (L0004 §2-17). -->
              <p v-if="turn.failed && !readOnly" class="conv-failed">
                <AppIcon name="warning" />
                {{ t('main.conversation_view.send_pending_failed') }}
                <button type="button" class="conv-retry" @click="retryTurn(turn)">
                  {{ t('main.conversation_view.send_retry') }}
                </button>
              </p>
            </div>
          </div>
        </template>
      </template>
    </div>

    <!-- Participant strip (D0002 §6): who is in this conversation and how far each has
         got. Filled by the same call that loads the turns — there is no separate
         participants endpoint (P0003 scenario 1). -->
    <div v-if="participants.length > 0" class="conv-participants">
      <span class="conv-participants-label">{{ t('main.conversation_view.participants_label') }}</span>
      <span
        v-for="p in participants"
        :key="p.participant_key"
        class="conv-participant"
        :title="t('main.conversation_view.participant_position', { seq: p.last_read_seq })"
      >
        <AppIcon :name="p.kind === 'user' ? 'user' : 'robot'" />
        {{ p.display_name || p.participant_key }}
        <em>#{{ p.last_read_seq }}</em>
      </span>
    </div>

    <!-- Group 0235 (D0005 / P0007 / L0008): the composer's helper row now separates
         TWO independent features: (1) a "send-time action" radio — what happens
         automatically on a successful send (copy mention / call AI / nothing) — and
         (2) the manual [Copy mention] / [Call AI] buttons. Chat AI calls run
         immediately with the header-selected provider (no settings dialog); the
         SEND button carries that run's progress (0235 R0001: no dialog for chat) and,
         since 0264 R0001, carries it as a STOP button rather than a passive spinner —
         the manual [Call AI] button still spins in lockstep. -->
    <form v-if="!readOnly" class="conv-composer" @submit.prevent="send">
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
      <!-- Chat settings panel (D0008 §6-2/§6-3, P0009 §1, L0010 §2-7, group 0362).
           Inline in the composer's flow like .conv-manualcopy below — never a
           full-screen overlay, so the conversation stays visible behind it and a
           close control is always on screen. -->
      <div v-if="showChatSettings" class="conv-settings">
        <div class="conv-settings-hd">
          <span class="conv-settings-title">
            <AppIcon name="gear" />
            {{ t('main.conversation_view.chat_settings_title') }}
          </span>
          <button type="button" class="conv-assist-btn" @click="closeChatSettings">
            <AppIcon name="x" />
            {{ t('common.close') }}
          </button>
        </div>
        <div class="conv-settings-bd">
          <div class="conv-settings-group">
            <span class="conv-settings-group-label">{{ t('main.conversation_view.send_action_label') }}</span>
            <div
              class="conv-settings-radios"
              role="radiogroup"
              :aria-label="t('main.conversation_view.send_action_label')"
            >
              <label class="conv-radio">
                <input type="radio" value="copy_mention" v-model="draftSendAction" />
                {{ t('main.conversation_view.send_action_copy') }}
              </label>
              <label
                class="conv-radio"
                :class="{ 'is-disabled': !invokeSelectable }"
                :title="!invokeSelectable ? t('main.conversation_view.send_action_invoke_disabled_hint') : ''"
              >
                <input type="radio" value="invoke_ai" v-model="draftSendAction" :disabled="!invokeSelectable" />
                {{ t('main.conversation_view.send_action_invoke') }}
              </label>
              <label class="conv-radio">
                <input type="radio" value="none" v-model="draftSendAction" />
                {{ t('main.conversation_view.send_action_none') }}
              </label>
            </div>
          </div>

          <div class="conv-settings-group">
            <span class="conv-settings-group-label">{{ t('main.conversation_view.context_range_label') }}</span>
            <div class="conv-settings-range-row">
              <select v-model="draftRangeChoice" class="form-ctrl conv-settings-select">
                <option
                  v-for="preset in chatSettingsDomain.context_turns_presets"
                  :key="preset"
                  :value="String(preset)"
                >
                  {{ t('main.conversation_view.context_range_turns', { n: preset }) }}
                </option>
                <option value="all">{{ t('main.conversation_view.context_range_all') }}</option>
                <option value="custom">{{ t('main.conversation_view.context_range_custom') }}</option>
              </select>
              <input
                v-if="draftRangeChoice === 'custom'"
                v-model.number="draftContextTurnsCustom"
                type="number"
                class="form-ctrl conv-settings-number"
                :min="chatSettingsDomain.context_turns_min"
                :max="chatSettingsDomain.context_turns_max"
              />
            </div>
            <p class="conv-settings-hint">{{ t('main.conversation_view.context_range_hint') }}</p>
            <p v-if="chatSettingsErrorField === 'context_turns'" class="conv-settings-error">
              {{ chatSettingsErrorMessage }}
            </p>
          </div>

          <p v-if="chatSettingsErrorMessage && chatSettingsErrorField !== 'context_turns'" class="conv-settings-error">
            {{ chatSettingsErrorMessage }}
          </p>
        </div>
        <div class="conv-settings-ft">
          <button type="button" class="conv-assist-btn" @click="closeChatSettings">
            {{ t('common.cancel') }}
          </button>
          <button
            type="button"
            class="conv-assist-btn conv-settings-save"
            :disabled="savingChatSettings"
            @click="saveChatSettings"
          >
            <AppIcon :name="savingChatSettings ? 'spinner' : 'check'" :spin="savingChatSettings" />
            {{ t('common.save') }}
          </button>
        </div>
      </div>
      <div class="conv-assist">
        <!-- Chat settings gear (D0008 §6-1/§6-2, group 0362). Replaces the inline
             [전송 시] radios that used to sit here — those now live in the dialog
             this button opens. "Call AI" inside that dialog is disabled when no
             provider is available (single source of truth: aiProvider store). -->
        <button
          type="button"
          class="conv-gear-btn"
          :class="{ 'is-active': showChatSettings }"
          :title="t('main.conversation_view.chat_settings_title')"
          :aria-label="t('main.conversation_view.chat_settings_title')"
          :aria-expanded="showChatSettings"
          @click="toggleChatSettings"
        >
          <AppIcon name="gear" />
        </button>
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
               (D0005 §3-3); spinner while a run is in flight (duplicate-run prevention). -->
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
import { getRequest, patchRequest, postRequest } from '@shared/api'
import { useToast } from './common/useToast'
import { useAiProviderStore } from '../stores/aiProvider'
import { consumeLastFailedCopyText, copyToClipboard } from '../utils/clipboard'
import AppIcon from '@shared/AppIcon.vue'
import AiProviderSelect from './AiProviderSelect.vue'

// P0003 §0-2. The conversation of record is a list of turns, not a markdown body, so
// this component no longer parses a document — it receives turn objects and appends
// them one at a time.
interface ConvTurn {
  seq: number
  speaker: string // 'user' | 'ai'
  participant_key?: string
  display_name?: string | null
  locale?: string | null
  body: string
  based_on_seq?: number | null
  stale_since_seq?: number | null
  source_run_id?: string | null
  created_at?: string
  // Client-only, for an optimistic bubble that has no server seq yet (L0004 §2-17).
  localId?: string
  idempotencyKey?: string
  pending?: boolean
  failed?: boolean
}

// P0003 §0-4.
interface ConvParticipant {
  participant_key: string
  kind: string
  display_name?: string | null
  first_seen_seq: number
  last_read_seq: number
  last_written_seq: number
  last_seen_at?: string | null
}

interface ConvHead {
  intro?: string
  carried_over_from?: string | null
  total_turns?: number
  head_seq?: number
}

interface TurnsPage {
  turns: ConvTurn[]
  participants?: ConvParticipant[]
  me?: ConvParticipant | null
  head?: ConvHead | null
  head_seq: number
  next_after_seq: number | null
  prev_before_seq: number | null
  has_more: boolean
}

// L0004 §1-5 screen parameters — single source of truth, do not inline these numbers.
const TURN_PAGE_SIZE = 50
const PREPEND_PAGE_SIZE = 30
const CATCHUP_MAX_ROUNDS = 20
const SEND_RETRY_MAX = 2
// 0391 T0005 §7-6: a send failure is an ACTIONABLE rejection — the server's 422 for a
// corrupted body is ~150 characters of instructions. Measured on the real component,
// the default 3s toast life removed it before it could be read. Declared here rather
// than imported from useToast so specs that mock that module partially keep working.
const SEND_FAILED_TOAST_MS = 15000
const VIEWED_DEBOUNCE_MS = 1000
const PREPEND_TRIGGER_PX = 60

const props = defineProps<{
  docId: string
  projectId?: string | null
  // Text of a mention copy that failed to reach the clipboard (B0001 / group 0240). The
  // parent (MainPanel.onConversationCopyMention) hands it over instead of opening the
  // full-screen fallback modal; null/empty hides the inline panel.
  manualCopyText?: string | null
  readOnly?: boolean
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

// ── Chat settings (D0008/P0009/L0010, group 0362) ─────────────────────────────
// [전송 시] used to be a lone browser-local preference (D0005 §3-2 / P0007 §0-1 /
// L0008 §2-1). It is now one of two fields the server stores per user, alongside
// the new [대화 제공 범위] (context_mode / context_turns) that controls where an AI
// call starts reading a long conversation. Both live behind one settings dialog
// opened from the gear button; both round-trip through GET/PATCH /me/chat-settings.
const SEND_ACTION_KEY = 'flowgate.chat.sendAction'
const LEGACY_AUTOCOPY_KEY = 'flowgate.chat.autoCopyMention'
const SEND_ACTIONS = ['copy_mention', 'invoke_ai', 'none'] as const
type SendAction = (typeof SEND_ACTIONS)[number]
const CONTEXT_MODES = ['recent', 'all'] as const
type ContextMode = (typeof CONTEXT_MODES)[number]

interface ChatSettingsValue {
  send_action: SendAction
  context_mode: ContextMode
  context_turns: number
  updated_at: string | null
}
interface ChatSettingsDomain {
  send_action: SendAction[]
  context_mode: ContextMode[]
  context_turns_presets: number[]
  context_turns_min: number
  context_turns_max: number
}
interface ChatSettingsResponse {
  ok: boolean
  settings: ChatSettingsValue
  is_default: boolean
  defaults: { send_action: SendAction; context_mode: ContextMode; context_turns: number }
  domain: ChatSettingsDomain
}

// Reads (and, on first read, migrates) the legacy boolean "auto-copy" key into the
// newer SEND_ACTION_KEY. Kept only for that migration path (L0010 §2-6 item 5) —
// no longer used to seed `sendAction` directly, since the server is now the source
// of truth and the local-storage watcher that used to write SEND_ACTION_KEY on
// every change is gone (L0010 §2-7-1: the dialog's [저장] is the only write path).
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

const sendAction = ref<SendAction>('none')
const contextMode = ref<ContextMode>('recent')
const contextTurns = ref(20)
const chatSettingsIsDefault = ref(true)
const chatSettingsDomain = ref<ChatSettingsDomain>({
  send_action: [...SEND_ACTIONS],
  context_mode: [...CONTEXT_MODES],
  context_turns_presets: [5, 10, 15, 20, 30],
  context_turns_min: 1,
  context_turns_max: 200,
})

function applyChatSettings(data: ChatSettingsResponse): void {
  sendAction.value = data.settings.send_action
  contextMode.value = data.settings.context_mode
  contextTurns.value = data.settings.context_turns
  chatSettingsIsDefault.value = data.is_default
  chatSettingsDomain.value = data.domain
}

// One migration attempt per chat-screen entry (L0010 §2-6, MIGRATION_ATTEMPTS_PER_ENTRY=1).
let sendActionMigrationAttempted = false

async function migrateSendActionOnce(getResult: ChatSettingsResponse | null): Promise<void> {
  if (sendActionMigrationAttempted) return
  if (!getResult) return // a failed GET is not evidence either way — do not decide yet
  if (!getResult.is_default) {
    sendActionMigrationAttempted = true // the server already holds a real choice
    return
  }

  readSendAction() // folds the legacy boolean key into SEND_ACTION_KEY, if present
  let raw: string | null
  try {
    raw = localStorage.getItem(SEND_ACTION_KEY)
  } catch {
    raw = null
  }

  if (raw === null) {
    sendActionMigrationAttempted = true // nothing in this browser to carry over
    return
  }
  if (!(SEND_ACTIONS as readonly string[]).includes(raw) || raw === 'none') {
    // Migrating would not change the outcome — clear the key without calling the
    // server, so a real choice left in another browser can still migrate later.
    try {
      localStorage.removeItem(SEND_ACTION_KEY)
    } catch {
      /* best-effort */
    }
    sendActionMigrationAttempted = true
    return
  }

  sendActionMigrationAttempted = true
  try {
    const res = await patchRequest<ChatSettingsResponse>('/api/v1/me/chat-settings', {
      send_action: raw,
    })
    // Clear the browser key only after the server confirms the write (P0009 scenario 3).
    try {
      localStorage.removeItem(SEND_ACTION_KEY)
    } catch {
      /* best-effort */
    }
    applyChatSettings(res.data)
  } catch {
    // Leave the key in place; the next chat-screen entry re-evaluates from scratch.
  }
}

async function loadChatSettings(): Promise<void> {
  let result: ChatSettingsResponse | null = null
  try {
    const res = await getRequest<ChatSettingsResponse>('/api/v1/me/chat-settings')
    if (disposed) return
    result = res.data
    applyChatSettings(result)
  } catch {
    if (disposed) return
    result = null // D0008 §3-4 / P0009 scenario 10: keep chatting on defaults, do not block
  }
  await migrateSendActionOnce(result)
}

// ── Chat settings dialog (D0008 §6-2, P0009 §1, L0010 §2-7) ──────────────────
const showChatSettings = ref(false)
const savingChatSettings = ref(false)
const chatSettingsErrorField = ref<string | null>(null)
const chatSettingsErrorMessage = ref<string | null>(null)
const draftSendAction = ref<SendAction>('none')
// A preset turn count (as a string), 'all', or 'custom' — draftContextTurnsCustom
// holds the number for the 'custom' case. Both a list pick and typed-in number are
// the same context_turns field on the wire; the split only exists for the UI
// (L0010 §2-7-3).
const draftRangeChoice = ref<string>('20')
const draftContextTurnsCustom = ref(20)

function draftRangeChoiceFor(mode: ContextMode, turns: number): string {
  if (mode === 'all') return 'all'
  if (chatSettingsDomain.value.context_turns_presets.includes(turns)) return String(turns)
  return 'custom'
}

// [scenario 4] opening the dialog never re-queries the server — it redraws the
// last GET/PATCH result, already in hand.
function openChatSettings(): void {
  draftSendAction.value = sendAction.value
  draftRangeChoice.value = draftRangeChoiceFor(contextMode.value, contextTurns.value)
  draftContextTurnsCustom.value = contextTurns.value
  chatSettingsErrorField.value = null
  chatSettingsErrorMessage.value = null
  showChatSettings.value = true
}

function closeChatSettings(): void {
  showChatSettings.value = false
}

function toggleChatSettings(): void {
  if (showChatSettings.value) closeChatSettings()
  else openChatSettings()
}

async function saveChatSettings(): Promise<void> {
  if (savingChatSettings.value) return
  const mode: ContextMode = draftRangeChoice.value === 'all' ? 'all' : 'recent'
  const patch: Record<string, unknown> = { send_action: draftSendAction.value, context_mode: mode }
  // §2-7-4: never send context_turns alongside context_mode: 'all' — the number the
  // user was using must survive an [전체] round trip untouched (server only writes
  // fields present in the request body).
  if (mode === 'recent') {
    const turnsValue =
      draftRangeChoice.value === 'custom' ? draftContextTurnsCustom.value : Number(draftRangeChoice.value)
    if (!Number.isInteger(turnsValue)) {
      chatSettingsErrorField.value = 'context_turns'
      chatSettingsErrorMessage.value = t('main.conversation_view.context_range_invalid')
      return
    }
    patch.context_turns = turnsValue
  }

  savingChatSettings.value = true
  chatSettingsErrorField.value = null
  chatSettingsErrorMessage.value = null
  try {
    const res = await patchRequest<ChatSettingsResponse>('/api/v1/me/chat-settings', patch)
    applyChatSettings(res.data)
    showChatSettings.value = false
  } catch (e: any) {
    const data = e?.response?.data
    chatSettingsErrorField.value = data?.error?.field ?? null
    chatSettingsErrorMessage.value = data?.error?.message ?? t('main.conversation_view.chat_settings_save_failed')
  } finally {
    savingChatSettings.value = false
  }
}

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

// Fallback (L0008 §4-3, kept screen-only by L0010 §2-7-2): once the provider list
// has resolved to EMPTY, a stale "invoke_ai" selection reverts to "none" on screen.
// Never fires while still resolving, so a transient empty list during load can't
// wipe a valid selection. This reset is NOT written back to the server — opening a
// project with no providers once must not silently erase a user's [AI 호출] choice
// for every other project.
// `sendAction` is also watched: the chat-settings GET/PATCH (including the L0010
// §2-6 migration) resolves independently of the provider list, so a value that
// arrives AFTER providers have already settled to empty must still be caught —
// watching only [providersResolving, hasProviders] would miss that ordering.
watch([providersResolving, hasProviders, sendAction], () => {
  if (!providersResolving.value && !hasProviders.value && sendAction.value === 'invoke_ai') {
    sendAction.value = 'none'
  }
})

const turns = ref<ConvTurn[]>([])
const participants = ref<ConvParticipant[]>([])
const head = ref<ConvHead | null>(null)
// The highest seq the server has confirmed. This is the recovery datum: after a dropped
// stream we ask for everything after it (P0003 scenario 7), so it must come from the
// server's post-commit MAX(seq), never from the seq we happened to be assigned.
const headSeq = ref(0)
// This user's read boundary, as the server knows it.
const readSeq = ref(0)
const hasMoreBefore = ref(false)
const loadingOlder = ref(false)
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

// ── Turn list maintenance ────────────────────────────────────────────────────
// Reconciliation keys on `seq` alone. `idempotency_key` is deliberately absent from the
// response turn object (P0003 §0-2), so an optimistic bubble is matched by its local id
// and everything else by the sequence number the server assigned.
function turnsUrl(): string {
  return `/api/v1/documents/${encodeURIComponent(props.docId)}/conversation/turns`
}

function hasSeq(seq: number): boolean {
  return seq > 0 && turns.value.some((turn) => turn.seq === seq)
}

/** Insert in seq order, ignoring a turn already on screen. Optimistic bubbles (seq 0)
 *  always sort last — they are, by definition, the newest thing this client knows. */
function insertInSeqOrder(turn: ConvTurn): void {
  if (hasSeq(turn.seq)) return
  const list = turns.value
  let at = list.length
  for (let i = 0; i < list.length; i++) {
    const other = list[i]
    if (!other.seq || other.seq > turn.seq) { at = i; break }
  }
  list.splice(at, 0, turn)
}

function applyPage(page: TurnsPage): void {
  for (const turn of page.turns ?? []) insertInSeqOrder(turn)
  headSeq.value = Math.max(headSeq.value, Number(page.head_seq) || 0)
  if (page.participants && page.participants.length > 0) participants.value = page.participants
  if (page.me) readSeq.value = Math.max(readSeq.value, Number(page.me.last_read_seq) || 0)
  if (page.head) head.value = page.head
}

function oldestSeq(): number {
  const confirmed = turns.value.filter((turn) => turn.seq > 0)
  return confirmed.length > 0 ? confirmed[0].seq : headSeq.value + 1
}

/** Draw the boundary immediately above the first turn the user has not read. Suppressed
 *  when everything on screen is new (nothing has been read yet) — a line above the very
 *  first bubble would say nothing. */
function isBoundaryBefore(turn: ConvTurn): boolean {
  if (readSeq.value <= 0 || !turn.seq || turn.seq <= readSeq.value) return false
  const earlier = turns.value.filter((other) => other.seq > 0 && other.seq <= readSeq.value)
  if (earlier.length === 0) return false
  const firstUnread = turns.value.find((other) => other.seq > readSeq.value)
  return firstUnread?.seq === turn.seq
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
/** True when the reader is already following the tail, so a new turn may pull the view
 *  down. Anything further up is a deliberate scroll into history — leave it alone. */
function isPinnedToBottom(): boolean {
  const el = scrollEl.value
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < 80
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

async function fetchPage(params: Record<string, unknown>): Promise<TurnsPage | null> {
  try {
    const res = await getRequest<TurnsPage>(turnsUrl(), params)
    return (res.data as TurnsPage) ?? null
  } catch {
    // A conversation that cannot be read right now is an empty screen, not a crash;
    // the composer stays usable and SSE//turns will fill it in on the next attempt.
    return null
  }
}

/** Full (re)entry: background + everything this user has not read, then enough earlier
 *  context to make the screen readable. P0003 scenario 1. */
async function load(): Promise<ConvTurn[]> {
  if (!props.docId) return []
  loading.value = true
  try {
    turns.value = []
    participants.value = []
    head.value = null
    headSeq.value = 0
    readSeq.value = 0
    hasMoreBefore.value = false

    // No cursor: the server resumes from the position it remembers for this user, so
    // the client never computes where it left off (D0002 §3-4).
    const first = await fetchPage({ include_head: 1, limit: TURN_PAGE_SIZE })
    if (first) {
      applyPage(first)
      let cursor = first.next_after_seq
      for (let round = 0; cursor != null && round < CATCHUP_MAX_ROUNDS; round++) {
        const next = await fetchPage({ after_seq: cursor, limit: TURN_PAGE_SIZE })
        if (!next) break
        applyPage(next)
        cursor = next.next_after_seq
      }
      // Everything already read still lives above the boundary; pull one page of it so
      // re-entering a conversation shows context rather than a bare "여기까지 읽음" line.
      hasMoreBefore.value = oldestSeq() > 1
      if (hasMoreBefore.value && turns.value.length < PREPEND_PAGE_SIZE) {
        await loadOlder({ keepScroll: false })
      }
    }
    scrollToBottom()
    scheduleViewedReport()
    return turns.value
  } finally {
    loading.value = false
  }
}

/** Scroll-up paging (P0003 scenario 2). The prepended block's height is added back to
 *  scrollTop so the conversation does not jump under the reader's eyes. */
async function loadOlder(opts: { keepScroll?: boolean } = {}): Promise<void> {
  if (loadingOlder.value) return
  const before = oldestSeq()
  if (before <= 1) { hasMoreBefore.value = false; return }
  loadingOlder.value = true
  const el = scrollEl.value
  const heightBefore = el?.scrollHeight ?? 0
  const topBefore = el?.scrollTop ?? 0
  try {
    const page = await fetchPage({ before_seq: before, limit: PREPEND_PAGE_SIZE })
    if (!page) return
    applyPage(page)
    hasMoreBefore.value = page.prev_before_seq != null || oldestSeq() > 1
    if (opts.keepScroll !== false) {
      await nextTick()
      const target = scrollEl.value
      if (target) target.scrollTop = topBefore + (target.scrollHeight - heightBefore)
    }
  } finally {
    loadingOlder.value = false
  }
}

// ── Read reporting (L0004 §1-3 / §2-8) ───────────────────────────────────────
// Debounced, foreground-only, and only for a bubble whose BOTTOM is actually inside the
// viewport. The debounce exists to stop dozens of notices per second of scrolling, not
// to guess at attention.
let viewedTimer: ReturnType<typeof setTimeout> | null = null
let reportedViewed = 0

function highestVisibleSeq(): number {
  const el = scrollEl.value
  if (!el) return 0
  const limit = el.scrollTop + el.clientHeight
  let best = 0
  for (const node of Array.from(el.querySelectorAll<HTMLElement>('[data-seq]'))) {
    const seq = Number(node.dataset.seq ?? 0)
    if (!seq) continue
    if (node.offsetTop + node.offsetHeight <= limit + 1) best = Math.max(best, seq)
  }
  return best
}

function scheduleViewedReport(): void {
  if (viewedTimer !== null) clearTimeout(viewedTimer)
  viewedTimer = setTimeout(() => {
    viewedTimer = null
    void reportViewed()
  }, VIEWED_DEBOUNCE_MS)
}

async function reportViewed(): Promise<void> {
  if (disposed || !props.docId) return
  if (typeof document !== 'undefined' && document.hidden) return
  const seq = highestVisibleSeq()
  if (seq <= 0 || seq <= reportedViewed || seq <= readSeq.value) return
  reportedViewed = seq
  try {
    const res = await postRequest<{ me?: ConvParticipant }>(
      `/api/v1/documents/${encodeURIComponent(props.docId)}/conversation/read`,
      { last_read_seq: seq, reason: 'viewed' },
    )
    const me = (res.data as any)?.me
    // The boundary only ever moves forward, and the server's value wins — a cursor is
    // monotonic there, so a racing notice cannot pull the line back.
    if (me) readSeq.value = Math.max(readSeq.value, Number(me.last_read_seq) || 0)
  } catch {
    // Losing a read notice costs a boundary line, not a message. Retry on next scroll.
    reportedViewed = 0
  }
}

function onScroll(): void {
  const el = scrollEl.value
  if (el && el.scrollTop <= PREPEND_TRIGGER_PX && hasMoreBefore.value && !loadingOlder.value) {
    void loadOlder()
  }
  scheduleViewedReport()
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
  if (invoking.value) return // duplicate-run prevention (client guard)
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
// outright, and the spinner state lives only here (0251 B0001 / NR0003 §5, option B). Without
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
        // The reply has usually already arrived over SSE; catchUp() only closes a gap,
        // so verifying the run no longer costs a full reload of the conversation.
        await catchUp()
        const aiTurns = turns.value.filter((turn) => turn.speaker === 'ai').length
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

function newIdempotencyKey(): string {
  const uuid =
    typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `${Date.now().toString(16)}-${Math.random().toString(16).slice(2)}-${Math.random().toString(16).slice(2)}`
  return `sess_${uuid}`
}

/** POST one turn, replacing its optimistic bubble on success.
 *
 *  The key is generated ONCE per message and reused by every retry, so a turn the
 *  server already stored comes back as a replay (200) instead of being written twice
 *  (P0003 scenario 4). 404/400/409/422 are decisions, not blips — retrying them only
 *  repeats the same answer.
 */
async function postTurn(temp: ConvTurn): Promise<boolean> {
  for (let attempt = 0; attempt <= SEND_RETRY_MAX; attempt++) {
    try {
      const res = await postRequest<{ turn: ConvTurn; head_seq: number; me?: ConvParticipant }>(
        `/api/v1/documents/${encodeURIComponent(props.docId)}/conversation/turn`,
        {
          body: temp.body,
          idempotency_key: temp.idempotencyKey,
          based_on_seq: temp.based_on_seq ?? 0,
        },
      )
      const data = res.data as any
      const saved: ConvTurn | undefined = data?.turn
      const at = turns.value.findIndex((turn) => turn.localId === temp.localId)
      if (saved && !hasSeq(saved.seq)) {
        // Swap the placeholder for the numbered turn, in place, so the bubble does not
        // visibly jump. Only this one turn arrives — never the whole body (P0003 scenario 3).
        if (at >= 0) turns.value.splice(at, 1, saved)
        else insertInSeqOrder(saved)
      } else if (at >= 0) {
        // Already on screen (SSE beat the response, or this was a replay) — drop the
        // duplicate placeholder rather than showing the message twice.
        turns.value.splice(at, 1)
      }
      headSeq.value = Math.max(headSeq.value, Number(data?.head_seq) || 0)
      if (data?.me) readSeq.value = Math.max(readSeq.value, Number(data.me.last_read_seq) || 0)
      return true
    } catch (e: any) {
      const status = e?.response?.status
      if ([400, 404, 409, 422].includes(status) || attempt === SEND_RETRY_MAX) {
        const detail = describeErrorDetail(e?.response?.data?.detail ?? e?.response?.data ?? e)
        // 0391 T0005 §7-6: the server's 422 for a corrupted body is ~150 characters of
        // instructions (write a UTF-8 file first, or fill in force_encoding_reason).
        // Measured on the real component: at the default 3s the sentence was gone
        // before it could be read. Actionable rejections get the long life instead.
        showToast(t('main.conversation_view.send_failed', { detail }), 'danger', SEND_FAILED_TOAST_MS)
        return false
      }
    }
  }
  return false
}

async function send(): Promise<void> {
  const text = draft.value.trim()
  // Block a new send while the send button is busy — either already posting a turn
  // OR spinning through a chat AI call (R0001: one turn at a time, progress on send).
  if (!text || busy.value) return
  const temp: ConvTurn = {
    seq: 0,
    speaker: 'user',
    body: text,
    // What this message was written in response to. If someone speaks while it is in
    // flight the server keeps the order and marks the crossing — it does not reject.
    based_on_seq: headSeq.value,
    stale_since_seq: null,
    created_at: new Date().toISOString(),
    localId: newIdempotencyKey(),
    idempotencyKey: '',
    pending: true,
  }
  temp.idempotencyKey = temp.localId
  turns.value.push(temp)
  scrollToBottom()

  sending.value = true
  try {
    const ok = await postTurn(temp)
    if (!ok) {
      // The draft is NOT cleared on failure (P0003 scenario 17) and the bubble is kept
      // with a retry that reuses the same key — either route resends without doubling.
      const at = turns.value.findIndex((turn) => turn.localId === temp.localId)
      if (at >= 0) turns.value.splice(at, 1, { ...temp, pending: false, failed: true })
      return
    }
    draft.value = ''
    persistDraft(props.docId, '')
    resetInputHeight()
    scrollToBottom()
    // D0005 §3-2: dispatch the send-time action only AFTER the turn was accepted.
    // Never fires on a failed send.
    const action = sendAction.value
    if (action === 'copy_mention') {
      emit('copy-mention', { auto: true })
    } else if (action === 'invoke_ai') {
      void invokeAi('auto')
    }
    void nextTick(() => inputEl.value?.focus())
  } finally {
    sending.value = false
  }
}

async function retryTurn(turn: ConvTurn): Promise<void> {
  if (!turn.localId || sending.value) return
  const at = turns.value.findIndex((item) => item.localId === turn.localId)
  if (at >= 0) turns.value.splice(at, 1, { ...turn, pending: true, failed: false })
  sending.value = true
  try {
    const ok = await postTurn(turn)
    if (!ok) {
      const back = turns.value.findIndex((item) => item.localId === turn.localId)
      if (back >= 0) turns.value.splice(back, 1, { ...turn, pending: false, failed: true })
    }
  } finally {
    sending.value = false
  }
}

// ── Live delivery (P0003 scenario 6) ─────────────────────────────────────────
// The server pushes the appended TURN, so this appends one bubble instead of
// re-fetching the document. A turn already on screen — typically our own, echoed back
// before the POST response landed — is ignored by seq.
function onSseTurn(e: Event) {
  const detail = (e as CustomEvent).detail as {
    doc_id?: string
    head_seq?: number
    turn?: ConvTurn
    participant?: ConvParticipant
  } | undefined
  if (!detail?.turn || detail.doc_id !== props.docId) return
  const atBottom = isPinnedToBottom()
  if (!hasSeq(detail.turn.seq)) insertInSeqOrder(detail.turn)
  headSeq.value = Math.max(headSeq.value, Number(detail.head_seq) || detail.turn.seq || 0)
  if (detail.participant) {
    const at = participants.value.findIndex(
      (p) => p.participant_key === detail.participant!.participant_key,
    )
    if (at >= 0) participants.value.splice(at, 1, detail.participant)
    else participants.value.push(detail.participant)
  }
  // Only follow the conversation when the reader was already at the bottom; yanking a
  // reader who scrolled up into history is worse than a missed scroll.
  if (atBottom) scrollToBottom()
  scheduleViewedReport()
}

/** Fill the gap left by a dropped stream (P0003 scenario 7). Live delivery is an
 *  optimisation, not the record — the conversation must be correct with no SSE at all. */
async function catchUp(): Promise<void> {
  if (disposed || !props.docId) return
  let cursor: number | null = headSeq.value
  for (let round = 0; cursor != null && round < CATCHUP_MAX_ROUNDS; round++) {
    const page = await fetchPage({ after_seq: cursor, limit: TURN_PAGE_SIZE })
    if (!page) return
    applyPage(page)
    cursor = page.next_after_seq
  }
  scheduleViewedReport()
}

function onSseReconnected() {
  void catchUp()
}

// A CH document changed out-of-band by something other than a turn append (a rename,
// a workflow decision). The turn stream owns the conversation body, so this only
// re-reads the head — it must not re-pull the whole conversation on every message.
function onContentChanged(e: Event) {
  const detail = (e as CustomEvent).detail as { doc_id?: string | null } | undefined
  if (detail?.doc_id !== props.docId) return
  void catchUp()
}

watch(() => props.docId, (docId) => {
  draft.value = loadDraft(docId)
  reportedViewed = 0
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
  // Chat settings are per-user, not per-document, so this loads once per mount —
  // not on every props.docId change (group 0362).
  void loadChatSettings()
  window.addEventListener('fg:conversation_turn', onSseTurn)
  window.addEventListener('fg:sse_reconnected', onSseReconnected)
  window.addEventListener('fg:document_content_changed', onContentChanged)
})

onBeforeUnmount(() => {
  disposed = true
  if (viewedTimer !== null) {
    clearTimeout(viewedTimer)
    viewedTimer = null
  }
  window.removeEventListener('fg:conversation_turn', onSseTurn)
  window.removeEventListener('fg:sse_reconnected', onSseReconnected)
  window.removeEventListener('fg:document_content_changed', onContentChanged)
})

// 0351 T4 (P0003 scenario 16): a conversation-turn search result names a seq that may
// sit further back than what is currently loaded. Page older history in (reusing the
// same loadOlder the scroll-up trigger uses) until that turn is present, then scroll
// it into view. MainPanel calls this via the same ref map it already keeps for
// scrollToBottom, in response to the fg:conversation_jump_seq window event.
async function jumpToSeq(seq: number): Promise<void> {
  if (!seq || seq <= 0) return
  if (turns.value.length === 0 && !loading.value) await load()
  let rounds = 0
  while (!hasSeq(seq) && hasMoreBefore.value && rounds < CATCHUP_MAX_ROUNDS) {
    await loadOlder({ keepScroll: false })
    rounds += 1
  }
  await nextTick()
  const target = scrollEl.value?.querySelector<HTMLElement>(`[data-seq="${seq}"]`)
  if (!target) return
  target.scrollIntoView({ block: 'center' })
  target.classList.add('conv-turn--jump-highlight')
  setTimeout(() => target.classList.remove('conv-turn--jump-highlight'), 2000)
}

// scrollToBottom is exposed for the CH full view (0263 R0001): teleporting this component
// between the card and the dialog detaches and re-attaches .conv-scroll, and a re-attached
// element comes back at scrollTop 0 — the log stuck at the TOP, the same symptom rev8 fixed
// for new turns. The mover re-pins once the node lands.
defineExpose({ load, scrollToBottom, jumpToSeq })
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

/* 0351 T4 — brief flash on the turn a search result jumped to, so the reader's eye
   lands on the right bubble instead of just the scroll position. */
.conv-row.conv-turn--jump-highlight .conv-bubble {
  animation: conv-turn-jump-flash 2s ease-out;
}

@keyframes conv-turn-jump-flash {
  0% {
    box-shadow: 0 0 0 2px var(--accent, #2563eb);
  }
  100% {
    box-shadow: 0 0 0 0 transparent;
  }
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

/* An optimistic bubble is dimmed until the server gives it a number, so "sent" and
   "recorded" never look the same. */
.conv-row.is-pending .conv-bubble {
  opacity: 0.62;
}

.conv-row.is-failed .conv-bubble {
  border-color: var(--danger, #dc2626);
}

.conv-stale,
.conv-failed {
  display: flex;
  align-items: center;
  gap: 5px;
  margin: 5px 0 0;
  font-size: .68rem;
  line-height: 1.45;
}

.conv-stale {
  color: var(--warning, #b45309);
}

.conv-failed {
  color: var(--danger, #dc2626);
}

.conv-retry {
  padding: 1px 7px;
  font-size: .66rem;
  font-family: inherit;
  color: var(--danger, #dc2626);
  background: transparent;
  border: 1px solid currentColor;
  border-radius: 999px;
  cursor: pointer;
}

/* "Read up to here" (D0002 §6) — a rule across the log, not a bubble, because it marks
   a position between turns rather than a message. */
.conv-boundary {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 2px 0;
  font-size: .66rem;
  font-weight: 700;
  color: var(--primary, #2563eb);
  opacity: 0.85;
}

.conv-boundary::before,
.conv-boundary::after {
  content: '';
  flex: 1;
  height: 1px;
  background: currentColor;
  opacity: 0.4;
}

.conv-continued {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  font-size: .68rem;
  color: var(--text-m);
  background: var(--bg-subtle, #f1f5f9);
  border-radius: 6px;
}

.conv-older {
  align-self: center;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 12px;
  font-size: .7rem;
  font-family: inherit;
  color: var(--text-m);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 999px;
  cursor: pointer;
}

.conv-older:hover:not(:disabled) {
  color: var(--primary);
  border-color: var(--primary);
}

.conv-older:disabled {
  opacity: 0.6;
  cursor: default;
}

/* Participant strip — who is here and how far each has read (D0002 §6). */
.conv-participants {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  padding: 5px 14px;
  border-top: 1px solid var(--border);
  font-size: .68rem;
  color: var(--text-m);
}

.conv-participants-label {
  font-weight: 700;
  opacity: 0.85;
}

.conv-participant {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 1px 8px;
  background: var(--bg-subtle, #f1f5f9);
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 999px;
  max-width: 16rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.conv-participant em {
  font-style: normal;
  opacity: 0.7;
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

/* Chat settings gear (D0008 §6-1/§6-2, group 0362) — replaces the old inline
   send-time-action radio group; the radios now live inside .conv-settings below.
   Deliberately its own class rather than .conv-assist-btn: it toggles a dialog
   rather than firing an action, and several tests count `.conv-assist-btn`
   elements to assert which manual buttons (copy/invoke) are present. */
.conv-gear-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 3px 8px;
  font-size: .8rem;
  color: var(--text-m);
  background: transparent;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: background .12s, color .12s;
}

.conv-gear-btn:hover {
  background: var(--bg, #f1f5f9);
  color: var(--primary);
}

.conv-gear-btn.is-active {
  background: var(--bg, #f1f5f9);
  color: var(--primary);
}

/* Chat settings panel (D0008 §6-2/§6-3, P0009 §1, L0010 §2-7, group 0362) — inline
   in the composer's flow like .conv-manualcopy above: no position:fixed, no
   inset:0, no backdrop. The conversation stays visible above it and Cancel/[x]
   are always on screen (D0008 §6-3). */
.conv-settings {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--bg-card, #fff);
}

.conv-settings-hd {
  display: flex;
  align-items: center;
  gap: 6px;
}

.conv-settings-title {
  flex: 1;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: .75rem;
  font-weight: 700;
  color: var(--text);
}

.conv-settings-bd {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.conv-settings-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.conv-settings-group-label {
  font-size: .7rem;
  font-weight: 700;
  color: var(--text-m);
  opacity: 0.85;
}

.conv-settings-radios {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  font-size: .7rem;
  color: var(--text-m);
}

.conv-settings-range-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.conv-settings-select {
  max-width: 220px;
}

.conv-settings-number {
  max-width: 100px;
}

.conv-settings-hint {
  margin: 0;
  font-size: .68rem;
  line-height: 1.5;
  color: var(--text-m);
}

.conv-settings-error {
  margin: 0;
  font-size: .7rem;
  color: var(--danger);
}

.conv-settings-ft {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}

.conv-settings-save {
  color: var(--primary);
  font-weight: 600;
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