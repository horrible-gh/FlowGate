<template>
  <section class="ai-other-settings" data-test="ai-other-settings">
    <div v-if="loading && !loaded" class="panel-state">
      <LoadingState :label="t('gui.common.loading')" />
    </div>
    <template v-else>
      <div class="os-table" role="table" :aria-label="t('gui.aiManage.tabs.other')">
        <div class="os-row os-head" role="row">
          <span role="columnheader" class="os-name" />
          <span role="columnheader" class="os-col-title">{{ t('gui.aiManage.other.system') }}</span>
          <span role="columnheader" class="os-col-title">{{ t('gui.aiManage.other.mine') }}</span>
        </div>

        <!-- 최대 반복 횟수: the system ceiling (ai_repeat_count_max, administrators) beside my own value (/me/ai-preferences). -->
        <div class="os-row" role="row" data-test="repeat-row">
          <div class="os-name repeat-info" role="rowheader">
            <b>{{ t('gui.ai.providers.repeat_max') }}</b>
            <small>{{ t('gui.aiManage.other.repeat_hint') }}</small>
          </div>
          <div class="os-cell" role="cell" :data-label="t('gui.aiManage.other.system')">
            <div v-if="isAdmin && execPolicy" class="repeat-stepper" data-test="system-repeat-card">
              <IconButton
                :label="t('gui.ai.providers.repeat_decrease')"
                variant="secondary"
                size="sm"
                :disabled="repeatMax <= execPolicy.repeat_count_min"
                data-test="system-repeat-decrease"
                @click="stepRepeat(-1)"
              >
                <span aria-hidden="true">−</span>
              </IconButton>
              <b aria-live="polite" :aria-label="t('gui.aiManage.other.system_repeat')" data-test="system-repeat-value">{{ repeatMax }}</b>
              <IconButton
                :label="t('gui.ai.providers.repeat_increase')"
                variant="secondary"
                size="sm"
                :disabled="repeatMax >= execPolicy.repeat_count_hard_max"
                data-test="system-repeat-increase"
                @click="stepRepeat(1)"
              >
                <span aria-hidden="true">＋</span>
              </IconButton>
            </div>
            <b v-else-if="prefs" class="os-readonly" data-test="system-repeat-readonly">{{ prefs.repeat_count_system_max }}</b>
            <span v-else class="os-missing">{{ t('gui.aiManage.other.unavailable') }}</span>
          </div>
          <div class="os-cell" role="cell" :data-label="t('gui.aiManage.other.mine')">
            <div v-if="prefs" class="repeat-stepper" data-test="my-repeat-card">
              <Button
                v-if="prefs.repeat_count_max !== null"
                variant="secondary"
                size="sm"
                :disabled="prefsSaving"
                data-test="my-repeat-reset"
                @click="resetMyRepeat"
              >
                {{ t('gui.ai.providers.my_repeat_reset', { max: prefs.repeat_count_system_max }) }}
              </Button>
              <IconButton
                :label="t('gui.ai.providers.repeat_decrease')"
                variant="secondary"
                size="sm"
                :disabled="prefsSaving || prefs.repeat_count_max_effective <= prefs.repeat_count_min"
                data-test="my-repeat-decrease"
                @click="stepMyRepeat(-1)"
              >
                <span aria-hidden="true">−</span>
              </IconButton>
              <b aria-live="polite" data-test="my-repeat-value">{{ prefs.repeat_count_max_effective }}</b>
              <IconButton
                :label="t('gui.ai.providers.repeat_increase')"
                variant="secondary"
                size="sm"
                :disabled="prefsSaving || prefs.repeat_count_max_effective >= prefs.repeat_count_system_max"
                data-test="my-repeat-increase"
                @click="stepMyRepeat(1)"
              >
                <span aria-hidden="true">＋</span>
              </IconButton>
            </div>
            <span v-else class="os-missing">{{ t('gui.aiManage.other.unavailable') }}</span>
          </div>
        </div>

        <!-- 제목 짓기 AI: the system default (ai_title_provider, administrators) beside my own choice. -->
        <div class="os-row" role="row" data-test="title-row">
          <div class="os-name repeat-info" role="rowheader">
            <b>{{ t('gui.ai.providers.title_ai') }}</b>
            <small>{{ t('gui.aiManage.other.title_hint') }}</small>
          </div>
          <div class="os-cell" role="cell" :data-label="t('gui.aiManage.other.system')">
            <div v-if="isAdmin && titleProvider !== null" class="title-ai-card" data-test="title-ai-card">
              <Select
                class="title-ai-select ai-base-select"
                :model-value="titleProvider"
                :options="titleProviderOptions"
                :disabled="titleSaving"
                :aria-label="t('gui.aiManage.other.system_title')"
                data-test="title-ai-select"
                @update:model-value="chooseTitleProvider"
              />
            </div>
            <span v-else-if="!isAdmin" class="os-missing" :title="t('gui.aiManage.other.admin_only')" data-test="system-title-readonly">—</span>
            <span v-else class="os-missing">{{ t('gui.aiManage.other.unavailable') }}</span>
          </div>
          <div class="os-cell" role="cell" :data-label="t('gui.aiManage.other.mine')">
            <div v-if="prefs" class="title-ai-card" data-test="my-title-ai-card">
              <Select
                class="title-ai-select ai-base-select"
                :model-value="prefs.title_provider"
                :options="myTitleProviderOptions"
                :disabled="prefsSaving"
                :aria-label="t('gui.ai.providers.title_ai')"
                data-test="my-title-ai-select"
                @update:model-value="chooseMyTitleProvider"
              />
            </div>
            <span v-else class="os-missing">{{ t('gui.aiManage.other.unavailable') }}</span>
          </div>
        </div>
      </div>
      <p class="os-note">{{ t('gui.aiManage.other.note') }}</p>
    </template>

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </section>
</template>

