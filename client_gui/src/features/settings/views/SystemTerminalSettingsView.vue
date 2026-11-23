<template>
  <SettingsShell scope="system">
    <template #nav>
      <SettingsSubnav :items="systemNav" active="terminal" />
    </template>

    <section class="fgs-panel" data-panel="system-terminal">
      <div class="fgs-phead">
        <span class="fgs-tico"><SettingsIcon name="code" size="lg" /></span>
        <div class="fgs-ptitle">
          <h2>{{ w.title }}</h2>
          <p>{{ w.sub }}</p>
        </div>
      </div>

      <div class="fgs-panel-body">
        <div v-if="loading && !system" class="fgs-rows"><div class="fgs-row"><LoadingState inline size="sm" :label="t('gui.common.loading')" /></div></div>
        <div v-else-if="!system" class="fgs-rows">
          <div class="fgs-row">
            <ErrorState compact :title="w.loadFailed" :message="loadError ?? undefined" data-testid="terminal-load-error">
              <template #action>
                <Button variant="secondary" data-testid="terminal-retry" @click="load">{{ w.retry }}</Button>
              </template>
            </ErrorState>
          </div>
        </div>
        <template v-else>
          <!-- Status and the [사용] switch (dimmed "설치 후 사용" until installed). -->
          <div class="fgs-sec" data-card="terminal-status">
            <div class="fgs-sec-head"><h3>{{ w.status }}</h3></div>
            <div class="fgs-rows">
              <div class="fgs-row">
                <div class="fgs-row-label">
                  <span class="fgs-state" :class="chipClass" data-testid="terminal-state" :data-state="state">{{ w.state[state] }}</span>
                  <small class="fgs-muted" data-testid="terminal-platform">{{ system.platform }}</small>
                </div>
                <div class="fgs-row-ctrl">
                  <label class="fgs-toggle" :class="{ 'tx-dim': !system.installed }" :title="system.installed ? '' : w.needsInstall">
                    <input
                      type="checkbox"
                      role="switch"
                      :checked="system.enabled"
                      :disabled="!system.installed || busy || state === 'installing'"
                      :aria-label="w.use"
                      data-testid="terminal-switch"
                      @change="onSwitch($event)"
                    />
                    <span class="fgs-track"></span>
                  </label>
                  <span class="fgs-muted" data-testid="terminal-switch-label">{{ system.installed ? (system.enabled ? w.on : w.off) : w.needsInstall }}</span>
                </div>
              </div>
              <div v-if="confirmOff" class="fgs-row sub" data-testid="terminal-off-confirm">
                <span class="fgs-hint"><SettingsIcon name="alert" size="sm" />{{ w.offConfirm(system.open_sessions) }}</span>
                <span class="tx-actions">
                  <button type="button" class="fgs-btn sm ghost" data-testid="terminal-off-cancel" @click="confirmOff = false">{{ w.cancel }}</button>
                  <button type="button" class="fgs-btn sm danger" data-testid="terminal-off-confirm-yes" :disabled="busy" @click="switchTo(false)">{{ w.turnOff }}</button>
                </span>
              </div>
              <div v-if="notice" class="fgs-row sub"><span class="fgs-hint" role="status" data-testid="terminal-notice">{{ notice }}</span></div>
              <div v-if="actionError" class="fgs-row sub">
                <span class="fgs-error" role="alert" data-testid="terminal-action-error">{{ actionError }}</span>
              </div>
            </div>
          </div>

          <!-- Install: [설치] · progress · stages · [취소]; an error with [다시 설치] (and the offline line). -->
          <div class="fgs-sec" data-card="terminal-install">
            <div class="fgs-sec-head">
              <h3>{{ w.bundle }}</h3>
              <div v-if="system.bundle" class="meta" data-testid="terminal-bundle">{{ system.bundle.name }} {{ system.bundle.version }} · {{ formatSize(system.bundle.size_bytes) }}</div>
            </div>
            <div class="fgs-rows">
              <div v-if="!system.bundle_required" class="fgs-row"><span class="fgs-muted" data-testid="terminal-no-bundle">{{ w.noBundle }}</span></div>
              <template v-else>
                <div v-if="install && state === 'installing'" class="fgs-row tx-apply" data-testid="terminal-install-progress">
                  <div class="tx-grow">
                    <div v-if="install.bytes_total" class="tx-bytes" data-testid="terminal-install-bytes">{{ w.progress(formatSize(install.bytes_done), formatSize(install.bytes_total)) }}</div>
                    <div class="tx-prog"><i :style="{ width: `${percent}%` }" data-testid="terminal-install-bar"></i></div>
                    <div class="tx-steps" data-testid="terminal-install-steps">
                      <span v-for="(stage, index) in INSTALL_STAGES" :key="stage" :class="{ done: index < currentStage, now: index === currentStage }" :data-stage="stage">
                        {{ index < currentStage ? '✓ ' : '' }}{{ w.steps[stage] }}
                      </span>
                    </div>
                  </div>
                  <button type="button" class="fgs-btn sm ghost" :disabled="cancelling" data-testid="terminal-install-cancel" @click="cancel">{{ w.cancel }}</button>
                </div>
                <div v-if="state === 'installing'" class="fgs-row sub">
                  <span class="fgs-hint"><SettingsIcon name="info" size="sm" />{{ w.installingNote }}</span>
                </div>
                <template v-if="state === 'error'">
                  <div class="fgs-row sub" data-testid="terminal-install-error">
                    <span class="fgs-error" role="alert">{{ installErrorText }}</span>
                  </div>
                  <div v-if="offline" class="fgs-row sub" data-testid="terminal-offline-hint">
                    <span class="fgs-hint"><SettingsIcon name="info" size="sm" />{{ w.offlineHint }}</span>
                  </div>
                </template>
                <div v-if="state === 'not_installed' && install?.status === 'cancelled'" class="fgs-row sub">
                  <span class="fgs-muted" data-testid="terminal-install-cancelled">{{ w.cancelled }}</span>
                </div>
                <div v-if="state === 'not_installed' || state === 'error'" class="fgs-row">
                  <span class="fgs-muted">{{ w.state[state] }}</span>
                  <span class="tx-actions">
                    <button type="button" class="fgs-btn sm pri" :disabled="busy" data-testid="terminal-install" @click="startInstall">
                      {{ state === 'error' ? w.reinstall : w.install }}
                    </button>
                  </span>
                </div>
              </template>

              <!-- Installed: [셸 시험] · [삭제] (삭제 only where there is a component to delete). -->
              <div v-if="system.installed" class="fgs-row" data-testid="terminal-installed-actions">
                <span v-if="testing" class="fgs-muted">{{ w.testing }}</span>
                <span v-else-if="testResult && testResult.ok" class="tx-ok" data-testid="terminal-test-ok">{{ w.testOk(testResult.shell) }}</span>
                <span v-else-if="testResult && !testResult.ok" class="fgs-error" role="alert" data-testid="terminal-test-error">{{ testResult.message || errorText(testResult.error_code) }}</span>
                <span v-else class="fgs-muted">{{ w.state.installed }}</span>
                <span class="tx-actions">
                  <button type="button" class="fgs-btn sm" :disabled="testing || busy" data-testid="terminal-test" @click="runTest">{{ w.test }}</button>
                  <button v-if="system.bundle_required" type="button" class="fgs-btn sm danger" :disabled="busy || system.enabled"
                    :title="system.enabled ? w.removeNeedsOff : ''" data-testid="terminal-remove" @click="confirmRemove = true">{{ w.remove }}</button>
                </span>
              </div>
              <div v-if="confirmRemove" class="fgs-row sub" data-testid="terminal-remove-confirm">
                <span class="fgs-hint"><SettingsIcon name="alert" size="sm" />{{ w.removeConfirm }}</span>
                <span class="tx-actions">
                  <button type="button" class="fgs-btn sm ghost" @click="confirmRemove = false">{{ w.cancel }}</button>
                  <button type="button" class="fgs-btn sm danger" :disabled="busy" data-testid="terminal-remove-yes" @click="remove">{{ w.remove }}</button>
                </span>
              </div>
            </div>
          </div>
        </template>
      </div>
    </section>
  </SettingsShell>
