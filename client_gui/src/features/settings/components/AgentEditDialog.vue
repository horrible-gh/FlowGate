<template>
  <ModalShell :open="true" surface-class="agent-settings-dialog" :aria-label="title" @close="emit('close')">
    <template #header="{ titleId }">
      <header class="agent-dialog-head">
        <span class="agent-dialog-symbol" aria-hidden="true"><SettingsIcon name="bot" /></span>
        <div class="agent-dialog-titles">
          <div class="agent-dialog-title">
            <h2 :id="titleId">{{ title }}</h2>
            <span v-if="agent && !creating" class="agent-chip" :class="tone" data-field="connection">{{ stateLabel }}</span>
          </div>
          <p class="agent-dialog-sub" data-testid="agent-status">
            <template v-if="creating">{{ t('gui.agent_capacity.create_sub') }}</template>
            <template v-else-if="agent?.location === 'local'">{{ t('gui.settings.agents.local_note') }}</template>
            <template v-else-if="connecting">{{ t('gui.agent_capacity.not_connected') }}</template>
            <template v-else-if="agent"><span data-field="platform">{{ platform }}</span> · {{ t('gui.agent_screen.last_seen') }} {{ seen }}</template>
          </p>
        </div>
        <Button variant="ghost" class="agent-dialog-close" :disabled="closeBusy" :aria-label="t('gui.common.close')" data-testid="agent-editor-close" @click="emit('close')"><SettingsIcon name="x" /></Button>
      </header>
    </template>
    <form class="agent-dialog-form" :data-testid="creating ? 'agent-create-form' : 'agent-detail'" :data-agent="agent?.agent_id" @submit.prevent="emit('submit')">
      <section v-if="agent && !creating && connecting" class="agent-sec" data-testid="agent-enrollment">
        <h3 class="agent-sec-h">{{ t('gui.agent_capacity.connect') }}</h3>
        <p v-if="enrollmentError" class="agent-help is-error" role="alert" data-testid="enrollment-error">{{ enrollmentError }}</p>
        <p v-if="refreshError" class="agent-help is-error agent-inline-action" role="alert">{{ t('gui.settings.agents.errors.refresh_failed') }} <Button variant="secondary" size="sm" :disabled="busy" data-testid="enrollment-refresh" @click="emit('refresh')">{{ t('gui.common.retry') }}</Button></p>
        <template v-if="agent.enabled">
          <div v-if="enrollment" class="agent-conn" data-testid="enrollment-secret">
            <div class="agent-conn-top">
              <strong>{{ t('gui.agent_capacity.token_title') }}</strong>
              <time class="agent-help" :datetime="enrollment.expires_at" data-testid="enrollment-expiry">{{ t('gui.agent_capacity.token_until', { at: clock(enrollment.expires_at) }) }}</time>
            </div>
            <div class="agent-token">
              <code ref="secretRef" data-testid="enrollment-token">{{ enrollment.enrollment_token }}</code>
              <Button variant="secondary" size="sm" data-testid="enrollment-copy" @click="copy">{{ t('gui.agent_screen.copy') }}</Button>
            </div>
            <div class="agent-wait"><span class="agent-wait-dot" aria-hidden="true" />{{ t('gui.agent_capacity.waiting') }}</div>
          </div>
          <div v-else class="agent-conn">
            <div class="agent-row">
              <div class="agent-row-l">
                <div v-if="expired" class="agent-row-t" role="status" data-testid="enrollment-expired">{{ t('gui.agent_screen.expired') }}</div>
                <div v-else class="agent-row-t">{{ t('gui.agent_capacity.no_token') }}</div>
                <p class="agent-help">{{ t('gui.agent_capacity.no_token_hint') }}</p>
              </div>
              <Button variant="primary" :disabled="busy" data-testid="enrollment-retry" @click="emit('retry')">{{ t('gui.agent_screen.retry_issue') }}</Button>
            </div>
          </div>
          <p class="agent-help">{{ t('gui.agent_capacity.token_hint') }}</p>
        </template>
        <p v-else class="agent-note info">{{ t('gui.agent_capacity.enable_first') }}</p>
      </section>

      <div v-if="agent && !creating && agent.reported_capabilities.ai_cli === false" class="agent-sec agent-sec-note">
        <p class="agent-note warn" data-testid="agent-cli-missing">{{ t('gui.agent_capacity.cli_missing_detail') }}</p>
      </div>

      <section class="agent-sec">
        <h3 class="agent-sec-h">{{ t('gui.agent_capacity.sec_basic') }}</h3>
        <div class="agent-field">
          <label for="agent-dialog-name">{{ t('gui.settings.agents.fields.name') }}<span class="agent-req" aria-hidden="true">*</span></label>
          <Input id="agent-dialog-name" :model-value="name" :maxlength="100" :disabled="busy" :invalid="showNameProblem" autocomplete="off" data-modal-autofocus :data-testid="creating ? 'agent-name' : 'edit-name'" @update:model-value="emit('update:name', $event)" />
          <p v-if="showNameProblem" class="agent-help is-error" data-testid="agent-name-invalid">{{ nameProblem }}</p>
        </div>
        <div v-if="agent && !creating" class="agent-row">
          <div class="agent-row-l">
            <div id="agent-dialog-enabled" class="agent-row-t">{{ t('gui.agent_capacity.enabled') }}</div>
            <p class="agent-help">{{ t(enabled ? 'gui.agent_capacity.enabled_on' : 'gui.agent_capacity.enabled_off') }}</p>
          </div>
          <Toggle :model-value="enabled" :disabled="busy" aria-labelledby="agent-dialog-enabled" data-testid="edit-enabled" @update:model-value="emit('update:enabled', $event)" />
        </div>
      </section>

      <section v-if="remote" class="agent-sec">
        <h3 class="agent-sec-h">{{ t('gui.agent_capacity.sec_run') }}</h3>
        <div class="agent-row">
          <div class="agent-row-l">
            <label class="agent-row-t" for="agent-max-jobs">{{ t('gui.agent_capacity.label') }}</label>
            <p class="agent-help">{{ t('gui.agent_capacity.hint') }}</p>
          </div>
          <span class="agent-num">
            <Input id="agent-max-jobs" class="agent-num-input" inputmode="numeric" autocomplete="off" :model-value="maxJobs" :disabled="busy" :invalid="!!maxJobsProblem" data-testid="agent-max-jobs" @update:model-value="emit('update:maxJobs', $event)" />
            <span>{{ t('gui.agent_capacity.unit_jobs') }}</span>
          </span>
        </div>
        <p v-if="maxJobsProblem" class="agent-help is-error agent-tight" data-testid="agent-max-jobs-invalid">{{ maxJobsProblem }}</p>
        <div v-if="online" class="agent-run agent-tight" data-testid="agent-running">
          <span class="agent-bar" :class="{ full: running >= limit }" aria-hidden="true"><i :style="{ width: `${barPercent}%` }" /></span>
          <span>{{ t('gui.agent_capacity.running_now', { running }) }}<template v-if="agent?.queued_jobs"> · {{ t('gui.agent_capacity.queued_now', { count: agent.queued_jobs }) }}</template></span>
        </div>
        <p v-if="lowered" class="agent-help is-warn agent-tight">{{ t('gui.agent_capacity.lowered') }}</p>
      </section>

      <section v-if="remote" class="agent-sec">
        <h3 class="agent-sec-h">{{ t('gui.agent_capacity.sec_workdir') }}</h3>
        <div class="agent-field">
          <label for="agent-dialog-workdir">{{ t('gui.agent_capacity.workdir_label') }}</label>
          <Input id="agent-dialog-workdir" class="agent-mono" :model-value="workdir" :placeholder="WORKDIR_EXAMPLE" :disabled="busy" autocomplete="off" spellcheck="false" data-testid="edit-remote-workdir" @update:model-value="emit('update:workdir', $event)" />
          <p v-if="preview && perJob" class="agent-help" data-testid="edit-workdir-preview">{{ previewParts[0] }}<code class="agent-path">{{ preview }}</code>{{ previewParts[1] }}</p>
          <p v-else-if="preview" class="agent-help is-warn" data-testid="edit-workdir-shared">{{ sharedParts[0] }}<code class="agent-path">{{ preview }}</code>{{ sharedParts[1] }}</p>
          <p v-else class="agent-help" :class="{ 'is-warn': !creating }" data-testid="edit-workdir-empty">{{ t('gui.agent_capacity.workdir_hint') }}</p>
        </div>
        <div class="agent-row" data-testid="edit-workspace-cleanup">
          <div class="agent-row-l">
            <div id="agent-dialog-cleanup" class="agent-row-t">{{ t('gui.settings.agents.fields.workspace_cleanup') }}</div>
            <p v-if="cleanupEnabled" class="agent-help">{{ cleanupParts[0] }}<Input id="agent-dialog-retention" class="agent-inline-num" inputmode="numeric" autocomplete="off" :aria-label="t('gui.settings.agents.fields.workspace_cleanup_hours')" :model-value="cleanupHours" :disabled="busy" :invalid="!!cleanupHoursProblem" data-testid="edit-cleanup-hours" @update:model-value="emit('update:cleanupHours', $event)" />{{ cleanupParts[1] }}</p>
            <p v-else class="agent-help">{{ t('gui.agent_capacity.cleanup_off') }}</p>
            <p v-if="cleanupEnabled && cleanupHoursProblem" class="agent-help is-error" data-testid="edit-cleanup-hours-invalid">{{ cleanupHoursProblem }}</p>
          </div>
          <Toggle :model-value="!!cleanupEnabled" :disabled="busy" aria-labelledby="agent-dialog-cleanup" data-testid="edit-cleanup-enabled" @update:model-value="emit('update:cleanupEnabled', $event)" />
        </div>
      </section>
      <p v-if="error" class="fgs-error agent-dialog-error" role="alert" :data-testid="creating ? 'agent-form-error' : 'agent-action-error'">{{ error }}</p>
    </form>
    <template #footer><footer class="agent-dialog-foot">
      <span class="agent-dialog-state" :class="{ dirty: changeCount > 0 }" data-testid="edit-state">
        <template v-if="!creating">
          <span v-if="saved && !changeCount" role="status" data-testid="edit-saved">{{ t('gui.agent_screen.saved') }}</span>
          <template v-else>{{ changeCount === 1 ? t('gui.agent_capacity.changes_one') : changeCount ? t('gui.agent_capacity.changes', { count: changeCount }) : t('gui.agent_capacity.no_changes') }}</template>
        </template>
      </span>
      <Button variant="secondary" :disabled="closeBusy" :data-testid="creating ? 'agent-create-cancel' : 'edit-cancel'" @click="emit('close')">{{ t(creating ? 'gui.common.cancel' : 'gui.common.close') }}</Button>
      <Button variant="primary" :disabled="!canSave" :data-testid="creating ? 'agent-create-submit' : 'edit-save'" @click="emit('submit')">{{ busy ? t('gui.settings.saving') : t(creating ? 'gui.agent_screen.register' : 'gui.agent_screen.save') }}</Button>
    </footer></template>
  </ModalShell>
