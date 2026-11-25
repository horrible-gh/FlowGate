<template>
  <!-- Voice section of the chat ⚙ popover: 녹음 후 (text / voice), 서비스 ▼, 모델 ▼ and 실시간. These are this person's own
       settings (/me/ui-settings) and are saved as soon as they change; the chat settings' 저장 does not cover them.
       An administrator can install a model right here (the same installation API as 시스템 › 음성 입력). -->
  <div class="cvs" data-test="conv-voice-settings">
    <div class="cvs-title">{{ t('gui.voice.settings.title') }}</div>

    <div v-if="!state.loaded && !state.loadError" class="cvs-note">{{ t('gui.voice.settings.loading') }}</div>
    <div v-else-if="!state.loaded" class="cvs-note is-error" role="alert">
      {{ t('gui.voice.settings.load_failed') }}
      <button type="button" class="cvs-link" @click="loadVoice(true)">{{ t('gui.voice.settings.retry') }}</button>
    </div>
    <div v-else-if="adminOff" class="cvs-note" data-test="conv-voice-admin-off">{{ t('gui.voice.settings.admin_off') }}</div>
    <div v-else-if="services.length === 0" class="cvs-note" data-test="conv-voice-none">{{ t('gui.voice.settings.none_installed') }}</div>
    <template v-else>
      <div class="cvs-row">
        <span>{{ t('gui.voice.settings.after') }}</span>
        <span class="cvs-seg" role="group" :aria-label="t('gui.voice.settings.after')">
          <button type="button" :class="{ on: state.prefs.action === 'text' }" :aria-pressed="state.prefs.action === 'text'" data-test="conv-voice-action-text" :disabled="state.busy" @click="save({ action: 'text' })">{{ t('gui.voice.settings.as_text') }}</button>
          <button type="button" :class="{ on: state.prefs.action === 'audio' }" :aria-pressed="state.prefs.action === 'audio'" data-test="conv-voice-action-audio" :disabled="state.busy" @click="save({ action: 'audio' })">{{ t('gui.voice.settings.as_audio') }}</button>
        </span>
      </div>

      <div class="cvs-row">
        <span>{{ t('gui.voice.settings.service') }}</span>
        <button type="button" class="cvs-dd" :class="{ open: openList === 'svc' }" :aria-expanded="openList === 'svc'" data-test="conv-voice-service" @click="toggleList('svc')">
          <span class="cvs-dd-name">{{ browsingServiceInfo?.name ?? '—' }}</span>
          <span aria-hidden="true">{{ openList === 'svc' ? '▲' : '▼' }}</span>
        </button>
      </div>
      <div v-if="openList === 'svc'" class="cvs-list" role="listbox" data-test="conv-voice-service-list">
        <button
          v-for="service in services"
          :key="service.id"
          type="button"
          role="option"
          class="cvs-opt"
          :class="{ sel: service.id === browsingService }"
          :aria-selected="service.id === browsingService"
          :data-service="service.id"
          @click="pickService(service.id)"
        >
          <span class="cvs-ck">{{ service.id === browsingService ? '✓' : '' }}</span>
          <span class="cvs-name">{{ service.name }}<span v-if="service.models.some((m) => m.realtime)" class="cvs-tag rt">{{ t('gui.voice.settings.realtime_tag') }}</span></span>
          <span v-if="!service.installed" class="cvs-dim">{{ t('gui.voice.settings.not_installed') }}</span>
        </button>
      </div>

      <div class="cvs-row">
        <span>{{ t('gui.voice.settings.model') }}</span>
        <button type="button" class="cvs-dd" :class="{ open: openList === 'mdl', need: browsingPending }" :aria-expanded="openList === 'mdl'" data-test="conv-voice-model" @click="toggleList('mdl')">
          <span class="cvs-dd-name">{{ browsingPending ? t('gui.voice.settings.pick_model') : currentModelLabel }}</span>
          <span v-if="!browsingPending && model?.realtime" class="cvs-tag rt">{{ t('gui.voice.settings.realtime_tag') }}</span>
          <span aria-hidden="true">{{ openList === 'mdl' ? '▲' : '▼' }}</span>
        </button>
      </div>
      <div v-if="openList === 'mdl'" class="cvs-list" role="listbox" data-test="conv-voice-model-list">
        <div
          v-for="item in browsingModels"
          :key="item.id"
          role="option"
          class="cvs-opt"
          :class="{ sel: isCurrent(item.id), no: !item.installed }"
          :aria-selected="isCurrent(item.id)"
          :aria-disabled="!item.installed"
          :data-model="item.id"
          :tabindex="item.installed ? 0 : -1"
          @click="item.installed && pickModel(item.id)"
          @keydown.enter.prevent="item.installed && pickModel(item.id)"
        >
          <span class="cvs-ck">{{ isCurrent(item.id) ? '✓' : '' }}</span>
          <span class="cvs-name">
            {{ item.name }}<span v-if="item.realtime" class="cvs-tag rt">{{ t('gui.voice.settings.realtime_tag') }}</span><span v-if="item.isDefault" class="cvs-tag">{{ t('gui.voice.settings.default_tag') }}</span>
          </span>
          <span class="cvs-r">
            <template v-if="installingThis(item.id)">
              <span class="cvs-prog"><i :style="{ width: `${progressPercent}%` }"></i></span>
              <span class="cvs-pct">{{ progressPercent }}%</span>
              <button type="button" class="cvs-can" data-test="conv-voice-install-cancel" @click.stop="cancelInstall()">{{ t('gui.voice.mic.cancel') }}</button>
            </template>
            <span v-else-if="!item.installed && !item.available" class="cvs-dim" :title="voiceReasonText(item.unavailableReason, t, te)">{{ t('gui.voice.system.unavailable') }}</span>
            <button
              v-else-if="!item.installed && state.status?.canManage"
              type="button"
              class="cvs-ins"
              :data-test="`conv-voice-install-${item.id}`"
              :disabled="!!activeInstall || state.busy"
              @click.stop="install(item.id)"
            >{{ t('gui.voice.settings.install') }} {{ formatBytes(item.sizeBytes) }}</button>
          </span>
        </div>
      </div>
      <!-- F86: no notice per model any more; one line per engine under the list opens that engine's license tab. -->
      <div v-if="browsingServiceInfo" class="cvs-lic" data-test="conv-voice-license">
        <LicenseInfoLink :tab="browsingServiceInfo.id">{{ t('gui.licenses.open_for', { name: browsingServiceInfo.name }) }}</LicenseInfoLink>
      </div>
      <div v-if="browsingPending && model" class="cvs-note">{{ t('gui.voice.settings.keep_using', { model: currentModelLabel }) }}</div>

      <div v-if="state.prefs.action === 'text'" class="cvs-row">
        <span>{{ t('gui.voice.settings.realtime') }}</span>
        <span class="cvs-rt">
          <span v-if="!model?.realtime" class="cvs-dim">{{ t('gui.voice.settings.realtime_unsupported') }}</span>
          <label class="cvs-switch" :class="{ dis: !model?.realtime }">
            <input
              type="checkbox"
              role="switch"
              data-test="conv-voice-realtime"
              :checked="!!model?.realtime && state.prefs.realtime"
              :disabled="!model?.realtime || state.busy"
              :aria-label="t('gui.voice.settings.realtime')"
              @change="save({ realtime: ($event.target as HTMLInputElement).checked })"
            />
            <span class="cvs-track"></span>
          </label>
        </span>
      </div>

      <div v-if="state.prefs.service" class="cvs-row cvs-sub">
        <span></span>
        <button type="button" class="cvs-link" data-test="conv-voice-use-default" :disabled="state.busy" @click="save({ service: '', model: '', realtime: false })">{{ t('gui.voice.settings.use_default') }}</button>
      </div>
    </template>

    <div v-if="justInstalled" class="cvs-note is-info" role="status">{{ t('gui.voice.settings.installed_now', { model: justInstalled }) }}</div>
    <div v-if="errorCode" class="cvs-note is-error" role="alert" data-test="conv-voice-settings-error">{{ t(voiceErrorKey(errorCode, (key) => te(key))) }}</div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import type { VoicePrefs } from './voice/api'
