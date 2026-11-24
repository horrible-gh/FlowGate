<template>
  <SettingsShell scope="system">
    <template #nav>
      <SettingsSubnav :items="systemNav" active="voice" />
    </template>

    <section class="fgs-panel" data-panel="system-voice">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="mic" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ t('gui.voice.system.title') }}</h2>
          <p>{{ t('gui.voice.system.sub') }}</p>
        </div>
      </div>

      <div class="fgs-panel-body">
        <div v-if="!state.loaded && !state.loadError" class="fgs-rows"><div class="fgs-row"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div></div>
        <div v-else-if="!state.loaded || !status" class="fgs-rows">
          <div class="fgs-row">
            <ErrorState compact :title="t('gui.voice.system.load_failed')" :message="errorText(state.loadError)" data-testid="voice-load-error">
              <template #action>
                <Button variant="secondary" @click="loadVoice(true)">{{ t('gui.voice.system.retry') }}</Button>
              </template>
            </ErrorState>
          </div>
        </div>
        <template v-else>
          <!-- Status: state, the default model, on/off (switched on once installed and tested), restart on error. -->
          <div class="fgs-sec" data-card="voice-status">
            <div class="fgs-sec-head"><h3>{{ t('gui.voice.system.status') }}</h3></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <span class="fgs-state" :class="chipClass" data-testid="voice-state">{{ chipText }}</span>
                  <small v-if="defaultLabel" data-testid="voice-default">{{ defaultLabel }}</small>
                </div>
                <div class="fgs-row-ctrl">
                  <button v-if="status.state === 'error'" type="button" class="fgs-btn sm" :disabled="state.busy" data-testid="voice-restart" @click="restart">
                    {{ restarting ? t('gui.voice.system.restarting') : t('gui.voice.system.restart') }}
                  </button>
                  <label class="fgs-toggle" :title="status.defaultSelection ? '' : t('gui.voice.system.switch_hint')">
                    <input
                      type="checkbox"
                      role="switch"
                      :checked="switchedOn"
                      :disabled="!status.defaultSelection || state.busy || installing"
                      :aria-label="t('gui.voice.system.title')"
                      data-testid="voice-switch"
                      @change="onSwitch($event)"
                    />
                    <span class="fgs-track"></span>
                  </label>
                  <span class="fgs-muted">{{ switchedOn ? t('gui.voice.system.switch_on') : t('gui.voice.system.switch_off') }}</span>
                </div>
              </div>
              <div v-if="confirmOff" class="fgs-row sub" data-testid="voice-off-confirm">
                <span class="fgs-hint"><SettingsIcon name="alert" size="sm" />{{ t('gui.voice.system.off_confirm') }}</span>
                <span class="vx-actions">
                  <button type="button" class="fgs-btn sm ghost" @click="confirmOff = false">{{ t('gui.voice.system.cancel') }}</button>
                  <button type="button" class="fgs-btn sm danger" data-testid="voice-off-confirm-yes" :disabled="state.busy" @click="turnOff">{{ t('gui.voice.system.turn_off') }}</button>
                </span>
              </div>
              <div v-if="status.error" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="voice-status-error">{{ errorText(status.error.code) }}</span>
              </div>
              <div v-if="installing" class="fgs-row sub">
                <span class="fgs-hint"><SettingsIcon name="info" size="sm" />{{ t('gui.voice.system.installing_note') }}</span>
              </div>
              <div v-if="actionError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="voice-action-error">{{ errorText(actionError) }}</span>
              </div>
              <div v-for="item in status.retired" :key="`${item.service}/${item.model}`" class="fgs-row sub" data-testid="voice-retired">
                <span class="fgs-hint"><SettingsIcon name="info" size="sm" />{{ t('gui.voice.system.retired', { name: item.name }) }}</span>
              </div>
            </div>
          </div>

          <!-- Service · model: pick, then install (the service's program comes with its first model) or make default. -->
          <div class="fgs-sec" data-card="voice-choose">
            <div class="fgs-sec-head"><h3>{{ t('gui.voice.system.choose') }}</h3></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label"><label for="voice-service">{{ t('gui.voice.system.service') }}</label></div>
                <div class="fgs-row-ctrl">
                  <select id="voice-service" v-model="selService" class="fgs-input vx-select" :disabled="installing" data-testid="voice-service" @change="onServiceChange">
                    <option v-for="service in state.catalog" :key="service.id" :value="service.id">{{ serviceOption(service) }}</option>
                  </select>
                </div>
              </div>
              <div class="fgs-row">
                <div class="fgs-row-label"><label for="voice-model">{{ t('gui.voice.system.model') }}</label></div>
                <div class="fgs-row-ctrl">
                  <select id="voice-model" v-model="selModel" class="fgs-input vx-select" :disabled="installing" data-testid="voice-model">
                    <option v-for="item in selServiceInfo?.models ?? []" :key="item.id" :value="item.id">{{ modelOption(item) }}</option>
                  </select>
                </div>
              </div>
              <div v-if="selModelInfo && !selModelInfo.available" class="fgs-row sub">
                <span class="fgs-error" data-testid="voice-unavailable">{{ t('gui.voice.system.unavailable') }}<template v-if="selModelInfo.unavailableReason"> · {{ selModelInfo.unavailableReason }}</template></span>
              </div>

              <div v-if="selServiceInfo && selModelInfo" class="fgs-row sub">
                <VoiceLicenseNotice :service="selServiceInfo" :model="selModelInfo" />
              </div>
              <!-- Running installation -->
              <div v-if="job" class="fgs-row vx-apply" data-testid="voice-install-progress">
                <div class="vx-grow">
                  <strong>{{ jobLabel }}</strong>
                  <div v-if="job.step === 'downloading' || job.step === 'queued'" class="vx-bytes">
                    {{ t('gui.voice.system.progress', { received: formatBytes(job.receivedBytes), total: formatBytes(job.totalBytes) }) }}
                  </div>
                  <div class="vx-prog"><i :style="{ width: `${jobPercent}%` }"></i></div>
                  <div class="vx-steps">
                    <span v-for="(step, index) in STEPS" :key="step" :class="{ done: index < stepIndex, now: index === stepIndex }">
                      {{ index < stepIndex ? '✓ ' : '' }}{{ t(`gui.voice.system.step.${step}`) }}
                    </span>
                  </div>
                </div>
                <button type="button" class="fgs-btn sm ghost" :disabled="job.step === 'cancelling'" data-testid="voice-install-cancel" @click="cancel">
                  {{ job.step === 'cancelling' ? t('gui.voice.system.step.cancelling') : t('gui.voice.system.cancel') }}
                </button>
              </div>
              <!-- Apply the choice -->
              <div v-else-if="selModelInfo && !isDefaultChoice" class="fgs-row vx-apply" data-testid="voice-apply">
                <div class="vx-grow">
                  <template v-if="defaultLabel">{{ defaultLabel }} → </template><strong>{{ choiceLabel }}</strong>
                  <span v-if="!selModelInfo.installed" class="fgs-muted"> · {{ formatBytes(selModelInfo.sizeBytes) }}</span>
                </div>
                <button v-if="status.defaultSelection" type="button" class="fgs-btn sm ghost" data-testid="voice-revert" @click="revert">{{ t('gui.voice.system.revert') }}</button>
                <button
                  type="button"
                  class="fgs-btn sm pri"
                  :disabled="state.busy || !selModelInfo.available || (selModelInfo.installed && selModelInfo.defaultEligible === false)"
                  data-testid="voice-apply-go"
                  @click="apply"
                >{{ applyText }}</button>
              </div>
              <div v-if="state.installError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="voice-install-error">{{ errorText(state.installError) }}</span>
                <button type="button" class="fgs-link" @click="dismissInstallError">✕</button>
              </div>
            </div>
          </div>

          <!-- Test: record and transcribe with the chosen installed model (works while switched off). -->
          <div class="fgs-sec" data-card="voice-test">
            <div class="fgs-sec-head"><h3>{{ t('gui.voice.system.test') }}</h3><div class="meta">{{ t('gui.voice.system.test_hint') }}</div></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <strong>{{ testTarget ? selectionLabel(testTarget) : '—' }}</strong>
                </div>
                <div class="fgs-row-ctrl">
                  <label>Upload your recording
                    <input type="file" accept="audio/*" :disabled="!testTarget || testPhase !== 'idle'" data-testid="voice-test-upload" @change="uploadTest" />
                  </label>
                  <span v-if="testPhase === 'recording'" class="vx-rec">● {{ formatClock(testSeconds) }}</span>
                  <button
                    type="button"
                    class="fgs-btn sm"
                    :class="{ danger: testPhase === 'recording' }"
                    :disabled="!testTarget || testPhase === 'processing' || testPhase === 'starting'"
                    data-testid="voice-test-record"
                    @click="toggleTest"
                  >{{ testPhase === 'recording' ? t('gui.voice.system.test_stop') : t('gui.voice.system.test_record') }}</button>
                </div>
              </div>
              <div class="fgs-row sub" data-testid="voice-test-out">
                <span v-if="!testTarget" class="fgs-muted">{{ t('gui.voice.system.test_needs_install') }}</span>
                <span v-else-if="testPhase === 'processing'" class="fgs-muted">{{ t('gui.voice.system.test_running') }}</span>
                <span v-else-if="testError" class="fgs-error" role="alert">{{ errorText(testError) }}</span>
                <span v-else-if="testResult" class="vx-result">“{{ testResult.text || t('gui.voice.system.test_empty') }}” <span class="fgs-muted">{{ t('gui.voice.system.elapsed', { s: (testResult.elapsedMs / 1000).toFixed(1) }) }}</span></span>
              </div>
            </div>
          </div>

          <!-- Installed models -->
          <div class="fgs-sec" data-card="voice-installed">
            <div class="fgs-sec-head"><h3>{{ t('gui.voice.system.installed') }}</h3><div v-if="installedRows.length" class="meta">{{ t('gui.voice.system.total', { size: formatBytes(installedTotal) }) }}</div></div>
            <div class="fgs-rows">
              <div v-if="removedNotice" class="fgs-row sub"><span class="fgs-hint" role="status" data-testid="voice-removed">{{ removedNotice }}</span></div>
              <div v-if="installedRows.length === 0" class="fgs-row"><span class="fgs-muted">{{ t('gui.voice.system.none') }}</span></div>
              <div v-else class="fgs-table-wrap">
                <table class="fgs-t" data-testid="voice-installed-table">
                  <thead>
                    <tr>
                      <th>{{ t('gui.voice.system.col_service') }}</th>
                      <th>{{ t('gui.voice.system.col_model') }}</th>
                      <th class="r">{{ t('gui.voice.system.col_size') }}</th>
                      <th>{{ t('gui.voice.system.col_state') }}</th>
                      <th class="r"></th>
                    </tr>
                  </thead>
                  <tbody>
                    <template v-for="row in installedRows" :key="`${row.service.id}/${row.model.id}`">
                      <tr :data-model="`${row.service.id}/${row.model.id}`">
                        <td>{{ row.service.name }}</td>
                        <td><strong>{{ row.model.name }}</strong><span v-if="row.model.realtime" class="fgs-badge teal vx-tag">{{ t('gui.voice.system.realtime_tag') }}</span></td>
                        <td class="r">{{ formatBytes(row.model.sizeBytes) }}</td>
                        <td><span v-if="row.model.isDefault" class="fgs-badge purple">{{ t('gui.voice.system.default_tag') }}</span></td>
                        <td class="r">
                          <button type="button" class="fgs-btn sm danger" :disabled="state.busy || installing" data-testid="voice-remove" @click="askRemove(row.service.id, row.model.id)">{{ t('gui.voice.system.remove') }}</button>
                        </td>
                      </tr>
                      <tr v-if="removing && removing.service === row.service.id && removing.model === row.model.id" data-testid="voice-remove-confirm">
                        <td colspan="5">
                          <div class="vx-confirm">
                            <span>
                              {{ t('gui.voice.system.remove_confirm', { model: `${row.service.name} · ${row.model.name}` }) }}
                              <template v-if="row.service.models.filter((m) => m.installed).length === 1"> {{ t('gui.voice.system.remove_last') }}</template>
                              <template v-if="row.model.isDefault"> {{ t('gui.voice.system.remove_default') }}</template>
                            </span>
                            <span class="vx-actions">
                              <button type="button" class="fgs-btn sm ghost" @click="removing = null">{{ t('gui.voice.system.cancel') }}</button>
                              <button type="button" class="fgs-btn sm danger" :disabled="state.busy" data-testid="voice-remove-yes" @click="remove">{{ t('gui.voice.system.remove') }}</button>
                            </span>
                          </div>
                        </td>
                      </tr>
                    </template>
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </template>
      </div>
    </section>
  </SettingsShell>
</template>

<script setup lang="ts">
// 시스템 › 음성 입력: install, choose the default, switch on/off, restart, test and delete the voice models every
// user's chat microphone transcribes with. Contract: server/VOICE_API.md (administrator routes). The chat ⚙ popover
// uses the same store and installation API.
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import { testTranscribe, type VoiceModel, type VoiceSelection, type VoiceService, type VoiceTranscription } from '@/features/conversation/voice/api'
import { formatBytes, installVoiceMessages, voiceErrorKey } from '@/features/conversation/voice/messages'
import { startRecording, type RecordingHandle } from '@/features/conversation/voice/recorder'
import { errorCodeOf, formatClock } from '@/features/conversation/voice/useVoiceInput'
import {
  cancelInstall,
  chooseDefault,
  deleteModel,
  dismissInstallError,
  findModel,
  findService,
  installModel,
  loadVoice,
  restartVoiceModule,
  sameSelection,
  switchVoice,
  useVoice,
} from '@/features/conversation/voice/voiceStore'
import VoiceLicenseNotice from '@/features/conversation/voice/VoiceLicenseNotice.vue'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSystemNav } from '../components/systemNav'

