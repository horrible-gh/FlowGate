<template>
  <section class="question-policy" data-test="question-policy">
    <div v-if="loading" class="qp-loading">{{ t('loading') }}</div>
    <template v-else>
      <!-- v02test.0096 F96-7 (mockup ⑤): one line on top — the name, 미리보기|편집, a gap, the save state and length,
           [저장] (only while editing) and [AI와 정하기]. -->
      <header class="qp-head">
        <label id="question-policy-label" for="question-policy-body" class="qp-title">{{ t('title') }}</label>
        <div class="qp-mode" role="group" :aria-label="t('view')">
          <button type="button" class="qp-mode-btn" :class="{ 'is-active': mode === 'preview' }" :aria-pressed="mode === 'preview'"
            data-test="qp-mode-preview" @click="mode = 'preview'">{{ t('preview') }}</button>
          <button type="button" class="qp-mode-btn" :class="{ 'is-active': mode === 'edit' }" :aria-pressed="mode === 'edit'"
            data-test="qp-mode-edit" @click="edit">{{ t('edit') }}</button>
        </div>
        <span class="qp-status" :class="{ 'is-dirty': dirty, 'is-over': length > 4000 }" data-test="qp-status" aria-live="polite">
          {{ dirty ? '●' : '✓' }} {{ t(dirty ? 'unsaved' : 'saved') }} · {{ length }} / 4000</span>
        <Button v-if="mode === 'edit'" variant="primary" size="sm" data-test="qp-save" :disabled="!loaded || saving || busy || !dirty || length > 4000" @click="save">{{ t('save') }}</Button>
        <Button variant="secondary" size="sm" data-test="qp-assist" :disabled="!loaded" @click="open = true">{{ t('assist') }}</Button>
      </header>
      <textarea v-if="mode === 'edit'" id="question-policy-body" ref="bodyEl" v-model="body" :disabled="!loaded || saving" :placeholder="t('example')" />
      <div v-else class="qp-preview" data-test="qp-preview" aria-labelledby="question-policy-label" @dblclick="edit">
        <div v-if="body.trim()" class="qp-md qp-doc" v-html="bodyHtml" />
        <p v-else class="qp-preview-empty">{{ t('emptyPreview') }}</p>
      </div>
      <p class="qp-foot"><template v-for="(part, index) in deliveredParts" :key="index"><code v-if="index % 2">{{ part }}</code><template v-else>{{ part }}</template></template></p>
    </template>
    <p v-if="error" class="qp-alert" role="alert">{{ error }} <Button v-if="!loaded" size="sm" @click="load">{{ t('retry') }}</Button></p>
    <ModelessShell kind-color="#a78bfa" :open="open" :title="t('assist')" :initial-x="80" :initial-y="100" :initial-width="510" :initial-height="570"
      :min-width="320" :min-height="340" @close="open = false">
      <!-- 0021 (0004-CH): the conversation looks and moves like the chat panel — the chat's own message bubbles
           (TimelineMessage), choices only as buttons under the current question, the draft once in its own box with
           [맞음] under it, the new question scrolled into view and the answer box focused. -->
      <section class="policy-dialog" role="dialog" aria-modal="false" :aria-label="t('assist')">
        <div class="qp-top">
          <select v-model="provider" class="ai-base-select" :aria-label="t('choose')" :disabled="busy">
            <option value="" disabled>{{ t('choose') }}</option>
            <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
          </select>
          <button v-if="entries.length && !busy" type="button" class="qp-link" data-test="qp-restart" @click="restart">{{ t('restart') }}</button>
        </div>
        <p v-if="!providers.length" class="qp-state">{{ t('empty') }}</p>
        <div ref="logEl" class="qp-log" data-test="qp-log" aria-live="polite">
          <div v-if="!entries.length && body" class="qp-box" data-test="qp-current">
            <div class="qp-box-title">{{ t('current') }}</div>
            <div class="qp-box-body qp-plain">{{ body }}</div>
          </div>
          <TimelineMessage v-for="(entry, index) in entries" :key="index" :turn="turnOf(entry)" :pending="false" :failed="false" read-only />
          <div v-if="busy" class="qp-thinking" data-test="qp-thinking"><span class="qp-dots" aria-hidden="true"><i /><i /><i /></span>{{ t('thinking') }}</div>
          <template v-if="reply && !busy && !applied">
            <div v-if="reply.choices.length" class="qp-choices" data-test="qp-choices">
              <button v-for="(choice, index) in reply.choices" :key="index" type="button" class="qp-choice"
                :class="{ 'is-recommended': choice.recommended }" @click="send(choice.label)">
                <span>{{ choice.label }}</span><span v-if="choice.recommended" class="qp-badge">{{ t('recommended') }}</span>
              </button>
            </div>
            <div v-if="reply.policy" class="qp-box qp-draft" data-test="qp-draft">
              <div class="qp-box-title">{{ t('understood') }}</div>
              <div class="qp-box-body qp-md" v-html="draftHtml" />
              <div class="qp-draft-actions"><Button @click="confirm">{{ t('confirm') }}</Button><span>{{ t('orChange') }}</span></div>
            </div>
          </template>
          <p v-if="applied" class="qp-note">{{ t('applied') }}</p>
          <p v-if="assistantError" class="qp-error" role="alert">{{ assistantError }}
            <button v-if="retryable" type="button" class="qp-link" :disabled="busy || !provider" @click="send()">{{ t('retry') }}</button></p>
        </div>
        <Button v-if="!entries.length" :disabled="!provider || busy" @click="send()">{{ t('begin') }}</Button>
        <form v-else class="qp-pill" @submit.prevent="send(input)">
          <textarea ref="inputEl" v-model="input" class="qp-input" rows="1" :aria-label="t('answer')" :placeholder="t('answer')"
            :disabled="busy" maxlength="4000" @input="grow" @keydown.enter.exact="onEnter" />
          <button type="submit" class="qp-send" :title="t('send')" :aria-label="t('send')" :disabled="busy || !provider || !input.trim()">
            <span aria-hidden="true">➤</span></button>
        </form>
        <Button v-if="applied" :disabled="saving || [...body].length > 4000" @click="save">{{ t('save') }}</Button>
      </section>
    </ModelessShell>
  </section>