import { LicenseInfoLink } from '@/features/licenses'
import { formatBytes, voiceErrorKey, voiceReasonText } from './voice/messages'
import { errorCodeOf } from './voice/useVoiceInput'
import { cancelInstall, findService, installModel, loadVoice, sameSelection, updatePrefs, useVoice } from './voice/voiceStore'

const { t, te } = useI18n()
const { state, selection, model } = useVoice()

const openList = ref<'svc' | 'mdl' | null>(null)
/** The service whose models are listed; differs from the one in use while an uninstalled service is browsed. */
const browsingService = ref('')
const errorCode = ref<string | null>(null)
const justInstalled = ref<string | null>(null)

onMounted(() => { void loadVoice(true) })

const services = computed(() => state.catalog.filter((s) => state.status?.canManage || s.models.some((m) => m.installed)))
const adminOff = computed(() => {
  const s = state.status
  return !!s && !s.canManage && (s.state === 'off' || s.state === 'error' || s.state === 'not_installed')
})
const activeInstall = computed(() => (state.install && !state.install.done ? state.install : null))

watch(
  () => [selection.value?.service, services.value.length] as const,
  () => {
    if (!browsingService.value || !services.value.some((s) => s.id === browsingService.value)) {
      browsingService.value = selection.value?.service ?? services.value[0]?.id ?? ''
    }
  },
  { immediate: true },
)