const STEPS = ['queued', 'downloading', 'extracting', 'testing', 'complete'] as const

const composer = useI18n({ useScope: 'global' })
installVoiceMessages(composer)
const { t, te } = composer
const systemNav = useSystemNav()
const { state, installedModels } = useVoice()

const status = computed(() => state.status)
const installing = computed(() => !!state.install && !state.install.done)
const job = computed(() => (installing.value ? state.install : null))
// `enabled` is true only while switched on AND the voice module answers; switching on again also restarts it.
const switchedOn = computed(() => status.value?.enabled === true)

const errorText = (code: string | null | undefined) => t(voiceErrorKey(code, (key) => te(key)))

function selectionLabel(selection: VoiceSelection): string {
  const service = findService(state.catalog, selection.service)
  const model = findModel(state.catalog, selection)
  return `${service?.name ?? selection.service} · ${model?.name ?? selection.model}`
}

const defaultLabel = computed(() => (status.value?.defaultSelection ? selectionLabel(status.value.defaultSelection) : ''))

const chipClass = computed(() => ({ not_installed: 'off', installing: 'info', off: 'warn', on: 'ok', error: 'bad' })[installing.value ? 'installing' : status.value?.state ?? 'off'])
const chipText = computed(() => {
  const s = installing.value ? 'installing' : status.value?.state ?? 'off'
  return t(`gui.voice.system.state.${s}`)
})

