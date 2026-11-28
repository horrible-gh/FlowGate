<template>
  <section class="ai-other-settings" data-test="ai-other-settings">
    <div v-if="loading && !loaded" class="panel-state">
      <LoadingState :label="t('gui.common.loading')" />
    </div>
    <div v-else class="os-table" role="table" :aria-label="t('gui.aiManage.tabs.other')">
      <!-- 최대 반복 횟수: my own value (/me/ai-preferences), 1~30 (F96-11: no system ceiling). -->
      <div class="os-row" role="row" data-test="repeat-row">
        <div class="os-name repeat-info" role="rowheader">
          <b>{{ t('gui.ai.providers.repeat_max') }}</b>
          <small>{{ t('gui.aiManage.other.repeat_hint') }}</small>
        </div>
        <div class="os-cell" role="cell">
          <div v-if="prefs" class="repeat-stepper" data-test="my-repeat-card">
            <Button
              v-if="prefs.repeat_count_max !== null"
              variant="secondary"
              size="sm"
              :disabled="prefsSaving"
              data-test="my-repeat-reset"
              @click="resetMyRepeat"
            >
              {{ t('gui.ai.providers.my_repeat_reset') }}
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
              :disabled="prefsSaving || prefs.repeat_count_max_effective >= prefs.repeat_count_hard_max"
              data-test="my-repeat-increase"
              @click="stepMyRepeat(1)"
            >
              <span aria-hidden="true">＋</span>
            </IconButton>
          </div>
          <span v-else class="os-missing">{{ t('gui.aiManage.other.unavailable') }}</span>
        </div>
      </div>

      <!-- 제목 짓기 AI: my own choice. -->
      <div class="os-row" role="row" data-test="title-row">
        <div class="os-name repeat-info" role="rowheader">
          <b>{{ t('gui.ai.providers.title_ai') }}</b>
          <small>{{ t('gui.aiManage.other.title_hint') }}</small>
        </div>
        <div class="os-cell" role="cell">
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

    <ToastHost :toasts="toasts" :dismiss-label="t('gui.common.close')" placement="top" @dismiss="dismissToast" />
  </section>
</template>

