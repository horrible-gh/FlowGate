<template>
  <!-- AI tab of the ⚙ settings window (flowgate.v02test.0096 ④): this chat's AI, the 📌 pins of 자주 쓰는 AI (the same
       pins the AI picker and AI 관리 use, card ①'s composable) and my own 최대 반복 횟수 / 제목 짓기 AI with the system
       values beside them (the existing /me/ai-preferences API). Every change here is saved at once.
       F96-8: the same one-line rows as the 대화 / 음성 tabs (label left, value right, the window's --csx-* sizes); the
       system value sits next to the stepper and the title AI in effect inside the 자동 choice, not on extra lines. -->
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

    <div class="cas-row is-top">
      <span class="cas-label">{{ t('gui.conversation.settings.ai_pinned') }}</span>
      <div class="cas-value">
        <ul v-if="pinnedRows.length" class="cas-pins" data-test="conv-setting-pins">
          <li v-for="row in pinnedRows" :key="row.id" class="cas-pin" :data-provider-id="row.id">
            <span aria-hidden="true">📌</span>
            <span class="cas-pin-name" :title="row.name">{{ row.name }}</span>
            <button
              type="button"
              class="cas-x"
              data-test="conv-setting-pin-remove"
              :aria-label="t('gui.conversation.settings.ai_pin_remove', { name: row.name })"
              :title="t('gui.conversation.settings.ai_pin_remove', { name: row.name })"
              @click="favorites.setPinned(row.id, false)"
            >×</button>
          </li>
        </ul>
        <select
          v-if="unpinned.length"
          class="cas-select"
          data-test="conv-setting-pin-add"
          value=""
          :aria-label="t('gui.conversation.settings.ai_pin_add')"
          @change="addPin($event)"
        >
          <option value="" disabled>＋ {{ t('gui.conversation.settings.ai_pin_add') }}</option>
          <option v-for="p in unpinned" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <span v-else-if="!pinnedRows.length" class="cas-ref" data-test="conv-setting-pins-empty">{{ t('gui.conversation.settings.ai_pinned_empty') }}</span>
      </div>
    </div>

    <div v-if="prefsState === 'loading'" class="cas-note">{{ t('gui.conversation.settings.ai_prefs_loading') }}</div>
    <div v-else-if="prefsState === 'error'" class="cas-note is-error" role="alert" data-test="conv-setting-prefs-error">
      {{ t('gui.conversation.settings.ai_prefs_failed') }}
      <button type="button" class="cas-link" @click="loadPrefs">{{ t('gui.conversation.retry') }}</button>
    </div>
    <template v-else-if="prefs">
      <div class="cas-row">
        <span class="cas-label">{{ t('gui.conversation.settings.ai_repeat') }}</span>
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
        <span class="cas-label">{{ t('gui.conversation.settings.ai_title') }}</span>
        <select
          class="cas-select"
          data-test="conv-setting-title-ai"
          :value="prefs.title_provider"
          :title="t('gui.conversation.settings.ai_title_effective', { name: effectiveTitleName })"
          :disabled="saving"
          @change="chooseTitle(($event.target as HTMLSelectElement).value)"
        >
          <option v-for="o in titleOptions" :key="o.value" :value="o.value" :disabled="o.disabled">{{ o.label }}</option>
        </select>
      </label>
    </template>
    <div v-if="saveError" class="cas-note is-error" role="alert" data-test="conv-setting-prefs-save-error">{{ saveError }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useCurrentUser } from '@/core/auth/currentUser'
import { useAiFavorites } from '@shared/aiFavorites'
import { titleChoices, useMyAiPreferences, type TitleChoice } from '@shared/myAiPreferences'
import { fetchMyAiPrefs, saveMyAiPrefs } from './api'
import type { ChatProvider, MyAiPrefs } from './types'

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
// F96-5: the same load / save logic as AI 관리 › 기타 설정 (내 값), from the shared composable.
const saveError = ref('')
const control = useMyAiPreferences<MyAiPrefs>({
  fetch: fetchMyAiPrefs,
  save: saveMyAiPrefs,
  onSaveError: (err) => {
    saveError.value = err instanceof Error && err.message ? err.message : t('gui.conversation.settings.ai_prefs_save_failed')
  },
})
const { prefs, state: prefsState, saving, load: loadPrefs } = control

// Each new change starts without the previous failure line.
const clearingError = <A extends unknown[]>(fn: (...args: A) => void) => (...args: A): void => {
  saveError.value = ''
  fn(...args)
}
const stepRepeat = clearingError(control.stepRepeat)
const resetRepeat = clearingError(control.resetRepeat)
const chooseTitle = clearingError(control.chooseTitle)

const fixedTitle = computed<TitleChoice[]>(() => [
  { value: 'auto', label: t('gui.conversation.settings.ai_title_auto') },
  { value: 'off', label: t('gui.conversation.settings.ai_title_off') },
  { value: 'executor', label: t('gui.conversation.settings.ai_title_executor') },
])

// 자동 / 끄기 / 답한 AI 그대로 / separator / the AI this chat can use. While 자동 is chosen, the AI it resolves to is
// named inside that choice ("자동 · 답한 AI 그대로") instead of on a line of its own. F96-10: an administrator value
// whose name is not known here leaves just "자동" (never the internal ID).
const titleOptions = computed<TitleChoice[]>(() => {
  const p = prefs.value
  const resolved = p?.title_provider === 'auto' ? knownTitleName(p.title_provider_effective || '') : ''
  const autoInUse = resolved && resolved !== titleName('auto')
  const fixed = fixedTitle.value.map((o) => (o.value === 'auto' && autoInUse ? { ...o, label: `${o.label} · ${resolved}` } : o))
  return titleChoices(prefs.value?.title_provider, fixed, props.providers.map((p) => ({ value: p.id, label: p.name })))
})

function titleName(value: string): string {
  return fixedTitle.value.find((o) => o.value === value)?.label ?? nameOf(value)
}

/** The display name of a title choice, or '' when neither a fixed choice nor an AI of this chat has that id. */
function knownTitleName(value: string): string {
  return fixedTitle.value.find((o) => o.value === value)?.label ?? props.providers.find((p) => p.id === value)?.name ?? ''
}

const effectiveTitleName = computed(() => {
  const p = prefs.value
  if (!p) return ''
  return knownTitleName(p.title_provider_effective || p.title_provider) || titleName('auto')
})

onMounted(() => { void loadPrefs() })
onBeforeUnmount(control.dispose)
</script>

<style scoped>
/* F96-8: the row shape shared with the 대화 / 음성 tabs (sizes from the window's --csx-* variables). */
.cas {
  display: grid;
  gap: var(--csx-row-gap, 8px);
}

.cas-row {
  display: grid;
  grid-template-columns: var(--csx-label-width, 88px) minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  min-height: var(--csx-row-height, 28px);
}

.cas-row.is-top {
  align-items: start;
}

.cas-row.is-top > .cas-label {
  line-height: var(--csx-control-height, 26px);
}

.cas-label {
  color: var(--fg2-text);
  font-weight: 600;
  overflow-wrap: anywhere;
}

.cas-value {
  display: grid;
  gap: 4px;
  min-width: 0;
}

.cas-ref {
  color: var(--fg2-muted);
  font-weight: 400;
  font-size: 10.5px;
}

.cas-note {
  color: var(--fg2-muted);
}

.cas-note.is-error {
  color: var(--fg2-status-danger-fg, #d33);
}

.cas-select {
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  height: var(--csx-control-height, 26px);
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-bg);
  color: var(--fg2-text);
  font: inherit;
  padding: 0 5px;
}

.cas-pins {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.cas-pin {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  box-sizing: border-box;
  min-height: 22px;
  padding: 1px 2px 1px 6px;
  border: 1px solid var(--fg2-line);
  border-radius: var(--fg2-control-radius-sm);
  color: var(--fg2-text);
}

/* Long AI names wrap instead of being cut. */
.cas-pin-name {
  min-width: 0;
  overflow-wrap: anywhere;
}

.cas-x {
  width: 18px;
  height: 18px;
  padding: 0;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  cursor: pointer;
}

.cas-x:hover {
  color: var(--fg2-text);
  background: var(--fg2-control-surface);
}

.cas-stepper {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  color: var(--fg2-text);
}

.cas-stepper > b {
  min-width: 14px;
  text-align: center;
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