</template>

<script setup lang="ts">
import { computed, ref, onBeforeUnmount } from 'vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import { useI18n } from 'vue-i18n'
import ModalShell from '@/components/ui/ModalShell.vue'
import Input from '@/components/ui/Input.vue'
import Button from '@/components/ui/Button.vue'
import Toggle from '@/components/ui/Toggle.vue'
import SettingsIcon from '../SettingsIcon.vue'
import { AGENT_STATE_TONE, agentState } from '../agentState'
import { AGENT_JOB_VAR, jobFolderPreview } from '../agentWorkdir'
import { relativeTime } from '../format'
import type { AgentEnrollment, SystemAgent } from '../types'
const props = defineProps<{
  creating: boolean; agent: SystemAgent | null; name: string
  /** The parent folder as shown; the view appends ${AGENT_JOB_ID} when it saves (agentWorkdir). */
  workdir: string
  /** The setting that applies (saved while untouched, else what saving stores), for the preview. */
  workdirValue?: string | null
  busy: boolean; closeBusy: boolean; canSave: boolean; nameProblem: string; error: string; saved: boolean; stateLabel: string
  maxJobs: string; maxJobsProblem: string; enabled: boolean
  enrollment: AgentEnrollment | null; enrollmentError: string; expired: boolean; refreshError: string
  /** flowgate.v02test.0019: workspace cleanup policy of a remote Agent; hours as typed. */
  cleanupEnabled?: boolean; cleanupHours?: string; cleanupHoursProblem?: string
  /** Inputs that differ from the saved values (edit only), shown next to the buttons. */
  changeCount?: number
}>()
const emit = defineEmits<{
  'update:name': [name: string]; 'update:workdir': [workdir: string]
  'update:cleanupEnabled': [value: boolean]; 'update:cleanupHours': [value: string]
  submit: []; close: []; retry: []; refresh: []; copied: [success: boolean]
  'update:maxJobs': [value: string]; 'update:enabled': [value: boolean]
}>()
const { t, locale } = useI18n()
const WORKDIR_EXAMPLE = '/data/flowgate'
/** Splits a message around one parameter so markup (an input, a code span) can sit inside it. */
const SLOT = '\u0000'
const around = (key: string, param: string): [string, string] => {
  const [before = '', after = ''] = t(key, { [param]: SLOT }).split(SLOT)
  return [before, after]
}