<script setup lang="ts">
// v02test.0096 F96-3 "기타 설정": the repeat limit and title AI cards that sat under the AI list, as one table.
// F96-6: only my own values (내 값) stay here, one column. F96-13: the title AI is a personal setting only (the
// administrator's 환경 설정 no longer has it). F96-11: the max repeat count is a personal setting only, 1~30, with no system ceiling.
// Each value is saved as soon as it changes and a failed save puts the previous value back (v02test.0089).
import { useAiProvidersChanged } from '@shared/aiProvidersChanged'
import { computed, onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import IconButton from '@/components/ui/IconButton.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import Select, { type SelectOption } from '@/components/ui/Select.vue'
import ToastHost, { type ToastHostItem } from '@/components/ui/ToastHost.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import { TITLE_EXECUTOR, titleChoices, useMyAiPreferences } from '@shared/myAiPreferences'
import {
  fetchMyAi,
  fetchMyAiOrder,
  fetchMyAiPreferences,
  fetchSystemAi,
  fetchVisibleSystemAi,
  saveMyAiPreferences,
} from '../api'
import '../aiBaseSelect.css'
import type { MyAiPreferences, ProviderItem } from '../types'
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
const toasts = ref<ToastHostItem[]>([])
let disposed = false

function pushToast(message: string): void {
  toasts.value = [...toasts.value, { id: `ai-other-${Date.now()}-${Math.random()}`, message, tone: 'danger', duration: 3500 }]
}

function dismissToast(id: string | number): void {
  toasts.value = toasts.value.filter((item) => item.id !== id)
}

// Every read stands alone: the AI lists only feed the title AI choices; a failed one leaves fewer choices.
let loadRequest = 0
useAiProvidersChanged(() => { void load() })
async function load(): Promise<void> {
  const request = ++loadRequest
  loading.value = true
  const [system, mine, myOrder] = await Promise.allSettled([
    isAdmin.value ? fetchSystemAi() : fetchVisibleSystemAi(),
    fetchMyAi(),
    fetchMyAiOrder(),
    loadPreferences(),
  ])
  if (disposed || request !== loadRequest) return
  if (system.status === 'fulfilled') systemList.value = system.value.providers ?? []
  if (mine.status === 'fulfilled') myList.value = mine.value.providers ?? []
  if (myOrder.status === 'fulfilled') order.value = myOrder.value.order ?? []
  loading.value = false
  loaded.value = true
}

// My own values (내 값): F96-5 moved their load / save into the composable the ⚙ settings window's AI tab uses as well.
// A failed read leaves each row saying so (prefs stays null).
const myPrefs = useMyAiPreferences<MyAiPreferences>({
  fetch: fetchMyAiPreferences,
  save: saveMyAiPreferences,
  onSaveError: (err) => pushToast(err instanceof Error ? err.message : t('gui.ai.providers.save_failed')),
})
const { prefs, saving: prefsSaving, load: loadPreferences } = myPrefs
const stepMyRepeat = myPrefs.stepRepeat
const resetMyRepeat = myPrefs.resetRepeat
const chooseMyTitleProvider = myPrefs.chooseTitle

onBeforeUnmount(() => {
  disposed = true
  myPrefs.dispose()
})

// Every AI once, in my order: the saved ids that still exist, then the rest (system order, then mine) — the AI list's order.
const orderedItems = computed<ProviderItem[]>(() => {
  const byId = new Map<string, ProviderItem>()
  for (const item of systemList.value) byId.set(item.id, item)
  for (const item of myList.value) if (!byId.has(item.id)) byId.set(item.id, item)
  const ids = [...order.value.filter((id) => byId.has(id)), ...byId.keys()]
  return [...new Set(ids)].map((id) => byId.get(id)!)
})

// 답한 AI 그대로 (the default) / 끄기 / separator / the AI I can use (system AI shown to me and my own AI) in the list's
// order. F96-13: no 자동 — a stored 'auto' arrives as 'executor' from the composable. Off AI are left out (the server
// refuses them), except the stored one, which titleChoices always offers so the dropdown never claims another value.
const myTitleProviderOptions = computed<SelectOption[]>(() => {
  const current = prefs.value?.title_provider ?? null
  const ai = orderedItems.value.filter((p) => p.enabled || p.id === current).map((p) => ({ value: p.id, label: p.name }))
  return titleChoices(current, [
    { value: TITLE_EXECUTOR, label: t('gui.ai.providers.title_ai_answered') },
    { value: 'off', label: t('gui.ai.providers.title_ai_off') },
  ], ai)
})

void load()
</script>

<style scoped src="../providers/aiCards.css"></style>

<style scoped>
.ai-other-settings { container-type: inline-size; display: flex; flex-direction: column; gap: 10px; min-height: 0; }
.panel-state { display: flex; align-items: center; justify-content: center; min-height: 160px; }
.os-table { display: flex; flex-direction: column; border: 1px solid var(--fg2-line); border-radius: 10px; background: #0d1b2b; overflow: hidden; }
.os-row { display: grid; grid-template-columns: minmax(150px, 1fr) minmax(170px, 260px); align-items: center; gap: 12px 16px; padding: 12px 18px; }
.os-row + .os-row { border-top: 1px solid var(--fg2-line); }
.os-cell { display: flex; align-items: center; min-width: 0; }
.os-cell .repeat-stepper { flex-wrap: wrap; }
.os-cell .title-ai-card { width: 100%; }
/* Two classes: outranks the shared Select's own width: 100% and aiCards' fixed card width. */
.os-cell .title-ai-select { width: 100%; max-width: 100%; }
.os-missing { font-size: 12px; color: var(--fg2-muted); }
/* A narrow window: each value goes under its name. */
@container (max-width: 560px) {
  .os-row { grid-template-columns: 1fr; gap: 8px; }
}
</style>