</template>
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { normalizeApiError } from '@shared/apiErrors'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { createMarkdownRenderer } from '@/core/markdown/markdownRenderer'
import TimelineMessage from '@/features/conversation/TimelineMessage.vue'
import type { ConversationTurn } from '@/features/conversation/types'
import { installPolicyMessages } from './messages'
import '../aiBaseSelect.css'
import { loadPolicy, savePolicy, loadProviders, startAssistant, assistantResult, type Reply } from './api'
const composer = useI18n({ useScope: 'global' }); installPolicyMessages(composer)
const t = (key: string) => composer.t('gui.questionPolicy.' + key)
/** One line of the conversation: `text` is shown, `wire` goes back to the AI (its choices and draft included). */
interface Entry { role: 'user' | 'assistant'; text: string; wire: string; name: string | null }
const ERROR_KEYS: Record<string, string> = {
  timeout: 'errorTimeout', provider_launch_failed: 'errorLaunch', execution_failed: 'errorLaunch', transport_failed: 'errorLaunch',
  remote_execution_failed: 'errorLaunch', provider_unavailable: 'errorUnavailable', remote_agent_offline: 'errorUnavailable',
  remote_agent_unavailable: 'errorUnavailable', remote_agent_workdir_unset: 'errorUnavailable', remote_agent_workdir_unverified: 'errorUnavailable',
  reply_invalid: 'errorReply', policy_too_long: 'errorReply', policy_assistant_busy: 'errorBusy', policy_conversation_too_long: 'errorTooLong',
}
/**
 * 0026 (0004-CH): failures a button cannot fix — the AI is off or misconfigured, or the conversation is too long —
 * say why and show no [다시 시도]. Everything else the panel and the server first retry on their own.
 */
