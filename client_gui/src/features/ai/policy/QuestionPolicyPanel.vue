<template>
  <section class="question-policy" data-test="question-policy">
    <div v-if="loading">{{ t('loading') }}</div>
    <template v-else>
      <header><label for="question-policy-body">{{ t('mine') }}</label>
        <Button variant="secondary" :disabled="!loaded" @click="open = true">{{ t('assist') }}</Button></header>
      <textarea id="question-policy-body" v-model="body" :disabled="!loaded || saving" :placeholder="t('example')" rows="13" />
      <footer><span>{{ [...body].length }} / 4000 · {{ t(dirty ? 'unsaved' : 'saved') }}</span>
        <Button :disabled="!loaded || saving || busy || !dirty || [...body].length > 4000" @click="save">{{ t('save') }}</Button></footer>
    </template>
    <p v-if="error" role="alert">{{ error }} <Button v-if="!loaded" @click="load">{{ t('retry') }}</Button></p>
    <ModelessShell :open="open" :title="t('assist')" :initial-x="80" :initial-y="100" :initial-width="510" :initial-height="570"
      :min-width="320" :min-height="340" @close="open = false">
      <section class="policy-dialog" role="dialog" aria-modal="false" :aria-label="t('assist')">
        <select v-model="provider" :aria-label="t('choose')" :disabled="busy">
          <option value="" disabled>{{ t('choose') }}</option>
          <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <p v-if="!providers.length">{{ t('empty') }}</p>
        <div class="messages" aria-live="polite">
          <p v-if="!messages.length && body"><strong>{{ t('current') }}</strong><br />{{ body }}</p>
          <p v-for="(message, index) in messages" :key="index" :class="message.role">{{ message.content }}</p>
          <p v-if="busy">{{ t('thinking') }}</p>
          <template v-if="reply && !busy && !applied">
            <div class="choices"><Button v-for="(choice, index) in reply.choices" :key="index" variant="secondary" @click="send(choice.label)">
              {{ choice.label }}<span v-if="choice.recommended"> · {{ t('recommended') }}</span>
            </Button></div>
            <template v-if="reply.policy"><h3>{{ t('understood') }}</h3><pre>{{ reply.policy }}</pre>
              <Button @click="confirm">{{ t('confirm') }}</Button></template>
          </template>
          <p v-if="applied">{{ t('applied') }}</p>
          <p v-if="assistantError" role="alert">{{ assistantError }}</p>
        </div>
        <Button v-if="!messages.length" :disabled="!provider || busy" @click="send()">{{ t('begin') }}</Button>
        <form v-else @submit.prevent="send(input)">
          <input v-model="input" :aria-label="t('answer')" :placeholder="t('answer')" :disabled="busy" maxlength="4000" />
          <Button type="submit" :disabled="busy || !provider || !input.trim()">{{ t('send') }}</Button>
        </form>
        <Button v-if="assistantError" :disabled="busy || !provider" @click="send()">{{ t('retry') }}</Button>
        <Button v-if="applied" :disabled="saving || [...body].length > 4000" @click="save">{{ t('save') }}</Button>
      </section>
    </ModelessShell>
  </section>
</template>
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import { installPolicyMessages } from './messages'
import { loadPolicy, savePolicy, loadProviders, startAssistant, assistantResult, type Message, type Reply } from './api'
const composer = useI18n({ useScope: 'global' }); installPolicyMessages(composer)
const t = (key: string) => composer.t('gui.questionPolicy.' + key)
const body = ref(''), saved = ref(''), provider = ref(''), input = ref(''), error = ref(''), assistantError = ref('')
const providers = ref<{ id: string; name: string }[]>([]), messages = ref<Message[]>([]), reply = ref<Reply | null>(null)
const loading = ref(true), loaded = ref(false), saving = ref(false), open = ref(false), busy = ref(false), applied = ref(false)
const dirty = computed(() => body.value !== saved.value)
let disposed = false
let pendingId: string | null = null
onBeforeUnmount(() => { disposed = true })
async function load() {
  loading.value = true; error.value = ''
  try { const policy = await loadPolicy(); body.value = saved.value = policy.body; loaded.value = true }
  catch { error.value = t('error') }
  finally { loading.value = false }
  try { providers.value = await loadProviders() } catch { assistantError.value = t('error') }
}
async function save() {
  if (busy.value) return
  saving.value = true; error.value = ''
  try {
    const result = await savePolicy(body.value); saved.value = result.body; open.value = false
    messages.value = []; reply.value = null; applied.value = false; input.value = ''
  } catch { error.value = t('error') }
  finally { saving.value = false }
}
function confirm() {
  if (!reply.value?.policy) return
  body.value = reply.value.policy; applied.value = true
}
async function send(answer?: string) {
  if (busy.value || !provider.value) return
  busy.value = true; assistantError.value = ''; applied.value = false; reply.value = null
  if (answer?.trim()) { messages.value.push({ role: 'user', content: answer.trim() }); input.value = ''; pendingId = null }
  try {
    if (!pendingId) {
      const locale = ['ko', 'ja', 'en'].includes(composer.locale.value) ? composer.locale.value : 'ko'
      pendingId = (await startAssistant(provider.value, body.value, messages.value.map(message => ({ ...message })), locale)).request_id
    }
    while (!disposed) {
      const result = await assistantResult(pendingId)
      if (result.status === 'failed') { pendingId = null; throw new Error(result.error_code) }
      if (result.status === 'completed' && result.reply) {
        reply.value = result.reply; messages.value.push({ role: 'assistant', content: [result.reply.message, ...result.reply.choices.map(choice => choice.label), result.reply.policy].filter(Boolean).join('\n') }); pendingId = null; break
      }
      await new Promise(resolve => setTimeout(resolve, 1000))
    }
  } catch { assistantError.value = t('error') }
  finally { busy.value = false }
}
onMounted(load)
</script>
<style scoped>
.question-policy { max-width: 820px; padding: 8px 0; }
header, footer, form { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
textarea { width: 100%; box-sizing: border-box; margin: 16px 0; resize: vertical; padding: 12px; font: inherit; color: inherit; background: var(--color-bg, transparent); border: 1px solid var(--color-border, #aaa); border-radius: 8px; }
.policy-dialog { display: flex; flex-direction: column; gap: 12px; height: 100%; min-height: 0; }
.messages { flex: 1; min-height: 80px; overflow-y: auto; }
.messages p, pre { white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; }
.user { padding: 8px 12px; border-left: 3px solid #7089ba; }
.choices { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
select, input { padding: 8px; font: inherit; min-width: 0; }
input { flex: 1; }
footer span { opacity: .75; }
</style>