</template>

<script setup lang="ts">
// 시스템 › 실행 자원 › 웹 터미널 (0078 · 0015-T B): install the Windows shell component (pywinpty) the same way as the
// voice models — [설치], progress, 받기 → 확인(SHA-256) → 풀기 → 시험 → 적용, [취소] — then [셸 시험] / [삭제] and the
// [사용] switch, the only thing that turns the Designer terminal on (no environment variable). Linux / macOS servers
// need nothing (bundle_required=false) and are installed from the start. Turning it off with terminals open asks once
// ("열려 있는 터미널 N개가 바로 닫힙니다"). Contract: 0015-T 서버·화면 공통 약속 2~7 (GET/PUT /terminal/system…).
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import {
  cancelTerminalInstall, deleteTerminalBundle, readTerminalInstall, readTerminalSystem, setTerminalEnabled, startTerminalInstall,
  terminalErrorCode, testTerminalShell, type TerminalInstall, type TerminalShellTest, type TerminalSystem,
} from '@/features/designer/flowTerminal/api'
import SettingsIcon from '../SettingsIcon.vue'
import SettingsShell from '../components/SettingsShell.vue'
import SettingsSubnav from '../components/SettingsSubnav.vue'
import { useSystemNav } from '../components/systemNav'
import {
  INSTALL_STAGES, formatSize, installPercent, isInstallRunning, isOfflineFailure, stageIndex, terminalSystemState,
} from '../terminal/systemTerminalModel'
import { systemTerminalWordsFor } from '../terminal/systemTerminalWords'