const NOT_RETRYABLE = new Set(['provider_unavailable', 'remote_agent_offline', 'remote_agent_unavailable', 'remote_agent_workdir_unset',
  'remote_agent_workdir_unverified', 'provider_launch_failed', 'policy_conversation_too_long'])
/** Quiet re-tries of one start / result call that did not get through (network, 5xx, 429, busy) before it is shown. */
const QUIET_LIMIT = 20
/** A request the server no longer knows (404: it restarted) is sent again with the same conversation this often. */
const RESEND_LIMIT = 2
const wait = (ms: number) => new Promise(resolve => setTimeout(resolve, ms))
const backoff = (misses: number) => Math.min(1000 * 2 ** (misses - 1), 5000)
function transient(e: ReturnType<typeof normalizeApiError>) {
  return e.network || e.status === null || e.status >= 500 || e.status === 429 || e.status === 408 || e.code === 'policy_assistant_busy'
}
const body = ref(''), saved = ref(''), provider = ref(''), input = ref(''), error = ref(''), assistantError = ref('')
const failureCode = ref<string | null>(null)
const retryable = computed(() => !NOT_RETRYABLE.has(failureCode.value ?? ''))
const providers = ref<{ id: string; name: string }[]>([]), entries = ref<Entry[]>([]), reply = ref<Reply | null>(null)
const loading = ref(true), loaded = ref(false), saving = ref(false), open = ref(false), busy = ref(false), applied = ref(false)
const logEl = ref<HTMLElement | null>(null), inputEl = ref<HTMLTextAreaElement | null>(null), bodyEl = ref<HTMLTextAreaElement | null>(null)
const dirty = computed(() => body.value !== saved.value)
const length = computed(() => [...body.value].length)
/** v02test.0096: a saved policy opens as its rendered preview; an empty one opens in the editor. */
const mode = ref<'preview' | 'edit'>('edit')
const renderMarkdown = createMarkdownRenderer({ copyLabel: () => composer.t('gui.document_window.viewer.copy_code'), safe: true })
const draftHtml = computed(() => (reply.value?.policy ? renderMarkdown(reply.value.policy) : ''))
const bodyHtml = computed(() => (body.value.trim() ? renderMarkdown(body.value) : ''))
/** The foot line with the name the AI receives (`[사용자 질의 방침]`) set apart as code: odd parts are the bracketed name. */
const deliveredParts = computed(() => t('delivered').split(/(\[[^\]]+\])/))
async function edit() {
  mode.value = 'edit'
  await nextTick(); bodyEl.value?.focus({ preventScroll: true })
}
let disposed = false
let pendingId: string | null = null
onBeforeUnmount(() => { disposed = true })
const turnOf = (entry: Entry): ConversationTurn => ({ seq: 0, speaker: entry.role === 'user' ? 'user' : 'ai', displayName: entry.name, body: entry.text, createdAt: null })
const errorText = (code: string | null | undefined) => t(ERROR_KEYS[code ?? ''] ?? 'error')
function wireOf(r: Reply) {
  return [r.message, r.choices.length ? '(choices: ' + r.choices.map(choice => choice.label).join(' / ') + ')' : '',
    r.policy ? '(policy draft)\n' + r.policy : ''].filter(Boolean).join('\n')
}
/** The new question's first line at the top of the log (not the bottom), or the log's end while waiting. */
async function reveal(target: 'question' | 'end', focus = false) {
  await nextTick()
  const log = logEl.value
  if (log) {
    const questions = log.querySelectorAll<HTMLElement>('[data-speaker="ai"]')
    const last = target === 'question' ? questions[questions.length - 1] : undefined
    if (last) log.scrollTop += last.getBoundingClientRect().top - log.getBoundingClientRect().top - 8
    else log.scrollTop = log.scrollHeight
  }
  if (focus) inputEl.value?.focus({ preventScroll: true })
}
/** Enter sends, Shift+Enter breaks the line; an Enter that only ends a Korean/Japanese IME composition does not send. */
function onEnter(event: KeyboardEvent) {
  if (event.isComposing || event.keyCode === 229) return
  event.preventDefault(); void send(input.value)
}
function grow() {
  const el = inputEl.value
  if (!el) return
  el.style.height = 'auto'; el.style.height = Math.min(el.scrollHeight, 120) + 'px'
}
watch(open, value => { if (value && entries.value.length) void reveal(busy.value ? 'end' : 'question', !busy.value) })
async function load() {
  loading.value = true; error.value = ''
  try { const policy = await loadPolicy(); body.value = saved.value = policy.body; loaded.value = true; mode.value = policy.body.trim() ? 'preview' : 'edit' }
  catch { error.value = t('error') }
  finally { loading.value = false }
  try { providers.value = await loadProviders() } catch { assistantError.value = t('error') }
}
function restart() {
  if (busy.value) return
  entries.value = []; reply.value = null; applied.value = false; input.value = ''; assistantError.value = ''; failureCode.value = null; pendingId = null
}
async function save() {
  if (busy.value) return
  saving.value = true; error.value = ''
  try {
    const result = await savePolicy(body.value); saved.value = result.body; open.value = false
    restart()
  } catch { error.value = t('error') }
  finally { saving.value = false }
}
function confirm() {
  if (!reply.value?.policy) return
  body.value = reply.value.policy; applied.value = true; mode.value = 'edit'
}
async function send(answer?: string) {
  if (busy.value || !provider.value) return
  busy.value = true; assistantError.value = ''; failureCode.value = null; applied.value = false; reply.value = null
  if (answer?.trim()) {
    const text = answer.trim()
    entries.value.push({ role: 'user', text, wire: text, name: null }); input.value = ''; pendingId = null
    void nextTick(grow)
  }
  void reveal('end')
  let failure: string | null = null
  try { failure = await converse() }
  catch { failure = 'policy_assistant_failed' }
  finally { busy.value = false }
  if (failure) { failureCode.value = failure; assistantError.value = errorText(failure); void reveal('end') }
  else if (reply.value) void reveal('question', true)
}
/**
 * 0026 (0004-CH): one answer from the AI, or the code of a failure worth showing. A start or result call that does not
 * get through is tried again quietly (growing gaps, up to QUIET_LIMIT in a row); a request the server lost on a restart
 * (404) is sent again with the same conversation. A `failed` result is final: the server already asked the AI again.
 */
