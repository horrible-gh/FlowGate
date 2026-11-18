<template>
  <!-- flowgate.v02test.0042 — '병합 · 충돌' (시안 v7 items 2 · 3): the card result's merge, handled in the card.
       ① catching up: progress only · ② could not reconcile: overlapping files + [AI로 해결 ▾] / [파일별로 직접 고르기] /
       [이 결과 버리고 최신 코드로 다시 실행] · ③ per-file pick (AI에게 / 이 카드 쪽 / 최신 코드 쪽, no editor) and [병합]
       once every file is resolved · ④ merged · waiting: what it waits for + [‘…’ 카드로 이동]. -->
  <section class="merge-tab" data-test="merge-tab" :data-screen="screen" :aria-busy="busy ? 'true' : 'false'">
    <p v-if="screen === 'loading' && !readFailed" class="merge-note" role="status">{{ t('gui.inspector.merge.loading') }}</p>
    <p v-if="readFailed" class="merge-alert" role="alert" data-test="merge-read-failed">
      {{ t('gui.inspector.merge.read_failed') }}
      <button type="button" class="merge-link" @click="reload">{{ t('gui.inspector.merge.retry') }}</button>
    </p>

    <template v-if="screen === 'waiting' && state">
      <h3 class="merge-title is-info">{{ t('gui.inspector.merge.waiting.title') }}</h3>
      <p class="merge-body" data-test="merge-waiting-text">
        {{ state.blockedBy ? t('gui.inspector.merge.waiting.blocked', { title: state.blockedBy.title }) : t('gui.inspector.merge.waiting.turn') }}
      </p>
      <p v-if="reasonText" class="merge-reason">{{ reasonText }}</p>
      <Button v-if="state.blockedBy" size="sm" data-test="merge-go-blocked" @click="goBlocked">
        {{ t('gui.inspector.merge.waiting.go', { title: state.blockedBy.title }) }}
      </Button>
    </template>

    <template v-else-if="screen === 'catching_up' && state">
      <h3 class="merge-title is-info"><span class="merge-spinner" aria-hidden="true" />{{ t('gui.inspector.merge.catching_up.title') }}</h3>
      <p class="merge-body" data-test="merge-catching-up-text">
        {{ state.resolving ? t('gui.inspector.merge.catching_up.resolving') : t('gui.inspector.merge.catching_up.merging') }}
      </p>
      <ul v-if="state.files.length" class="merge-files" data-test="merge-files">
        <li v-for="file in state.files" :key="file.path" :data-resolved="file.resolved ? 'true' : 'false'">
          <code>{{ file.path }}</code><span v-if="file.resolved" class="merge-file-done">{{ t('gui.inspector.merge.file_resolved') }}</span>
        </li>
      </ul>
    </template>

    <template v-else-if="screen === 'needs_choice' && state">
      <h3 class="merge-title is-danger">{{ t('gui.inspector.merge.needs_choice.title') }}</h3>
      <p class="merge-body">{{ allResolved ? t('gui.inspector.merge.needs_choice.resolved_all') : t('gui.inspector.merge.needs_choice.body') }}</p>
      <p v-if="state.reason === 'conflict_deferred'" class="merge-reason" data-test="merge-deferred">{{ t('gui.inspector.merge.needs_choice.deferred') }}</p>
      <template v-if="state.files.length">
        <h4 class="merge-subtitle">{{ t('gui.inspector.merge.files') }}</h4>
        <ul class="merge-files" data-test="merge-files">
          <li v-for="file in state.files" :key="file.path" :data-resolved="file.resolved ? 'true' : 'false'">
            <code>{{ file.path }}</code><span v-if="file.resolved" class="merge-file-done">{{ t('gui.inspector.merge.file_resolved') }}</span>
          </li>
        </ul>
      </template>
      <div class="merge-actions">
        <Button v-if="can('merge')" size="sm" variant="primary" :disabled="busy" data-test="merge-merge" @click="merge">
          {{ t('gui.inspector.merge.needs_choice.merge') }}
        </Button>
        <Button v-if="can('ai_resolve')" size="sm" :disabled="busy" :aria-expanded="aiOpen ? 'true' : 'false'" data-test="merge-ai" @click="toggleAi">
          {{ t('gui.inspector.merge.needs_choice.ai') }} ▾
        </Button>
        <Button v-if="can('pick')" size="sm" :disabled="busy" data-test="merge-pick-open" @click="openPick">
          {{ t('gui.inspector.merge.needs_choice.pick') }}
        </Button>
        <Button v-if="can('discard_rerun')" size="sm" variant="danger" :disabled="busy" data-test="merge-discard" @click="confirmOpen = true">
          {{ t('gui.inspector.merge.needs_choice.discard') }}
        </Button>
      </div>
      <p v-if="can('merge')" class="merge-reason">{{ t('gui.inspector.merge.needs_choice.merge_note') }}</p>
      <div v-if="aiOpen" class="merge-ai-pick" data-test="merge-ai-picker">
        <label class="merge-label" for="merge-ai-provider">{{ t('gui.inspector.merge.needs_choice.ai_pick') }}</label>
        <select id="merge-ai-provider" v-model="providerId" class="merge-select" data-test="merge-ai-provider">
          <option :value="''">{{ defaultLabel }}</option>
          <option v-for="provider in otherProviders" :key="provider.id" :value="provider.id">{{ provider.name }}</option>
        </select>
        <Button size="sm" variant="primary" :disabled="busy" data-test="merge-ai-start" @click="startAi">{{ t('gui.inspector.merge.needs_choice.ai_start') }}</Button>
      </div>
    </template>

    <template v-else-if="screen === 'pick' && state">
      <h3 class="merge-title is-danger">{{ t('gui.inspector.merge.pick.title') }}</h3>
      <ul class="merge-pick-list" data-test="merge-pick-list">
        <li v-for="file in openFiles" :key="file.path" class="merge-pick-row" :data-path="file.path">
          <code>{{ file.path }}</code>
          <span class="merge-pick-sides" role="radiogroup" :aria-label="file.path">
            <label v-for="side in SIDES" :key="side"><input v-model="choices[file.path]" type="radio" :name="`merge-pick-${file.path}`" :value="side" :data-test="`merge-pick-${side}`" />{{ t(`gui.inspector.merge.pick.${side}`) }}</label>
          </span>
        </li>
      </ul>
      <div class="merge-actions">
        <Button size="sm" variant="primary" :disabled="busy" data-test="merge-pick-submit" @click="submitPick">{{ t('gui.inspector.merge.pick.submit') }}</Button>
        <Button size="sm" variant="secondary" :disabled="busy" data-test="merge-pick-cancel" @click="picking = false">{{ t('gui.inspector.merge.pick.cancel') }}</Button>
      </div>
    </template>

    <template v-else-if="screen === 'failed' && state">
      <h3 class="merge-title is-danger">{{ t('gui.inspector.merge.failed.title') }}</h3>
      <p class="merge-body" data-test="merge-failed-text">{{ reasonText }}</p>
      <div class="merge-actions">
        <Button v-if="can('discard_rerun')" size="sm" variant="danger" :disabled="busy" data-test="merge-discard" @click="confirmOpen = true">
          {{ t('gui.inspector.merge.needs_choice.discard') }}
        </Button>
      </div>
    </template>

    <template v-else-if="screen === 'merged'">
      <h3 class="merge-title is-ok">{{ t('gui.inspector.merge.merged.title') }}</h3>
      <p class="merge-body" data-test="merge-merged-text">{{ t('gui.inspector.merge.merged.body') }}</p>
    </template>

    <p v-if="errorText" class="merge-alert" role="alert" data-test="merge-error">{{ errorText }}</p>

    <ConfirmDialog
      :open="confirmOpen"
      danger
      :title="t('gui.inspector.merge.confirm_discard.title')"
      :message="t('gui.inspector.merge.confirm_discard.body')"
      :confirm-label="t('gui.inspector.merge.confirm_discard.ok')"
      :cancel-label="t('gui.inspector.merge.confirm_discard.cancel')"
      @confirm="discard"
      @cancel="confirmOpen = false"
    />
  </section>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import ConfirmDialog from '../../../../../components/ui/ConfirmDialog.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { mergeReasonKey } from './mergeModel'