<script setup lang="ts">
// v02test.0096 F96-3 "기타 설정": the repeat limit and title AI cards that sat under the AI list, now one table with the
// system value (administrators) and my own value side by side. The loads and saves are the ones the list used
// (v02test.0080 system title AI, v02test.0089 my preferences): each value is saved as soon as it changes and a failed
// save puts the previous value back. A user who is not an administrator sees the system column read-only.
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import {
  fetchMyAi,
  fetchMyAiOrder,
  fetchMyAiPreferences,
  fetchSystemAi,
  fetchTitleProvider,
  fetchVisibleSystemAi,
  saveMyAiPreferences,
  saveRepeatCountMax,
  saveTitleProvider,
} from '../api'
import '../aiBaseSelect.css'
import type { AiExecutionPolicy, MyAiPreferences, MyAiPreferencesUpdate, ProviderItem } from '../types'
import { installAiManageMessages } from './messages'

const composer = useI18n({ useScope: 'global' }); installAiManageMessages(composer)
const { t } = composer
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)

const loading = ref(false)
const loaded = ref(false)
const systemList = ref<ProviderItem[]>([])
const myList = ref<ProviderItem[]>([])
const order = ref<string[]>([])
const execPolicy = ref<AiExecutionPolicy | null>(null)
const repeatMax = ref(0)
// Title AI (ai_title_provider): null until read; the select stays hidden while the value is unknown.
const titleProvider = ref<string | null>(null)
const titleSaving = ref(false)
// My own title AI and repeat limit: null until read (or when the body is not in the contract shape).
const prefs = ref<MyAiPreferences | null>(null)
const prefsSaving = ref(false)
const toasts = ref<ToastHostItem[]>([])
let repeatTimer: ReturnType<typeof setTimeout> | null = null
let disposed = false