const title = computed(() => props.creating ? t('gui.agent_screen.create_title') : props.agent?.name ?? '')
const tone = computed(() => props.agent ? AGENT_STATE_TONE[agentState(props.agent)] : 'off')
const remote = computed(() => props.creating || props.agent?.location === 'remote')
const connecting = computed(() => props.agent?.location === 'remote' && props.agent.credential_status !== 'active')
const online = computed(() => !props.creating && !!props.agent && agentState(props.agent) === 'online')
const platform = computed(() => [props.agent?.os, props.agent?.architecture].filter(Boolean).join(' / ') || '—')
const seen = computed(() => props.agent?.last_seen_at ? relativeTime(props.agent.last_seen_at, locale.value) : t('gui.agent_screen.before_connection'))
const showNameProblem = computed(() => !!props.nameProblem && (!props.creating || !!props.name))

const running = computed(() => props.agent?.running_jobs ?? 0)
const limit = computed(() => props.agent?.max_concurrent_jobs ?? 4)
const barPercent = computed(() => limit.value > 0 ? Math.min(100, (running.value / limit.value) * 100) : 0)
const lowered = computed(() => !!props.agent && !props.creating && !props.maxJobsProblem && Number(props.maxJobs) < running.value)

const preview = computed(() => props.workdirValue ? jobFolderPreview(props.workdirValue) : '')
/** False only for an older saved setting without ${AGENT_JOB_ID}: every Job shares that one folder. */
const perJob = computed(() => !!props.workdirValue?.includes(AGENT_JOB_VAR))
const previewParts = computed(() => around('gui.agent_capacity.workdir_preview', 'path'))
const sharedParts = computed(() => around('gui.agent_capacity.workdir_shared', 'path'))
const cleanupParts = computed(() => around('gui.agent_capacity.cleanup_on', 'hours'))
const changeCount = computed(() => props.changeCount ?? 0)

