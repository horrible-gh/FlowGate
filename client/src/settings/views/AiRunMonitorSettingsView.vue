<template>
  <section class="ai-run-monitor-settings">
    <div class="heading">
      <h1>{{ t('settings.ai_run_monitor.title') }}</h1>
      <p>{{ t('settings.ai_run_monitor.description') }}</p>
    </div>

    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <div class="field">
      <label class="field-label" for="ai-finished-card-retention">
        {{ t('settings.ai_run_monitor.retention.label') }}
      </label>
      <p class="field-hint" id="ai-finished-card-retention-hint">
        {{ t('settings.ai_run_monitor.retention.hint') }}
      </p>
      <div class="field-row">
        <select
          id="ai-finished-card-retention"
          aria-describedby="ai-finished-card-retention-hint"
          :disabled="loading || saving"
          v-model.number="selected"
        >
          <option v-for="minutes in choices" :key="minutes" :value="minutes">
            {{ optionLabel(minutes) }}
          </option>
        </select>
        <button type="button" class="primary" :disabled="loading || saving" @click="save">
          {{ saving ? t('settings.ai_run_monitor.retention.saving') : t('settings.ai_run_monitor.retention.save') }}
        </button>
        <span v-if="loading" class="state">{{ t('settings.ai_run_monitor.retention.loading') }}</span>
        <span v-else-if="saved" class="state ok" role="status">
          {{ t('settings.ai_run_monitor.retention.saved') }}
        </span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
// 0452 T0005 section 3, renamed after the TR0006 review: the screen is named after what
// it configures — the AI run monitor in the header — not after the sidebar group it sits
// in. The nine choices are NOT written here: the screen draws whatever
// order the GET envelope ships (L0003 section 2-8), so the day the list changes there is
// one place to change it and this view follows. The mirror is written only after the
// server has accepted the value — a tab watching the storage event must never see a
// setting that failed to save (L0003 section 2-5).
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, patchRequest } from '@shared/api'
import {
  RETENTION_DEFAULT_MINUTES,
  RETENTION_FIELD,
  RETENTION_IMMEDIATE,
  RETENTION_NEVER,
  UI_SETTINGS_PATH,
  normalizeRetentionMinutes,
  retentionDomainFrom,
  retentionFromResponse,
  writeRetentionMirror,
  type UiSettingsResponse,
} from '@shared/aiFinishedCardRetention'

const { t } = useI18n()
const choices = ref<number[]>([])
const selected = ref<number>(RETENTION_DEFAULT_MINUTES)
const loading = ref(true)
const saving = ref(false)
const saved = ref(false)
const error = ref('')

function optionLabel(minutes: number): string {
  if (minutes === RETENTION_NEVER) return t('settings.ai_run_monitor.retention.never')
  if (minutes === RETENTION_IMMEDIATE) return t('settings.ai_run_monitor.retention.immediate')
  if (minutes % 60 !== 0) return t('settings.ai_run_monitor.retention.minutes', { minutes })
  const hours = minutes / 60
  return hours === 1
    ? t('settings.ai_run_monitor.retention.hour', { hours })
    : t('settings.ai_run_monitor.retention.hours', { hours })
}

function adopt(body: UiSettingsResponse | undefined): void {
  choices.value = retentionDomainFrom(body)
  // Re-normalized rather than trusted: a value the server repaired, or one from a build
  // that does not know this field, must not become the selection silently.
  selected.value = normalizeRetentionMinutes(retentionFromResponse(body))
}

const load = async () => {
  loading.value = true
  error.value = ''
  try {
    adopt((await getRequest<UiSettingsResponse>(UI_SETTINGS_PATH)).data)
  } catch {
    // The list still has to be drawable, so fall back to the shared domain and say why.
    choices.value = retentionDomainFrom(null)
    error.value = t('settings.ai_run_monitor.retention.load_failed')
  } finally {
    loading.value = false
  }
}

const save = async () => {
  saving.value = true
  saved.value = false
  error.value = ''
  const requested = selected.value
  try {
    const { data } = await patchRequest<UiSettingsResponse>(UI_SETTINGS_PATH, {
      [RETENTION_FIELD]: requested,
    })
    adopt(data)
    // Only now, and with the value the server answered with, not the one that was sent.
    writeRetentionMirror(selected.value)
    saved.value = true
  } catch {
    // Neither the selection nor the mirror is confirmed on failure: the open monitor tab
    // keeps applying the setting that is actually stored.
    error.value = t('settings.ai_run_monitor.retention.save_failed')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.heading h1{margin:0 0 6px}.heading p{margin:0 0 20px;color:#777}
.field{padding:16px;border:1px solid var(--border-color,#ddd);border-radius:8px;max-width:640px}
.field-label{display:block;font-weight:600}
.field-hint{margin:6px 0 12px;color:#777}
.field-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
select{padding:6px 8px;border:1px solid var(--border-color,#ddd);border-radius:6px;min-width:180px}
.state{color:#777}.state.ok{color:#1769aa}.error{color:#b42318}
</style>