const browsingServiceInfo = computed(() => findService(state.catalog, browsingService.value))
const browsingModels = computed(() => (browsingServiceInfo.value?.models ?? []).filter((m) => state.status?.canManage || m.installed))
const browsingPending = computed(() => browsingService.value !== '' && browsingService.value !== selection.value?.service)
const currentModelLabel = computed(() => model.value?.name ?? selection.value?.model ?? '—')
const isCurrent = (modelId: string) => !browsingPending.value && sameSelection(selection.value, { service: browsingService.value, model: modelId })
const installingThis = (modelId: string) => activeInstall.value?.service === browsingService.value && activeInstall.value?.model === modelId

const progressPercent = computed(() => {
  const job = activeInstall.value
  if (!job) return 0
  if (job.step === 'extracting' || job.step === 'testing' || job.step === 'complete') return 100
  return job.totalBytes > 0 ? Math.min(100, Math.floor((job.receivedBytes / job.totalBytes) * 100)) : 0
})

function toggleList(which: 'svc' | 'mdl'): void {
  openList.value = openList.value === which ? null : which
  if (!openList.value && browsingPending.value && !activeInstall.value) browsingService.value = selection.value?.service ?? browsingService.value
}

async function save(patch: Partial<VoicePrefs>): Promise<void> {
  errorCode.value = null
  justInstalled.value = null
  try {
    await updatePrefs(patch)
  } catch (error) {
    errorCode.value = errorCodeOf(error)
  }
}

function pickService(serviceId: string): void {
  if (serviceId === selection.value?.service) {
    browsingService.value = serviceId
    openList.value = null
    return
  }
  browsingService.value = serviceId
  const installed = findService(state.catalog, serviceId)?.models.find((m) => m.installed)
  if (installed) {
    openList.value = null
    void save({ service: serviceId, model: installed.id, realtime: installed.realtime })
  } else {
    openList.value = 'mdl'
  }
}

function pickModel(modelId: string): void {
  const picked = findService(state.catalog, browsingService.value)?.models.find((m) => m.id === modelId)
  if (!picked?.installed) return
  openList.value = null
  void save({ service: browsingService.value, model: modelId, realtime: picked.realtime })
}

async function install(modelId: string): Promise<void> {
  errorCode.value = null
  justInstalled.value = null
  const target = { service: browsingService.value, model: modelId }
  try {
    await installModel(target, 'personal')
  } catch (error) {
    errorCode.value = errorCodeOf(error)
  }
}

// An installation started here ends: show the outcome next to the list.
watch(
  () => [state.install?.installId ?? null, state.installError, state.lastInstalled] as const,
  ([, installError, lastInstalled], [, , previous]) => {
    if (installError) errorCode.value = installError
    if (lastInstalled && lastInstalled !== previous) {
      const info = findService(state.catalog, lastInstalled.service)?.models.find((m) => m.id === lastInstalled.model)
      justInstalled.value = info?.name ?? lastInstalled.model
      browsingService.value = lastInstalled.service
    }
  },
)
</script>

<style scoped>
.cvs {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--fg2-line);
  font-size: 11.5px;
}

.cvs-title {
  margin-bottom: 6px;
  font-weight: 700;
  color: var(--fg2-text);
}

.cvs-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  color: var(--fg2-muted);
}

.cvs-row > :last-child {
  justify-self: start;
}

.cvs-seg {
  display: inline-flex;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  overflow: hidden;
}