async function converse(): Promise<string | null> {
  let misses = 0, resends = 0
  while (!disposed) {
    if (!pendingId) {
      try {
        const locale = ['ko', 'ja', 'en'].includes(composer.locale.value) ? composer.locale.value : 'ko'
        const messages = entries.value.map(entry => ({ role: entry.role, content: entry.wire }))
        pendingId = (await startAssistant(provider.value, body.value, messages, locale)).request_id; misses = 0
      } catch (caught) {
        const e = normalizeApiError(caught)
        if (!transient(e) || ++misses > QUIET_LIMIT) return e.code ?? 'policy_assistant_failed'
        await wait(backoff(misses)); continue
      }
    }
    let result: Awaited<ReturnType<typeof assistantResult>>
    try { result = await assistantResult(pendingId); misses = 0 }
    catch (caught) {
      const e = normalizeApiError(caught)
      if (e.status === 404) {
        pendingId = null
        if (++resends > RESEND_LIMIT) return 'policy_assistant_failed'
        continue
      }
      if (!transient(e) || ++misses > QUIET_LIMIT) return e.code ?? 'policy_assistant_failed'
      await wait(backoff(misses)); continue
    }
    if (result.status === 'failed') { pendingId = null; return result.error_code ?? 'policy_assistant_failed' }
    if (result.status === 'completed' && result.reply) {
      const name = providers.value.find(p => p.id === provider.value)?.name ?? null
      reply.value = result.reply; pendingId = null
      entries.value.push({ role: 'assistant', text: result.reply.message || t('understood'), wire: wireOf(result.reply), name })
      return null
    }
    await wait(1000)
  }
  return null
}
onMounted(load)
</script>
<style scoped>
/* v02test.0096 F96-7: the panel takes the whole height it is given (window and full screen); the body fills what the
   top line and the foot leave, so no blank space sits under it. */
