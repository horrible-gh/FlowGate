<template>
  <ModalShell :open="true" surface-class="agent-settings-dialog" :aria-label="title" @close="emit('close')">
    <template #header="{ titleId }">
      <header class="agent-dialog-head"><h2 :id="titleId">{{ title }}</h2><Button variant="ghost" :disabled="closeBusy" :aria-label="t('gui.common.close')" data-testid="agent-editor-close" @click="emit('close')"><SettingsIcon name="x" /></Button></header>
    </template>
    <form class="agent-dialog-form" :data-testid="creating ? 'agent-create-form' : 'agent-detail'" :data-agent="agent?.agent_id" @submit.prevent="emit('submit')">
      <dl v-if="agent && !creating" class="agent-info-grid" data-testid="agent-status">
        <div><dt>{{ t('gui.agent_screen.connection') }}</dt><dd data-field="connection">{{ stateLabel }}</dd></div>
        <div><dt>{{ t('gui.agent_screen.os') }}</dt><dd data-field="platform">{{ [agent.os, agent.architecture].filter(Boolean).join(' / ') || '—' }}</dd></div>
        <div><dt>{{ t('gui.agent_capacity.running') }}</dt><dd>{{ agent.running_jobs ?? 0 }} / {{ agent.max_concurrent_jobs ?? 4 }}</dd></div>
      </dl>
      <p v-if="agent?.reported_capabilities.ai_cli === false" class="agent-dialog-note">{{ t('gui.agent_capacity.cli_missing') }}</p>
      <section v-if="agent && agent.location === 'remote' && agent.credential_status !== 'active'" class="agent-form-group" data-testid="agent-enrollment">
        <strong>{{ t('gui.agent_capacity.connect') }}</strong>
        <p v-if="enrollmentError" class="fgs-error" role="alert" data-testid="enrollment-error">{{ enrollmentError }}</p>
        <p v-if="refreshError" class="fgs-error" role="alert">{{ t('gui.settings.agents.errors.refresh_failed') }} <Button variant="secondary" :disabled="busy" data-testid="enrollment-refresh" @click="emit('refresh')">{{ t('gui.common.retry') }}</Button></p>
        <template v-if="agent.enabled">
          <div v-if="enrollment" class="agent-dialog-note" data-testid="enrollment-secret">
            <code ref="secretRef" data-testid="enrollment-token">{{ enrollment.enrollment_token }}</code>
            <p data-testid="enrollment-expiry">{{ t('gui.settings.agents.token_expires', { at: enrollment.expires_at }) }}</p>
            <Button variant="secondary" data-testid="enrollment-copy" @click="copy">{{ t('gui.agent_screen.copy') }}</Button>
          </div>
          <p v-if="expired" role="status" data-testid="enrollment-expired">{{ t('gui.agent_screen.expired') }}</p>
          <Button v-if="!enrollment" variant="secondary" :disabled="busy" data-testid="enrollment-retry" @click="emit('retry')">{{ t('gui.agent_screen.retry_issue') }}</Button>
          <small>{{ t('gui.agent_capacity.token_hint') }}</small>
          <small v-if="enrollment">{{ t('gui.agent_capacity.waiting') }}</small>
        </template>
        <small v-else>{{ t('gui.agent_capacity.enable_first') }}</small>
      </section>


      <div class="agent-form-group">
        <label for="agent-dialog-name">{{ t('gui.settings.agents.fields.name') }} <span>*</span></label>
        <Input id="agent-dialog-name" :model-value="name" :maxlength="100" :disabled="busy" :invalid="!!name && !!nameProblem" autocomplete="off" data-modal-autofocus :data-testid="creating ? 'agent-name' : 'edit-name'" @update:model-value="emit('update:name', $event)" />
        <small v-if="name && nameProblem" class="fgs-error" data-testid="agent-name-invalid">{{ nameProblem }}</small>
      </div>
      <div v-if="creating || agent?.location === 'remote'" class="agent-form-group">
        <label for="agent-max-jobs">{{ t('gui.agent_capacity.label') }}</label>
        <Input id="agent-max-jobs" type="number" min="1" max="32" step="1" inputmode="numeric" :model-value="maxJobs" :disabled="busy" :invalid="!!maxJobsProblem" data-testid="agent-max-jobs" @update:model-value="emit('update:maxJobs', $event)" />
        <small v-if="maxJobsProblem" class="fgs-error" data-testid="agent-max-jobs-invalid">{{ maxJobsProblem }}</small>
        <small v-else>{{ t('gui.agent_capacity.hint') }}</small>
        <small v-if="agent && Number(maxJobs) < (agent.running_jobs ?? 0)">{{ t('gui.agent_capacity.lowered') }}</small>
      </div>
      <div v-if="creating || agent?.location === 'remote'" class="agent-form-group">
        <label for="agent-dialog-workdir">{{ t('gui.settings.agents.fields.remote_workdir') }}</label>
        <Input id="agent-dialog-workdir" :model-value="workdir" :disabled="busy" autocomplete="off" data-testid="edit-remote-workdir" @update:model-value="emit('update:workdir', $event)" />
        <small>{{ t('gui.agent_capacity.workdir_hint') }}</small>
      </div>
      <div v-if="creating || agent?.location === 'remote'" class="agent-form-group" data-testid="edit-workspace-cleanup">
        <label class="agent-check-row">
          <input type="checkbox" :checked="cleanupEnabled" :disabled="busy" data-testid="edit-cleanup-enabled" @change="emit('update:cleanupEnabled', ($event.target as HTMLInputElement).checked)" />
          <span><strong>{{ t('gui.settings.agents.fields.workspace_cleanup') }}</strong></span>
        </label>
        <label for="agent-dialog-retention">{{ t('gui.settings.agents.fields.workspace_cleanup_hours') }}</label>
        <Input id="agent-dialog-retention" type="number" min="1" max="720" step="1" inputmode="numeric" :model-value="cleanupHours" :disabled="busy" :invalid="!!cleanupHoursProblem" data-testid="edit-cleanup-hours" @update:model-value="emit('update:cleanupHours', $event)" />
        <small v-if="cleanupHoursProblem" class="fgs-error" data-testid="edit-cleanup-hours-invalid">{{ cleanupHoursProblem }}</small>
        <small v-else>{{ t('gui.settings.agents.fields.workspace_cleanup_hours_hint') }}</small>
      </div>
      <label v-if="agent && !creating" class="agent-check-row">
        <input type="checkbox" :checked="enabled" :disabled="busy" data-testid="edit-enabled" @change="emit('update:enabled', ($event.target as HTMLInputElement).checked)" />
        <span>{{ t('gui.agent_capacity.enabled') }}</span>
      </label>
      <p v-if="error" class="fgs-error" role="alert" :data-testid="creating ? 'agent-form-error' : 'agent-action-error'">{{ error }}</p>
      <p v-if="saved" role="status" data-testid="edit-saved">{{ t('gui.agent_screen.saved') }}</p>
    </form>
    <template #footer><footer class="agent-dialog-foot">
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
import SettingsIcon from '../SettingsIcon.vue'
import type { AgentEnrollment, SystemAgent } from '../types'
const props = defineProps<{
  creating: boolean; agent: SystemAgent | null; name: string; workdir: string
  busy: boolean; closeBusy: boolean; canSave: boolean; nameProblem: string; error: string; saved: boolean; stateLabel: string
  /** flowgate.v02test.0019: workspace cleanup policy of a remote Agent; hours as typed. */
  maxJobs: string; maxJobsProblem: string; enabled: boolean
  enrollment: AgentEnrollment | null; enrollmentError: string; expired: boolean; refreshError: string
  cleanupEnabled?: boolean; cleanupHours?: string; cleanupHoursProblem?: string
}>()
const emit = defineEmits<{
  'update:name': [name: string]; 'update:workdir': [workdir: string]
  'update:cleanupEnabled': [value: boolean]; 'update:cleanupHours': [value: string]
  submit: []; close: []; retry: []; refresh: []; copied: [success: boolean]
  'update:maxJobs': [value: string]; 'update:enabled': [value: boolean]
}>()
const { t } = useI18n()
const title = computed(() => props.creating ? t('gui.agent_screen.create_title') : `${props.agent?.name ?? ''} · ${props.stateLabel}`)
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
.ui-modal-surface.agent-settings-dialog { max-width: 515px; border-radius: 14px; }
.agent-settings-dialog .agent-dialog-head { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px 17px; border-bottom: 1px solid var(--fg2-line-strong); }
.agent-settings-dialog .agent-dialog-head h2 { font-size: 17px; margin: 0; }
.agent-settings-dialog .agent-dialog-form { display: grid; gap: 17px; padding: 22px 0; }
.agent-settings-dialog .agent-form-group { display: grid; gap: 8px; border: 0; padding: 0; margin: 0; min-width: 0; }
.agent-settings-dialog .agent-form-group > label, .agent-settings-dialog legend { font-size: 11px; font-weight: 700; }
.agent-settings-dialog legend { margin-bottom: 8px; }
.agent-settings-dialog small { color: var(--fg2-muted); font-size: 10px; line-height: 1.6; }
.agent-settings-dialog .agent-check-row { display: flex; gap: 12px; align-items: center; padding: 12px 13px; border: 1px solid var(--fg2-line-strong); border-radius: 9px; background: var(--fg2-control-surface); }
.agent-settings-dialog .agent-check-row strong, .agent-settings-dialog .agent-check-row small { display: block; }
.agent-settings-dialog .agent-check-row input { width: 16px; height: 16px; }
.agent-settings-dialog .agent-info-grid { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid var(--fg2-line-strong); border-radius: 10px; margin: 0; }
.agent-settings-dialog .agent-info-grid > div { padding: 15px; }
.agent-settings-dialog dt { font-size: 11px; color: var(--fg2-muted); }
.agent-settings-dialog dd { margin: 8px 0 0; font-size: 13px; }
.agent-settings-dialog .agent-dialog-note { margin: 0; padding: 12px 15px; border: 1px solid var(--fg2-line-strong); border-radius: 10px; font-size: 11px; line-height: 1.65; }
.agent-settings-dialog .agent-dialog-foot { display: flex; justify-content: flex-end; gap: 9px; padding: 14px 24px; border-top: 1px solid var(--fg2-line-strong); }
.agent-settings-dialog code { overflow-wrap: anywhere; user-select: all; }
</style>