// ── Choice ─────────────────────────────────────────────────────────────────────────────
const selService = ref('')
const selModel = ref('')
const selServiceInfo = computed(() => findService(state.catalog, selService.value))
const selModelInfo = computed(() => findModel(state.catalog, { service: selService.value, model: selModel.value }))
const isDefaultChoice = computed(() => sameSelection(status.value?.defaultSelection, { service: selService.value, model: selModel.value }))
const choiceLabel = computed(() => selectionLabel({ service: selService.value, model: selModel.value }))

/** The model to suggest for a service: an installed one, else the first usable one. */
function suggestedModel(service: VoiceService | null): string {
  if (!service) return ''
  const eligible = service.models.filter((m) => m.defaultEligible !== false)
  return (eligible.find((m) => m.installed) ?? eligible.find((m) => m.available) ?? eligible[0])?.id ?? ''
}

function revert(): void {
  const selection = status.value?.defaultSelection
  if (selection) {
    selService.value = selection.service
    selModel.value = selection.model
  } else {
    selService.value = state.catalog[0]?.id ?? ''
    selModel.value = suggestedModel(selServiceInfo.value)
  }
}

watch(() => [state.loaded, status.value?.defaultSelection?.service, status.value?.defaultSelection?.model] as const, () => {
  if (!state.loaded) return
  if (!selService.value || !selServiceInfo.value || isDefaultChoice.value || !selModelInfo.value) revert()
}, { immediate: true })