.question-policy { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 0; box-sizing: border-box; }
.qp-head { flex: none; display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.qp-title { font-size: 14px; font-weight: 700; color: var(--fg2-text); }
.qp-mode { display: inline-flex; overflow: hidden; border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-sm, 7px); background: var(--fg2-control-surface-flat); }
.qp-mode-btn { padding: 3px 12px; border: 0; border-radius: 0; background: transparent; color: var(--fg2-muted); font: inherit; font-size: 12px; cursor: pointer; }
.qp-mode-btn.is-active { background: var(--fg2-status-info-bg); color: var(--fg2-text); font-weight: 600; }
.qp-status { margin-left: auto; font-size: 11.5px; color: var(--fg2-status-success-fg); white-space: nowrap; }
.qp-status.is-dirty { color: var(--fg2-status-warning-fg, var(--fg2-muted)); }
.qp-status.is-over { color: var(--fg2-status-danger-fg); }
textarea#question-policy-body, .qp-preview { flex: 1 1 auto; width: 100%; box-sizing: border-box; min-height: 96px; color: var(--fg2-text, inherit); }
/* 편집: the editor as before (a box), now filling the height. */
textarea#question-policy-body { padding: 10px 12px; font: inherit; line-height: 1.5; resize: none;
  background: var(--fg2-bg, transparent); border: 1px solid var(--fg2-line-strong, #aaa); border-radius: 8px; }
/* 미리보기: a document, not an input box — no border, the UI font, ## as bold sub-headings and - as • lists. */
.qp-preview { overflow-y: auto; padding: 2px 2px 4px; font-size: 13px; line-height: 1.6; }
.qp-preview-empty { margin: 0; color: var(--fg2-muted); }
.qp-doc :deep(h1), .qp-doc :deep(h2), .qp-doc :deep(h3), .qp-doc :deep(h4) { margin: .9em 0 .25em; font-size: 13px; font-weight: 700; color: var(--fg2-text); }
.qp-doc :deep(ul) { list-style: none; padding-left: 0; }
.qp-doc :deep(ul > li) { position: relative; padding-left: 1.1em; }
.qp-doc :deep(ul > li)::before { content: '•'; position: absolute; left: .2em; color: var(--fg2-muted); }
.qp-doc :deep(li + li) { margin-top: .1em; }
.qp-foot { flex: none; margin: 0; font-size: 11.5px; color: var(--fg2-muted); }
.qp-foot code { font-family: var(--fg2-font-mono, ui-monospace, monospace); font-size: 11px; }
.qp-alert { margin: 0; }
.policy-dialog { display: flex; flex-direction: column; gap: 8px; height: 100%; min-height: 0; }
.qp-top { display: flex; align-items: center; gap: 8px; }
.qp-top select { flex: 1; min-width: 0; padding: 6px 8px; font: inherit; }
.qp-state { margin: 0; color: var(--fg2-muted); font-size: 11px; }
/* Same log as the chat panel (.conv-log). */
.qp-log { flex: 1; min-height: 80px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding: 10px 9px 12px;
  border: 1px solid var(--fg2-line); border-radius: var(--fg2-control-radius); background: var(--fg2-bg); }
.qp-log > * { flex-shrink: 0; }
.qp-box { border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface-flat); padding: 8px 10px; }
.qp-box-title { margin-bottom: 4px; font-size: 10.5px; font-weight: 700; color: var(--fg2-muted); }
.qp-box-body { font-size: 12px; line-height: 1.5; color: var(--fg2-text); overflow-wrap: anywhere; }
.qp-plain { white-space: pre-wrap; }
.qp-draft { border-color: var(--fg2-status-info-border); }
.qp-md :deep(> :first-child) { margin-top: 0; }
.qp-md :deep(> :last-child) { margin-bottom: 0; }
.qp-md :deep(p) { margin: 0 0 .5em; }
.qp-md :deep(h1), .qp-md :deep(h2), .qp-md :deep(h3), .qp-md :deep(h4) { margin: .7em 0 .3em; font-size: 12px; font-weight: 700; }
.qp-md :deep(ul), .qp-md :deep(ol) { margin: 0 0 .5em; padding-left: 1.4em; }
.qp-md :deep(ul) { list-style: disc; }
.qp-md :deep(ol) { list-style: decimal; }
.qp-draft-actions { display: flex; align-items: center; gap: 10px; margin-top: 8px; font-size: 11px; color: var(--fg2-muted); }
.qp-choices { display: flex; flex-wrap: wrap; gap: 6px; padding-left: 4px; }
.qp-choice { display: inline-flex; align-items: center; gap: 6px; max-width: 100%; padding: 5px 10px; text-align: left;
  border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius-pill); background: var(--fg2-control-surface);
  color: var(--fg2-text); font: inherit; font-size: 12px; cursor: pointer; }