function clock(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleTimeString(locale.value, { hour: '2-digit', minute: '2-digit' })
}

const secretRef = ref<HTMLElement | null>(null)
let active = true
onBeforeUnmount(() => { active = false })
async function copy() {
  const issued = props.enrollment
  if (!issued || Date.parse(issued.expires_at) <= Date.now()) return
  const copied = await copyToClipboard(issued.enrollment_token, secretRef.value)
  if (active && props.enrollment === issued) emit('copied', copied)
}
</script>

<style>
/* flowgate.v02test.0043 CH0004: draft 4 (one status chip, section headings with hairlines, switches,
   narrow number inputs with units, cleanup hours inside its sentence, change count in the footer). */
.ui-modal-surface.agent-settings-dialog { max-width: 520px; border-radius: 14px; }
.agent-settings-dialog .agent-dialog-head { display: flex; gap: 14px; align-items: flex-start; padding: 20px 20px 18px 24px; border-bottom: 1px solid var(--fg2-line); }
.agent-settings-dialog .agent-dialog-symbol { width: 38px; height: 38px; flex: none; display: grid; place-items: center; border: 1px solid var(--fg2-line-strong); border-radius: 10px; color: var(--fg2-accent); background: rgba(75, 163, 255, 0.06); }
.agent-settings-dialog .agent-dialog-titles { flex: 1; min-width: 0; }
.agent-settings-dialog .agent-dialog-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.agent-settings-dialog .agent-dialog-title h2 { margin: 0; font-size: 16px; font-weight: 700; letter-spacing: -0.01em; overflow-wrap: anywhere; }
.agent-settings-dialog .agent-dialog-sub { margin: 5px 0 0; font-size: 12px; color: var(--fg2-muted); }
.agent-settings-dialog .agent-dialog-close { width: 32px; height: 32px; padding: 0; margin: -4px -2px 0 0; flex: none; }
.agent-settings-dialog .agent-chip { display: inline-flex; align-items: center; gap: 6px; height: 22px; padding: 0 9px; border: 1px solid; border-radius: 999px; font-size: 11.5px; font-weight: 650; white-space: nowrap; }
.agent-settings-dialog .agent-chip::before { content: ''; width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.agent-settings-dialog .agent-chip.ok { color: #4fe0c3; border-color: rgba(38, 211, 174, 0.35); background: rgba(38, 211, 174, 0.08); }
.agent-settings-dialog .agent-chip.warn { color: #f4c867; border-color: rgba(242, 190, 85, 0.35); background: rgba(242, 190, 85, 0.08); }
.agent-settings-dialog .agent-chip.bad { color: var(--fg2-status-danger-fg); border-color: rgba(255, 120, 140, 0.35); background: rgba(255, 120, 140, 0.08); }
.agent-settings-dialog .agent-chip.info { color: #8fc5ff; border-color: rgba(75, 163, 255, 0.35); background: rgba(75, 163, 255, 0.08); }
.agent-settings-dialog .agent-chip.off { color: #9fb1c5; border-color: rgba(159, 177, 197, 0.3); background: rgba(159, 177, 197, 0.07); }

.agent-settings-dialog .agent-dialog-form { display: grid; }
.agent-settings-dialog .agent-sec { display: grid; gap: 16px; margin: 0; padding: 18px 0 20px; min-width: 0; }
.agent-settings-dialog .agent-sec + .agent-sec { border-top: 1px solid var(--fg2-line); }
.agent-settings-dialog .agent-sec.agent-sec-note { padding-bottom: 0; }
.agent-settings-dialog .agent-sec-note + .agent-sec { border-top: 0; }
.agent-settings-dialog .agent-sec-h { margin: 0; font-size: 11px; font-weight: 700; color: var(--fg2-muted-2); letter-spacing: 0.04em; }
.agent-settings-dialog .agent-field { display: grid; gap: 7px; min-width: 0; }
.agent-settings-dialog .agent-field > label, .agent-settings-dialog .agent-row-t { display: block; font-size: 12.5px; font-weight: 650; color: #dbe7f5; }
.agent-settings-dialog .agent-req { margin-left: 2px; color: var(--fg2-status-danger-fg); }
.agent-settings-dialog .agent-help { margin: 0; font-size: 11.5px; line-height: 1.55; color: var(--fg2-muted); }
.agent-settings-dialog .agent-help.is-error { color: var(--fg2-status-danger-fg); }
.agent-settings-dialog .agent-help.is-warn { color: #f4c867; }
.agent-settings-dialog .agent-tight { margin-top: -6px; }
.agent-settings-dialog .agent-inline-action { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.agent-settings-dialog .agent-row { display: flex; gap: 16px; align-items: center; justify-content: space-between; }
.agent-settings-dialog .agent-row-l { flex: 1; min-width: 0; }
.agent-settings-dialog .agent-row-l .agent-help { margin-top: 3px; }
.agent-settings-dialog .agent-num { display: inline-flex; align-items: center; gap: 8px; flex: none; font-size: 12.5px; color: var(--fg2-control-fg); }
.agent-settings-dialog .ui-input.agent-num-input { width: 76px; text-align: right; }
.agent-settings-dialog .ui-input.agent-inline-num { display: inline-block; width: 58px; height: 26px; margin: 0 4px; padding: 0 8px; border-radius: 6px; font-size: 12px; text-align: right; vertical-align: baseline; }
.agent-settings-dialog .ui-input.agent-mono, .agent-settings-dialog .agent-path, .agent-settings-dialog .agent-token code { font-family: ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', monospace; letter-spacing: 0; }
.agent-settings-dialog .ui-input.agent-mono { font-size: 12.5px; }
.agent-settings-dialog .agent-path { font-size: 11.5px; color: #c3d2e3; overflow-wrap: anywhere; }
.agent-settings-dialog .agent-run { display: flex; align-items: center; gap: 10px; font-size: 11.5px; color: var(--fg2-muted); }
.agent-settings-dialog .agent-bar { flex: none; width: 84px; height: 5px; border-radius: 5px; background: rgba(154, 181, 210, 0.14); overflow: hidden; }
.agent-settings-dialog .agent-bar i { display: block; height: 100%; border-radius: 5px; background: var(--fg2-cyan); }
.agent-settings-dialog .agent-bar.full i { background: #ffc072; }

.agent-settings-dialog .agent-conn { display: grid; gap: 12px; padding: 14px; border: 1px solid rgba(75, 163, 255, 0.28); border-radius: 10px; background: rgba(75, 163, 255, 0.06); }
.agent-settings-dialog .agent-conn-top { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
.agent-settings-dialog .agent-conn-top strong { font-size: 12.5px; }
.agent-settings-dialog .agent-token { display: flex; align-items: center; gap: 8px; padding: 5px 5px 5px 12px; border: 1px solid var(--fg2-line-strong); border-radius: 8px; background: rgba(5, 12, 21, 0.55); }
.agent-settings-dialog .agent-token code { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12.5px; color: #dfebfa; user-select: all; }
.agent-settings-dialog .agent-wait { display: flex; align-items: center; gap: 8px; font-size: 12px; color: #d0dfee; }
.agent-settings-dialog .agent-wait-dot { width: 7px; height: 7px; border-radius: 50%; background: #f2be55; animation: agent-wait-pulse 1.6s infinite; }
@keyframes agent-wait-pulse { 0% { box-shadow: 0 0 0 0 #f2be5566; } 70% { box-shadow: 0 0 0 7px #f2be5500; } 100% { box-shadow: 0 0 0 0 #f2be5500; } }
@media (prefers-reduced-motion: reduce) { .agent-settings-dialog .agent-wait-dot { animation: none; } }
.agent-settings-dialog .agent-note { margin: 0; padding: 10px 12px; border-radius: 8px; font-size: 12px; line-height: 1.55; }
.agent-settings-dialog .agent-note.warn { border: 1px solid rgba(242, 190, 85, 0.3); background: rgba(242, 190, 85, 0.07); color: #f1d79a; }
.agent-settings-dialog .agent-note.info { border: 1px solid var(--fg2-line-strong); background: rgba(154, 181, 210, 0.05); color: #c3d2e3; }
.agent-settings-dialog .agent-dialog-error { margin: 0 0 16px; }

.agent-settings-dialog .agent-dialog-foot { display: flex; align-items: center; gap: 9px; padding: 14px 24px; border-top: 1px solid var(--fg2-line); }
.agent-settings-dialog .agent-dialog-state { flex: 1; min-width: 0; font-size: 12px; color: var(--fg2-muted); }
.agent-settings-dialog .agent-dialog-state.dirty { color: #9cc8ff; }
@media (max-width: 480px) {
  .agent-settings-dialog .agent-row { flex-wrap: wrap; }
  .agent-settings-dialog .agent-dialog-foot { flex-wrap: wrap; }
}
</style>