// While installing, the choice shows what is being installed.
watch(job, (current) => {
  if (current) {
    selService.value = current.service
    selModel.value = current.model
  }
}, { immediate: true })

function onServiceChange(): void {
  const selection = status.value?.defaultSelection
  selModel.value = selection && selection.service === selService.value ? selection.model : suggestedModel(selServiceInfo.value)
}

function serviceOption(service: VoiceService): string {
  const tags = [service.version ? `v${service.version}` : '', service.installed ? t('gui.voice.system.installed_tag') : '', service.available ? '' : t('gui.voice.system.unavailable')]
  return [service.name, ...tags.filter(Boolean)].join(' · ')
}

function modelOption(model: VoiceModel): string {
  const tags = [
    formatBytes(model.sizeBytes),
    model.isDefault ? t('gui.voice.system.default_tag') : model.installed ? t('gui.voice.system.installed_tag') : '',
    model.realtime ? t('gui.voice.system.realtime_tag') : '',
    model.available ? '' : t('gui.voice.system.unavailable'),
  ]
  return [model.name, ...tags.filter(Boolean)].join(' · ')
}

const applyText = computed(() => {
  if (selModelInfo.value?.defaultEligible === false) return selModelInfo.value.installed ? 'Default selection unavailable' : 'Download from upstream'
  if (!selModelInfo.value?.installed) return status.value?.defaultSelection ? t('gui.voice.system.install_switch') : t('gui.voice.system.install_enable')
  return t('gui.voice.system.make_default')
})