.cvs-seg button {
  border: 0;
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  padding: 3px 12px;
  cursor: pointer;
}

.cvs-seg button.on {
  background: var(--fg2-status-info-bg);
  color: var(--fg2-status-info-fg);
}

.cvs-dd {
  width: 100%;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-text);
  font: inherit;
  padding: 4px 8px;
  cursor: pointer;
  text-align: left;
}

.cvs-row > .cvs-dd {
  justify-self: stretch;
}

.cvs-dd.need .cvs-dd-name {
  color: var(--fg2-status-warning-fg, var(--fg2-status-info-fg));
}

.cvs-dd-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cvs-list {
  max-height: 180px;
  overflow: auto;
  margin: -2px 0 6px 72px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-panel);
}

.cvs-opt {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: var(--fg2-text);
  font: inherit;
  padding: 5px 8px;
  text-align: left;
  cursor: pointer;
  box-sizing: border-box;
}

.cvs-opt:hover,
.cvs-opt:focus-visible {
  background: var(--fg2-control-surface);
}

.cvs-opt.sel {
  color: var(--fg2-status-info-fg);
}

.cvs-opt.no {
  cursor: default;
  color: var(--fg2-muted);
}

.cvs-ck {
  width: 12px;
  flex: none;
}

.cvs-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.cvs-r {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  flex: none;
}

.cvs-tag {
  margin-left: 5px;
  padding: 0 5px;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  font-size: 10px;
  color: var(--fg2-muted);
}

.cvs-tag.rt {
  border-color: var(--fg2-blue);
  color: var(--fg2-status-info-fg);
}

.cvs-dim {
  color: var(--fg2-muted);
  font-size: 10.5px;
}

.cvs-ins,
.cvs-can {
  border: 1px solid var(--fg2-line-strong);
  border-radius: var(--fg2-control-radius-sm);
  background: var(--fg2-control-surface);
  color: var(--fg2-status-info-fg);
  font: inherit;
  font-size: 10.5px;
  padding: 1px 7px;
  cursor: pointer;
}

.cvs-ins:disabled {
  opacity: var(--fg2-control-disabled-opacity);
  cursor: not-allowed;
}

.cvs-prog {
  width: 54px;
  height: 5px;
  border-radius: 3px;
  background: var(--fg2-line);
  overflow: hidden;
}

.cvs-prog i {
  display: block;
  height: 100%;
  background: var(--fg2-blue);
}

.cvs-pct {
  font-variant-numeric: tabular-nums;
  font-size: 10.5px;
}

.cvs-rt {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.cvs-switch {
  position: relative;
  display: inline-block;
  width: 30px;
  height: 16px;
}

.cvs-switch input {
  position: absolute;
  inset: 0;
  margin: 0;
  opacity: 0;
  cursor: pointer;
}

.cvs-switch input:disabled {
  cursor: not-allowed;
}

.cvs-track {
  position: absolute;
  inset: 0;
  border: 1px solid var(--fg2-line-strong);
  border-radius: 8px;
  background: var(--fg2-control-surface);
  pointer-events: none;
}

.cvs-track::after {
  content: '';
  position: absolute;
  top: 2px;
  left: 2px;
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--fg2-muted);
  transition: transform 0.15s;
}

.cvs-switch input:checked + .cvs-track {
  border-color: var(--fg2-blue);
  background: var(--fg2-status-info-bg);
}

.cvs-switch input:checked + .cvs-track::after {
  transform: translateX(14px);
  background: var(--fg2-status-info-fg);
}

.cvs-switch input:focus-visible + .cvs-track {
  outline: 2px solid var(--fg2-focus);
  outline-offset: 1px;
}

.cvs-switch.dis {
  opacity: var(--fg2-control-disabled-opacity);
}

.cvs-note {
  margin: 2px 0 6px;
  color: var(--fg2-muted);
}

.cvs-note.is-error {
  color: var(--fg2-status-danger-fg);
}

.cvs-note.is-info {
  color: var(--fg2-status-info-fg);
}

.cvs-lic {
  padding: 2px 0 4px;
}

.cvs-link {
  border: 0;
  background: transparent;
  color: var(--fg2-status-info-fg);
  font: inherit;
  padding: 0;
  cursor: pointer;
}

.cvs-sub {
  margin-top: -2px;
}
</style>