import { openCardMerge, useMerge } from './useMerge'

const props = defineProps<{ target: InspectorTarget }>()
const { t, te } = useI18n()

const SIDES = ['ai', 'card', 'latest'] as const
type Side = (typeof SIDES)[number]

const { state, screen, readFailed, error, busy, picking, providers, reload, aiResolve, pick, merge, discardRerun, loadProviders } =
  useMerge(() => props.target)

const can = (action: string) => !!state.value?.actions.includes(action as never)
const allResolved = computed(() => !!state.value?.files.length && state.value.files.every((file) => file.resolved))
const openFiles = computed(() => (state.value?.files ?? []).filter((file) => !file.resolved))

const reasonText = computed(() => {
  const reason = state.value?.reason ?? null
  if (!reason) return ''
  const key = mergeReasonKey(reason)
  return t(`gui.inspector.merge.reasons.${key}`, { reason })
})

const errorText = computed(() => {
  const failure = error.value
  if (!failure) return ''
  const code = failure.network ? 'network' : failure.code ?? 'error'
  const known = `gui.inspector.merge.error_codes.${code}`
  const message = te(known) ? t(known) : failure.message || String(failure.status ?? '')
  return t('gui.inspector.merge.error', { code, message })
})

// ② [AI로 해결 ▾]: the AI defaults to this card's run AI (the server's default_provider).
const aiOpen = ref(false)
const providerId = ref('')
const defaultLabel = computed(() => {
  const provider = state.value?.defaultProvider
  return provider ? `${provider.name} (${t('gui.inspector.merge.needs_choice.ai_default')})` : t('gui.inspector.merge.needs_choice.ai_default')
})
const otherProviders = computed(() => providers.value.filter((provider) => provider.id !== state.value?.defaultProvider?.id))