/** How often a running installation is read again. */
const POLL_MS = 1000

const { t, locale } = useI18n()
const w = computed(() => systemTerminalWordsFor(locale.value))
const systemNav = useSystemNav()

const system = ref<TerminalSystem | null>(null)
const loading = ref(false)
const loadError = ref<string | null>(null)
const busy = ref(false)
const cancelling = ref(false)
const actionError = ref<string | null>(null)
const notice = ref<string | null>(null)
const confirmOff = ref(false)
const confirmRemove = ref(false)
const testing = ref(false)
const testResult = ref<TerminalShellTest | null>(null)
let alive = true

const install = computed(() => system.value?.install ?? null)
const state = computed(() => (system.value ? terminalSystemState(system.value) : 'not_installed'))
const chipClass = computed(() => ({ not_installed: 'off', installing: 'info', installed: system.value?.enabled ? 'ok' : 'warn', error: 'bad' })[state.value])
const currentStage = computed(() => stageIndex(install.value))
const percent = computed(() => installPercent(install.value))
const offline = computed(() => isOfflineFailure(install.value))

function errorText(code: string | null | undefined): string {
  return (code && w.value.errors[code]) || w.value.failed
}
const installErrorText = computed(() => {
  const last = install.value
  if (!last) return w.value.failed
  const known = last.error_code ? w.value.errors[last.error_code] : ''
  return [known, last.message].filter(Boolean).join(' · ') || w.value.failed
})

async function guarded(work: () => Promise<void>): Promise<void> {
  actionError.value = null
  busy.value = true
  try { await work() } catch (error) { actionError.value = errorText(terminalErrorCode(error)) } finally { busy.value = false }
}

/* ── reading ── */
async function load(): Promise<void> {
  loading.value = true
  loadError.value = null
  try {
    const next = await readTerminalSystem()
    if (!alive) return
    system.value = next
    schedulePoll()
  } catch (error) {
    if (alive) loadError.value = terminalErrorCode(error)
  } finally {
    loading.value = false
  }
}