function pushToast(message: string): void {
  toasts.value = [...toasts.value, { id: `ai-other-${Date.now()}-${Math.random()}`, message, tone: 'danger', duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

// A body without the contract's fields (an older server) is treated as unavailable.
function isPreferences(value: unknown): value is MyAiPreferences {
  if (!value || typeof value !== 'object') return false
  const p = value as Record<string, unknown>
  return typeof p.title_provider === 'string'
    && typeof p.repeat_count_max_effective === 'number'
    && typeof p.repeat_count_system_max === 'number'
    && typeof p.repeat_count_min === 'number'
    && (p.repeat_count_max === null || p.repeat_count_max === undefined || typeof p.repeat_count_max === 'number')
}

// Every read stands alone: one that fails leaves its cell saying so and the rest of the table works.
async function load(): Promise<void> {
  loading.value = true
  const [system, mine, myOrder, title] = await Promise.allSettled([
    isAdmin.value ? fetchSystemAi() : fetchVisibleSystemAi(),
    fetchMyAi(),
    fetchMyAiOrder(),
    isAdmin.value ? fetchTitleProvider() : Promise.resolve(null),
    loadPreferences(),
  ])
  if (disposed) return
  if (system.status === 'fulfilled') {
    systemList.value = system.value.providers ?? []
    execPolicy.value = isAdmin.value ? system.value.execution_policy ?? null : null
    repeatMax.value = execPolicy.value?.repeat_count_max ?? 0
  }
  if (mine.status === 'fulfilled') myList.value = mine.value.providers ?? []
  if (myOrder.status === 'fulfilled') order.value = myOrder.value.order ?? []
  if (title.status === 'fulfilled' && !titleSaving.value) titleProvider.value = title.value
  loading.value = false
  loaded.value = true
}

async function loadPreferences(): Promise<void> {
  try {
    const data = await fetchMyAiPreferences()
    // A change saved meanwhile is newer than this read.
    if (!disposed && !prefsSaving.value && isPreferences(data)) prefs.value = { ...data, repeat_count_max: data.repeat_count_max ?? null }
  } catch {
    // My column says it could not load; the system column does not depend on it.
  }
}

// Shown at once and saved at once; the server's answer (same shape as GET) replaces it, a failure puts the previous back.
async function savePreferences(update: MyAiPreferencesUpdate, shown: MyAiPreferences): Promise<void> {
  const previous = prefs.value
  if (!previous || prefsSaving.value) return
  prefs.value = shown
  prefsSaving.value = true
  try {
    const data = await saveMyAiPreferences(update)
    if (!disposed && isPreferences(data)) prefs.value = { ...data, repeat_count_max: data.repeat_count_max ?? null }
  } catch (err: unknown) {
    if (disposed) return
    prefs.value = previous
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'))
  } finally {
    prefsSaving.value = false
  }
}

// −/＋ moves my own value within repeat_count_min ~ the system ceiling, starting from the effective value.
function stepMyRepeat(delta: number): void {
  const p = prefs.value
  if (!p || prefsSaving.value) return
  const next = p.repeat_count_max_effective + delta
  if (next < p.repeat_count_min || next > p.repeat_count_system_max) return
  void savePreferences({ repeat_count_max: next }, { ...p, repeat_count_max: next, repeat_count_max_effective: next })
}

// Back to "not set": the system value applies again.
function resetMyRepeat(): void {
  const p = prefs.value
  if (!p || prefsSaving.value || p.repeat_count_max === null) return
  void savePreferences(
    { repeat_count_max: null },
    { ...p, repeat_count_max: null, repeat_count_max_effective: p.repeat_count_system_max },
  )
}

function chooseMyTitleProvider(value: string): void {
  const p = prefs.value
  if (!p || prefsSaving.value || value === p.title_provider || value === TITLE_SEPARATOR) return
  void savePreferences({ title_provider: value }, { ...p, title_provider: value })
}

// Saved as soon as it is chosen; a failed save puts the previous value back.
async function chooseTitleProvider(value: string): Promise<void> {
  const previous = titleProvider.value
  if (previous === null || titleSaving.value || value === previous || value === TITLE_SEPARATOR) return
  titleProvider.value = value
  titleSaving.value = true
  try {
    await saveTitleProvider(value)
  } catch (err: unknown) {
    if (disposed) return
    titleProvider.value = previous
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'))
  } finally {
    titleSaving.value = false
  }
}

// No save button: the system ceiling is written shortly after the last −/＋ click.
function stepRepeat(delta: number): void {
  if (!execPolicy.value) return
  const next = repeatMax.value + delta
  if (next < execPolicy.value.repeat_count_min || next > execPolicy.value.repeat_count_hard_max) return
  repeatMax.value = next
  if (repeatTimer) clearTimeout(repeatTimer)
  repeatTimer = setTimeout(() => void saveRepeat(), 600)
}

async function saveRepeat(): Promise<void> {
  repeatTimer = null
  const current = execPolicy.value
  if (!current || repeatMax.value === current.repeat_count_max) return
  const value = repeatMax.value
  try {
    await saveRepeatCountMax(value)
    if (disposed) return
    execPolicy.value = { ...current, repeat_count_max: value }
    // My column is capped by the ceiling: read it again so its limit and effective value follow.
    void loadPreferences()
  } catch (err: unknown) {
    if (disposed) return
    repeatMax.value = current.repeat_count_max
    pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed'))
  }
}

onBeforeUnmount(() => {
  // A pending −/＋ change is still written when the tab or window closes.
  if (repeatTimer) {
    clearTimeout(repeatTimer)
    void saveRepeat()
  }
  disposed = true
})

// Every AI once, in my order: the saved ids that still exist, then the rest (system order, then mine) — the AI list's order.
const orderedItems = computed<ProviderItem[]>(() => {
  const byId = new Map<string, ProviderItem>()
  for (const item of systemList.value) byId.set(item.id, item)
  for (const item of myList.value) if (!byId.has(item.id)) byId.set(item.id, item)
  const ids = [...order.value.filter((id) => byId.has(id)), ...byId.keys()]
  return [...new Set(ids)].map((id) => byId.get(id)!)
})

// Fixed choices / separator / the AI in the list's order. Off AI are left out (the server refuses them), except the
// stored one, which is always shown so the dropdown never claims another value.
const TITLE_SEPARATOR = '__separator__'
function titleOptions(current: string | null, fixed: SelectOption[]): SelectOption[] {
  const items = orderedItems.value.filter((p) => p.enabled || p.id === current)
  const ai: SelectOption[] = items.map((p) => ({ value: p.id, label: p.name }))
  if (current && !fixed.some((option) => option.value === current) && !items.some((p) => p.id === current)) {
    ai.push({ value: current, label: current })
  }
  return ai.length ? [...fixed, { value: TITLE_SEPARATOR, label: '──────────', disabled: true }, ...ai] : fixed
}

// System: 끄기 / 실행한 AI 그대로 / the AI.
const titleProviderOptions = computed<SelectOption[]>(() => titleOptions(titleProvider.value, [
  { value: 'off', label: t('gui.ai.providers.title_ai_off') },
  { value: 'executor', label: t('gui.ai.providers.title_ai_executor') },
]))

// Mine: 자동 / 끄기 / 답한 AI 그대로 / the AI I can use (system AI shown to me and my own AI).
const myTitleProviderOptions = computed<SelectOption[]>(() => titleOptions(prefs.value?.title_provider ?? null, [
  { value: 'auto', label: t('gui.ai.providers.title_ai_auto') },
  { value: 'off', label: t('gui.ai.providers.title_ai_off') },
  { value: 'executor', label: t('gui.ai.providers.title_ai_answered') },
]))

void load()
</script>

<style scoped src="../providers/aiCards.css"></style>

<style scoped>
.ai-other-settings { container-type: inline-size; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.panel-state { display: flex; align-items: center; justify-content: center; min-height: 160px; }
.os-table { display: flex; flex-direction: column; border: 1px solid var(--fg2-line); border-radius: 10px; background: #0d1b2b; overflow: hidden; }
.os-row { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(170px, 260px) minmax(170px, 260px); align-items: center; gap: 12px 16px; padding: 12px 18px; }
.os-row + .os-row { border-top: 1px solid var(--fg2-line); }
.os-head { padding-top: 8px; padding-bottom: 8px; background: color-mix(in srgb, var(--fg2-text, #fff) 4%, transparent); }
.os-col-title { font-size: 11.5px; font-weight: 700; color: var(--fg2-muted); }
.os-cell { display: flex; align-items: center; min-width: 0; }
.os-cell .repeat-stepper { flex-wrap: wrap; }
.os-cell .title-ai-card { width: 100%; }
/* Two classes: outranks the shared Select's own width: 100% and aiCards' fixed card width. */
.os-cell .title-ai-select { width: 100%; max-width: 100%; }
.os-readonly { font-size: 14px; color: #eaf1f8; padding: 0 6px; }
.os-missing { font-size: 12px; color: var(--fg2-muted); }
.os-note { margin: 0 2px; font-size: 11.5px; color: var(--fg2-muted); }
/* A narrow window: each value goes under its name with the column title as a small label. */
@container (max-width: 560px) {
  .os-head { display: none; }
  .os-row { grid-template-columns: 1fr; gap: 8px; }
  .os-cell { gap: 10px; }
  .os-cell::before { content: attr(data-label); flex: none; width: 92px; font-size: 11px; color: var(--fg2-muted); }
}
</style>