const actionError = ref<string | null>(null)
const restarting = ref(false)
const confirmOff = ref(false)

async function guarded(work: () => Promise<unknown>): Promise<void> {
  actionError.value = null
  try { await work() } catch (error) { actionError.value = errorCodeOf(error) }
}

async function apply(): Promise<void> {
  const selection = { service: selService.value, model: selModel.value }
  if (!selModelInfo.value) return
  removedNotice.value = null
  if (selModelInfo.value.installed && selModelInfo.value.defaultEligible === false) return
  if (selModelInfo.value.installed) await guarded(() => chooseDefault(selection))
  else await guarded(() => installModel(selection, status.value?.defaultSelection && selModelInfo.value?.defaultEligible !== false ? 'default' : null))
}

const cancel = () => guarded(() => cancelInstall())

const jobLabel = computed(() => (job.value ? selectionLabel({ service: job.value.service, model: job.value.model }) : ''))
const stepIndex = computed(() => {
  const step = job.value?.step ?? 'queued'
  const index = (STEPS as readonly string[]).indexOf(step)
  return index >= 0 ? index : 0
})
const jobPercent = computed(() => {
  const current = job.value
  if (!current) return 0
  if (stepIndex.value >= 2) return 100
  return current.totalBytes > 0 ? Math.min(100, Math.floor((current.receivedBytes / current.totalBytes) * 100)) : 0
})

// ── On / off, restart ──────────────────────────────────────────────────────────────────
function onSwitch(event: Event): void {
  const input = event.target as HTMLInputElement
  if (input.checked) {
    confirmOff.value = false
    void guarded(() => switchVoice(true))
  } else {
    // Turning off affects every user: ask first (the switch stays on until confirmed).
    input.checked = true
    confirmOff.value = true
  }
}

async function turnOff(): Promise<void> {
  confirmOff.value = false
  await guarded(() => switchVoice(false))
}

async function restart(): Promise<void> {
  restarting.value = true
  try { await guarded(() => restartVoiceModule()) } finally { restarting.value = false }
}

// ── Test ───────────────────────────────────────────────────────────────────────────────
type TestPhase = 'idle' | 'starting' | 'recording' | 'processing'
const testPhase = ref<TestPhase>('idle')
const testSeconds = ref(0)
const testResult = ref<VoiceTranscription | null>(null)
const testError = ref<string | null>(null)
let testRecording: RecordingHandle | null = null
let testTick: ReturnType<typeof setInterval> | null = null
let testGen = 0