let pollTimer: ReturnType<typeof setTimeout> | null = null
function stopPoll(): void {
  if (pollTimer) clearTimeout(pollTimer)
  pollTimer = null
}
/** While an installation runs it is read again every second; when it ends the whole setting is read again. */
function schedulePoll(): void {
  stopPoll()
  const running = install.value
  if (!alive || !isInstallRunning(running)) return
  pollTimer = setTimeout(() => { void poll(running!.install_id) }, POLL_MS)
}
async function poll(installId: string): Promise<void> {
  pollTimer = null
  try {
    const next = await readTerminalInstall(installId)
    if (!alive || !system.value) return
    applyInstall(next)
    if (isInstallRunning(next)) schedulePoll()
    else await load()
  } catch {
    if (alive) await load()
  }
}
function applyInstall(next: TerminalInstall): void {
  if (system.value) system.value = { ...system.value, install: next }
}

/* ── install / cancel ── */
function startInstall(): Promise<void> {
  notice.value = null
  testResult.value = null
  return guarded(async () => {
    try {
      applyInstall(await startTerminalInstall())
    } catch (error) {
      // Already running (another administrator / tab): show that installation.
      if (terminalErrorCode(error) === 'terminal_install_running') { await load(); return }
      throw error
    }
    schedulePoll()
  })
}
async function cancel(): Promise<void> {
  const running = install.value
  if (!running) return
  cancelling.value = true
  try {
    await guarded(async () => { await cancelTerminalInstall(running.install_id) })
    await load()
  } finally {
    cancelling.value = false
  }
}

/* ── the [사용] switch ── */
function onSwitch(event: Event): void {
  const input = event.target as HTMLInputElement
  const current = system.value
  if (!current) return
  if (input.checked) { confirmOff.value = false; void switchTo(true); return }
  // Turning off with terminals open closes them at once: ask once first (the switch stays on until confirmed).
  if (current.open_sessions >= 1) {
    input.checked = true
    confirmOff.value = true
    return
  }
  void switchTo(false)
}
async function switchTo(enabled: boolean): Promise<void> {
  confirmOff.value = false
  notice.value = null
  await guarded(async () => {
    const result = await setTerminalEnabled(enabled)
    if (system.value) system.value = { ...system.value, enabled: result.enabled, open_sessions: result.enabled ? system.value.open_sessions : 0 }
    if (!result.enabled && result.closed_sessions > 0) notice.value = w.value.closedSessions(result.closed_sessions)
  })
  if (actionError.value) await load() // the switch shows what the server has
}

/* ── test / delete ── */
async function runTest(): Promise<void> {
  testing.value = true
  testResult.value = null
  try {
    testResult.value = await testTerminalShell()
  } catch (error) {
    const code = terminalErrorCode(error) ?? 'failed'
    testResult.value = { ok: false, error_code: code, message: errorText(code) }
  } finally {
    testing.value = false
  }
}
async function remove(): Promise<void> {
  confirmRemove.value = false
  notice.value = null
  testResult.value = null
  await guarded(async () => {
    await deleteTerminalBundle()
    notice.value = w.value.removed
  })
  await load()
}

onMounted(() => { void load() })
onBeforeUnmount(() => {
  alive = false
  stopPoll()
})
</script>

<style scoped>
.tx-apply {
  gap: 10px;
}

.tx-grow {
  flex: 1;
  min-width: 0;
}

.tx-bytes {
  font-size: 12px;
  color: #90a7bf;
}

.tx-prog {
  height: 6px;
  margin-top: 6px;
  border-radius: 3px;
  background: #22384f;
  overflow: hidden;
}

.tx-prog i {
  display: block;
  height: 100%;
  background: #2b9af5;
  transition: width 0.3s;
}

.tx-steps {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 8px;
  font-size: 12px;
  color: #6f86a0;
}

.tx-steps .done {
  color: #74e1c5;
}

.tx-steps .now {
  color: #d6e8fb;
  font-weight: 700;
}

.tx-actions {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
}

.tx-dim {
  opacity: 0.45;
}

.tx-ok {
  color: #74e1c5;
}
</style>
