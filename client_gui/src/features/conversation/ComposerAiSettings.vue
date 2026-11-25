<template>
  <!-- AI tab of the ⚙ settings window (flowgate.v02test.0096 ④): this chat's AI, the 📌 pins of 자주 쓰는 AI (the same
       pins the AI picker and AI 관리 use, card ①'s composable) and my own 최대 반복 횟수 / 제목 짓기 AI with the system
       values beside them (the existing /me/ai-preferences API). Every change here is saved at once. -->
  <div class="cas" data-test="conv-ai-settings">
    <label class="cas-row">
      <span class="cas-label">{{ t('gui.conversation.settings.ai_this_chat') }}</span>
      <select
        class="cas-select"
        data-test="conv-setting-ai"
        :value="selectedId"
        :title="selectedName"
        :disabled="disabled || providers.length === 0"
        @change="emit('select-provider', ($event.target as HTMLSelectElement).value)"
      >
        <option v-if="!providers.some((p) => p.id === selectedId)" value="" disabled>{{ selectedName }}</option>
        <option v-for="p in providers" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
    </label>

    <div class="cas-block">
      <div class="cas-label">{{ t('gui.conversation.settings.ai_pinned') }}</div>
      <p class="cas-hint">{{ t('gui.conversation.settings.ai_pinned_hint') }}</p>
      <ul v-if="pinnedRows.length" class="cas-pins" data-test="conv-setting-pins">
        <li v-for="row in pinnedRows" :key="row.id" class="cas-pin" :data-provider-id="row.id">
          <span aria-hidden="true">📌</span>
          <span class="cas-pin-name" :title="row.name">{{ row.name }}</span>
          <button
            type="button"
            class="cas-icon"
            data-test="conv-setting-pin-remove"
            :aria-label="t('gui.conversation.settings.ai_pin_remove', { name: row.name })"
            :title="t('gui.conversation.settings.ai_pin_remove', { name: row.name })"
            @click="favorites.setPinned(row.id, false)"
          >×</button>
        </li>
      </ul>
      <p v-else class="cas-hint" data-test="conv-setting-pins-empty">{{ t('gui.conversation.settings.ai_pinned_empty') }}</p>
      <select
        v-if="unpinned.length"
        class="cas-select cas-add"
        data-test="conv-setting-pin-add"
        value=""
        :aria-label="t('gui.conversation.settings.ai_pin_add')"
        @change="addPin($event)"
      >
        <option value="" disabled>＋ {{ t('gui.conversation.settings.ai_pin_add') }}</option>
        <option v-for="p in unpinned" :key="p.id" :value="p.id">{{ p.name }}</option>
      </select>
    </div>

    <div v-if="prefsState === 'loading'" class="cas-hint">{{ t('gui.conversation.settings.ai_prefs_loading') }}</div>
    <div v-else-if="prefsState === 'error'" class="cas-hint is-error" role="alert" data-test="conv-setting-prefs-error">
      {{ t('gui.conversation.settings.ai_prefs_failed') }}
      <button type="button" class="cas-link" @click="loadPrefs">{{ t('gui.conversation.retry') }}</button>
    </div>
    <template v-else-if="prefs">
      <div class="cas-row">
        <span class="cas-label">
          {{ t('gui.conversation.settings.ai_repeat') }}
          <small class="cas-ref" data-test="conv-setting-repeat-system">{{ t('gui.conversation.settings.ai_repeat_system', { max: prefs.repeat_count_system_max }) }}</small>
        </span>
        <span class="cas-stepper">
          <button
            type="button"
            class="cas-icon"
            data-test="conv-setting-repeat-decrease"
            :aria-label="t('gui.conversation.settings.ai_repeat_decrease')"
            :disabled="saving || prefs.repeat_count_max_effective <= prefs.repeat_count_min"
            @click="stepRepeat(-1)"
          >−</button>
          <b aria-live="polite" data-test="conv-setting-repeat-value">{{ prefs.repeat_count_max_effective }}</b>
          <button
            type="button"
            class="cas-icon"
            data-test="conv-setting-repeat-increase"
            :aria-label="t('gui.conversation.settings.ai_repeat_increase')"
            :disabled="saving || prefs.repeat_count_max_effective >= prefs.repeat_count_system_max"
            @click="stepRepeat(1)"
          >＋</button>
          <button
            v-if="prefs.repeat_count_max !== null"
            type="button"
            class="cas-link"
            data-test="conv-setting-repeat-reset"
            :disabled="saving"
            @click="resetRepeat"
          >{{ t('gui.conversation.settings.ai_repeat_reset') }}</button>
        </span>
      </div>
      <label class="cas-row">
        <span class="cas-label">
          {{ t('gui.conversation.settings.ai_title') }}
          <small class="cas-ref" data-test="conv-setting-title-effective">{{ t('gui.conversation.settings.ai_title_effective', { name: effectiveTitleName }) }}</small>
        </span>
        <select
          class="cas-select"
          data-test="conv-setting-title-ai"
          :value="prefs.title_provider"
          :title="titleName(prefs.title_provider)"
          :disabled="saving"
          @change="chooseTitle(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="o in titleOptions" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</option>
        </select>
      </label>
    </template>
    <div v-if="saveError" class="cas-hint is-error" role="alert" data-test="conv-setting-prefs-save-error">{{ saveError }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import { useAiFavorites } from '@shared/aiFavorites'
import { fetchMyAiPrefs, saveMyAiPrefs } from './api'
import type { ChatProvider, MyAiPrefs, MyAiPrefsUpdate } from './types'

const props = defineProps<{
  providers: readonly ChatProvider[]
  selectedId: string
  disabled?: boolean
}>()

const emit = defineEmits<{ 'select-provider': [providerId: string] }>()

const { t } = useI18n()
const { user } = useCurrentUser()
const favorites = useAiFavorites(() => user.value?.username)

const nameOf = (id: string) => props.providers.find((p) => p.id === id)?.name ?? id
const selectedName = computed(() => (props.selectedId ? nameOf(props.selectedId) : t('gui.conversation.composer.provider_none')))

// ── 📌 pins (shared with the AI picker and AI 관리) ─────────────────────────────────────
const pinnedRows = computed(() => favorites.pinned.value.map((id) => ({ id, name: nameOf(id) })))
const unpinned = computed(() => props.providers.filter((p) => !favorites.pinned.value.includes(p.id)))

function addPin(event: Event): void {
  const select = event.target as HTMLSelectElement
  const id = select.value
  select.value = ''
  if (id) favorites.setPinned(id, true)
}

// ── my repeat limit and title AI (/me/ai-preferences) ───────────────────────────────────
const prefs = ref<MyAiPrefs | null>(null)
const prefsState = ref<'loading' | 'ready' | 'error'>('loading')
const saving = ref(false)
const saveError = ref('')
let disposed = false

// A body without the contract's fields (an older server) counts as a failed read.
function isPrefs(value: unknown): value is MyAiPrefs {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return typeof p.title_provider === 'string'
    && typeof p.repeat_count_max_effective === 'number'
    && typeof p.repeat_count_system_max === 'number'
    && typeof p.repeat_count_min === 'number'
    && (p.repeat_count_max === null || p.repeat_count_max === undefined || typeof p.repeat_count_max === 'number')
}

const normalized = (p: MyAiPrefs): MyAiPrefs => ({ ...p, repeat_count_max: p.repeat_count_max ?? null })

async function loadPrefs(): Promise<void> {
  prefsState.value = 'loading'
  try {
    const data = await fetchMyAiPrefs()
    if (disposed) return
    if (!isPrefs(data)) throw new Error('invalid')
    // A change saved meanwhile is newer than this read.
    if (!saving.value) prefs.value = normalized(data)
    prefsState.value = 'ready'
  } catch {
    if (!disposed) prefsState.value = 'error'
  }
}

// Shown at once and saved at once; the server's answer replaces it, a failure puts the previous value back.
async function save(update: MyAiPrefsUpdate, shown: MyAiPrefs): Promise<void> {
  const previous = prefs.value
  if (!previous || saving.value) return
  prefs.value = shown
  saving.value = true
  saveError.value = ''
  try {
    const data = await saveMyAiPrefs(update)
    if (!disposed && isPrefs(data)) prefs.value = normalized(data)
  } catch (err: unknown) {
    if (disposed) return
    prefs.value = previous
    saveError.value = err instanceof Error && err.message ? err.message : t('gui.conversation.settings.ai_prefs_save_failed')
  } finally {
    saving.value = false
  }
}

function stepRepeat(delta: number): void {
  const p = prefs.value
  if (!p) return
  const next = p.repeat_count_max_effective + delta
  if (next < p.repeat_count_min || next > p.repeat_count_system_max) return
  void save({ repeat_count_max: next }, { ...p, repeat_count_max: next, repeat_count_max_effective: next })
}

// Back to "not set": the system value applies again.
function resetRepeat(): void {
  const p = prefs.value
  if (!p || p.repeat_count_max === null) return
  void save({ repeat_count_max: null }, { ...p, repeat_count_max: null, repeat_count_max_effective: p.repeat_count_system_max })
}

const SEPARATOR = '__separator__'
interface TitleOption {
  value: string
  label: string
  disabled?: boolean
}
const fixedTitle = computed<TitleOption[]>(() => [
  { value: 'auto', label: t('gui.conversation.settings.ai_title_auto') },
  { value: 'off', label: t('gui.conversation.settings.ai_title_off') },
  { value: 'executor', label: t('gui.conversation.settings.ai_title_executor') },
])

// 자동 / 끄기 / 답한 AI 그대로 / separator / the AI this chat can use. The stored value is always offered, so the
// select never claims another one.
const titleOptions = computed<TitleOption[]>(() => {
  const current = prefs.value?.title_provider ?? ''
  const ai: TitleOption[] = props.providers.map((p) => ({ value: p.id, label: p.name }))
  if (current && !fixedTitle.value.some((o) => o.value === current) && !ai.some((o) => o.value === current)) {
    ai.push({ value: current, label: current })
  }
  return ai.length ? [...fixedTitle.value, { value: SEPARATOR, label: '──────────', disabled: true }, ...ai] : fixedTitle.value
})

function titleName(value: string): string {
  return fixedTitle.value.find((o) => o.value === value)?.label ?? nameOf(value)
}

const effectiveTitleName = computed(() => {
  const p = prefs.value
  if (!p) return ''
  return titleName(p.title_provider_effective || p.title_provider)
})

function chooseTitle(value: string): void {
  const p = prefs.value
  if (!p || value === p.title_provider || value === SEPARATOR) return
  void save({ title_provider: value }, { ...p, title_provider: value })
}

onMounted(() => { void loadPrefs() })
onBeforeUnmount(() => { disposed = true })
</script>

<style scoped>
.cas {
  display: grid;
  gap: 10px;
}

.cas-row {
  display: grid;
  grid-template-columns: minmax(64px, max-content) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
}

.cas-label {
  color: var(--fg2-text);
  font-weight: 600;
  display: grid;
}

.cas-ref,
.cas-hint {
  color: var(--fg2-muted);
  font-weight: 400;
  font-size: 10.5px;
}

.cas-hint {
  margin: 2px 0;
}

.cas-hint.is-error {
  color: var(--fg2-status-danger-fg, #d33);
}

.cas-select {
  width: 100%;
  min-width: 0;
  height: 27px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: inherit;
  padding: 0 5px;
}

.cas-add {
  margin-top: 4px;
}

.cas-block {
  display: grid;
  gap: 2px;
}

.cas-pins {
  list-style: none;
  margin: 2px 0 0;
  padding: 0;
  display: grid;
  gap: 3px;
}

.cas-pin {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 3px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  color: var(--fg2-text);
}

/* Long AI names wrap instead of being cut. */
.cas-pin-name {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.cas-stepper {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  color: var(--fg2-text);
}

.cas-icon {
  min-width: 24px;
  height: 24px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  cursor: pointer;
}

.cas-icon:disabled {
  cursor: not-allowed;
  opacity: var(--fg2-control-disabled-opacity);
}

.cas-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  padding: 0;
  cursor: pointer;
}
</style>