/** The chosen model when installed, otherwise the default. */
const testTarget = computed<VoiceSelection | null>(() => {
  if (selModelInfo.value?.installed) return { service: selService.value, model: selModel.value }
  return status.value?.defaultSelection ?? null
})

function stopTestTimer(): void {
  if (testTick) clearInterval(testTick)
  testTick = null
}

async function uploadTest(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  const target = testTarget.value
  if (!file || !target || testPhase.value !== 'idle') return
  const gen = ++testGen
  testPhase.value = 'processing'
  testError.value = null
  testResult.value = null
  try {
    const result = await testTranscribe(file, file.name, target)
    if (gen === testGen) testResult.value = result
  } catch (error) {
    if (gen === testGen) testError.value = errorCodeOf(error)
  } finally {
    input.value = ''
    if (gen === testGen) testPhase.value = 'idle'
  }
}

async function toggleTest(): Promise<void> {
  if (testPhase.value === 'recording') {
    const target = testTarget.value
    const gen = testGen
    stopTestTimer()
    testPhase.value = 'processing'
    try {
      const audio = await testRecording!.stop()
      testRecording = null
      if (!target) throw new Error('no target')
      const result = await testTranscribe(audio.blob, audio.filename, target)
      if (gen === testGen) testResult.value = result
    } catch (error) {
      if (gen === testGen) testError.value = errorCodeOf(error)
    } finally {
      if (gen === testGen) testPhase.value = 'idle'
    }
    return
  }
  if (testPhase.value !== 'idle') return
  const gen = ++testGen
  testResult.value = null
  testError.value = null
  testSeconds.value = 0
  testPhase.value = 'starting'
  try {
    testRecording = await startRecording({ onLimit: () => { if (gen === testGen && testPhase.value === 'recording') void toggleTest() } })
    if (gen !== testGen) { testRecording.cancel(); return }
    testPhase.value = 'recording'
    testTick = setInterval(() => { testSeconds.value += 1 }, 1000)
  } catch (error) {
    testError.value = errorCodeOf(error)
    testPhase.value = 'idle'
  }
}

// ── Installed models ───────────────────────────────────────────────────────────────────
const installedRows = installedModels
const installedTotal = computed(() => installedRows.value.reduce((sum, row) => sum + row.model.sizeBytes, 0))
const removing = ref<VoiceSelection | null>(null)
const removedNotice = ref<string | null>(null)

function askRemove(service: string, model: string): void {
  removing.value = { service, model }
  removedNotice.value = null
}

async function remove(): Promise<void> {
  const target = removing.value
  if (!target) return
  const label = selectionLabel(target)
  removing.value = null
  await guarded(async () => {
    const result = await deleteModel(target)
    removedNotice.value = result.voiceDisabled ? t('gui.voice.system.removed_disabled') : t('gui.voice.system.removed', { model: label })
  })
}

onMounted(() => { void loadVoice(true) })

onBeforeUnmount(() => {
  testGen++
  stopTestTimer()
  testRecording?.cancel()
})
</script>

<style scoped>
.vx-select {
  min-width: 280px;
  max-width: 100%;
}

.vx-apply {
  gap: 10px;
}

.vx-grow {
  flex: 1;
  min-width: 0;
}

.vx-bytes {
  margin-top: 6px;
  font-size: 12px;
  color: #90a7bf;
}

.vx-prog {
  height: 6px;
  margin-top: 6px;
  border-radius: 3px;
  background: #22384f;
  overflow: hidden;
}

.vx-prog i {
  display: block;
  height: 100%;
  background: #2b9af5;
  transition: width 0.3s;
}

.vx-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
  color: #6f86a0;
}

.vx-steps .done {
  color: #74e1c5;
}

.vx-steps .now {
  color: #d6e8fb;
  font-weight: 700;
}

.vx-actions {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
}

.vx-confirm {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 12.5px;
}

.vx-rec {
  color: #fb7a80;
  font-variant-numeric: tabular-nums;
}

.vx-result {
  color: #d5e3f2;
}

.vx-tag {
  margin-left: 6px;
}
</style>