.qp-choice:hover, .qp-choice:focus-visible { border-color: var(--fg2-blue); }
.qp-choice.is-recommended { border-color: var(--fg2-blue); background: var(--fg2-status-info-bg); }
.qp-badge { flex-shrink: 0; padding: 0 6px; border-radius: var(--fg2-control-radius-pill); background: var(--fg2-blue); color: var(--fg2-panel, #fff); font-size: 10px; font-weight: 700; }
.qp-thinking { display: flex; align-items: center; gap: 6px; color: var(--fg2-muted); font-size: 11px; }
.qp-dots { display: inline-flex; gap: 3px; }
.qp-dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--fg2-violet, currentColor); animation: qp-blink 1.2s infinite ease-in-out; }
.qp-dots i:nth-child(2) { animation-delay: .2s; }
.qp-dots i:nth-child(3) { animation-delay: .4s; }
@keyframes qp-blink { 0%, 80%, 100% { opacity: .25; } 40% { opacity: 1; } }
.qp-note { margin: 0; font-size: 11px; color: var(--fg2-status-success-fg); }
.qp-error { margin: 0; font-size: 11px; color: var(--fg2-status-danger-fg); }
.qp-link { border: 0; padding: 0; background: transparent; color: var(--fg2-status-info-fg); font: inherit; font-size: 11px; text-decoration: underline; cursor: pointer; }
.qp-link:disabled { cursor: not-allowed; opacity: .5; }
/* Same input as the chat composer (.conv-pill). */
.qp-pill { display: grid; grid-template-columns: minmax(0, 1fr) 39px; align-items: end; min-height: 43px; overflow: hidden;
  border: 1px solid var(--fg2-line-strong); border-radius: var(--fg2-control-radius); background: var(--fg2-control-surface); }
.qp-input { min-width: 0; min-height: 43px; max-height: 120px; height: 43px; resize: none; border: 0; outline: 0; background: transparent;
  color: var(--fg2-text); padding: 12px 9px 8px; font: 12px/1.35 var(--fg2-font, inherit); }
.qp-input::placeholder { color: var(--fg2-muted-2); }
.qp-send { width: 32px; height: 32px; margin: 5px 5px 5px 0; padding: 0; display: grid; place-items: center; font-size: 14px; cursor: pointer;
  border: 1px solid var(--fg2-blue); border-radius: var(--fg2-control-radius-sm); background: var(--fg2-status-info-bg); color: var(--fg2-status-info-fg); }
.qp-send:disabled { cursor: not-allowed; opacity: var(--fg2-control-disabled-opacity, .5); }
</style>