function toggleAi(): void {
  aiOpen.value = !aiOpen.value
  if (aiOpen.value) void loadProviders()
}

async function startAi(): Promise<void> {
  aiOpen.value = false
  await aiResolve(providerId.value || null)
}

// ③ per-file pick: every file starts on the AI.
const choices = reactive<Record<string, Side>>({})
function openPick(): void {
  for (const file of openFiles.value) if (!choices[file.path]) choices[file.path] = 'ai'
  aiOpen.value = false
  picking.value = true
}

async function submitPick(): Promise<void> {
  await pick(openFiles.value.map((file) => ({ path: file.path, side: choices[file.path] ?? 'ai' })), providerId.value || null)
}

const confirmOpen = ref(false)
async function discard(): Promise<void> {
  confirmOpen.value = false
  await discardRerun()
}

function goBlocked(): void {
  const blocked = state.value?.blockedBy
  if (blocked) openCardMerge(props.target.flowId, blocked.nodeKey)
}

watch(() => props.target.nodeId, () => {
  aiOpen.value = false
  confirmOpen.value = false
  providerId.value = ''
  for (const key of Object.keys(choices)) delete choices[key]
})
</script>

<style scoped>
.merge-tab { display: flex; flex-direction: column; gap: 8px; min-width: 0; font-size: 12px; }
.merge-title { display: flex; align-items: center; gap: 6px; margin: 0; font-size: 13px; font-weight: 650; }
.merge-title.is-info { color: var(--fg2-blue); }
.merge-title.is-danger { color: var(--fg2-status-danger-fg); }
.merge-title.is-ok { color: var(--fg2-cyan); }
.merge-subtitle { margin: 4px 0 0; color: var(--fg2-muted); font-size: 11.5px; font-weight: 600; }
.merge-body, .merge-reason, .merge-note { margin: 0; }
.merge-reason, .merge-note { color: var(--fg2-muted); }
.merge-alert { margin: 0; color: var(--fg2-status-danger-fg); }
.merge-link { margin-left: 6px; padding: 0; border: 0; background: none; color: var(--fg2-blue); cursor: pointer; text-decoration: underline; }
.merge-files, .merge-pick-list { display: grid; gap: 4px; margin: 0; padding: 0; list-style: none; }
.merge-files li, .merge-pick-row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; min-width: 0; }
.merge-files code, .merge-pick-row code { overflow-wrap: anywhere; color: var(--fg2-text); }
.merge-file-done { color: var(--fg2-cyan); font-size: 11px; }
.merge-pick-sides { display: inline-flex; flex-wrap: wrap; gap: 10px; }
.merge-pick-sides label { display: inline-flex; align-items: center; gap: 4px; cursor: pointer; }
.merge-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.merge-ai-pick { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.merge-select { min-width: 160px; }
.merge-spinner { width: 10px; height: 10px; border: 2px solid currentColor; border-right-color: transparent; border-radius: 50%; animation: merge-spin 0.9s linear infinite; }
@keyframes merge-spin { to { transform: rotate(360deg); } }
</style>
