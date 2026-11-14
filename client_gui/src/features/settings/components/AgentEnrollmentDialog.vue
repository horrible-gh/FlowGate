<template>
  <ModalShell :open="true" surface-class="agent-enrollment-dialog" :aria-label="t('gui.agent_screen.enroll_title')" @close="emit('close')">
    <template #header="{ titleId }"><header class="enroll-head"><h2 :id="titleId">{{ t('gui.agent_screen.enroll_title') }}</h2><Button variant="ghost" :aria-label="t('gui.common.close')" data-testid="enrollment-close" @click="emit('close')"><SettingsIcon name="x" /></Button></header></template>
    <div class="enroll-body" data-testid="agent-enrollment">
      <strong>{{ agent.name }}</strong>
      <p>{{ t('gui.agent_screen.enroll_hint') }}</p>
      <p v-if="error" class="fgs-error" role="alert" data-testid="enrollment-error">{{ error }}</p>
      <p v-if="refreshError" class="fgs-error" role="alert">{{ t('gui.settings.agents.errors.refresh_failed') }} <Button variant="secondary" :disabled="busy" data-testid="enrollment-refresh" @click="emit('refresh')">{{ t('gui.common.retry') }}</Button></p>
      <div v-if="enrollment" class="enroll-secret" data-testid="enrollment-secret">
        <code ref="secretRef" data-testid="enrollment-token">{{ enrollment.enrollment_token }}</code>
        <span data-testid="enrollment-expiry">{{ t('gui.settings.agents.token_expires', { at: enrollment.expires_at }) }}</span>
        <Button variant="secondary" data-testid="enrollment-copy" @click="copy">{{ t('gui.agent_screen.copy') }}</Button>
      </div>
      <p v-else-if="expired" role="status" data-testid="enrollment-expired">{{ t('gui.agent_screen.expired') }}</p>
      <p v-else-if="busy" role="status">{{ t('gui.common.loading') }}</p>
      <Button v-if="(error || expired) && agent.enabled && agent.credential_status !== 'active'" variant="secondary" :disabled="busy" data-testid="enrollment-retry" @click="emit('retry')">{{ t('gui.agent_screen.retry_issue') }}</Button>
      <dl class="enroll-status"><div><dt>{{ t('gui.agent_screen.connection') }}</dt><dd data-testid="enrollment-state">{{ stateLabel }}</dd></div><div><dt>{{ t('gui.agent_screen.last_seen') }}</dt><dd>{{ agent.last_seen_at ? relativeTime(agent.last_seen_at, locale) : t('gui.agent_screen.before_connection') }}</dd></div></dl>
    </div>
    <template #footer><footer class="enroll-foot"><Button variant="primary" data-testid="enrollment-done" @click="emit('close')">{{ t('gui.agent_screen.done') }}</Button></footer></template>
  </ModalShell>
</template>

<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModalShell from '@/components/ui/ModalShell.vue'
import Button from '@/components/ui/Button.vue'
import SettingsIcon from '../SettingsIcon.vue'
import { copyToClipboard } from '@/core/interaction/clipboard'
import { relativeTime } from '../format'
import type { AgentEnrollment, SystemAgent } from '../types'
const props = defineProps<{ agent: SystemAgent; enrollment: AgentEnrollment | null; busy: boolean; error: string; refreshError: string; stateLabel: string; expired: boolean }>()
const emit = defineEmits<{ close: []; retry: []; refresh: []; copied: [success: boolean] }>()
const { t, locale } = useI18n()
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

<style scoped>
:global(.ui-modal-surface.agent-enrollment-dialog) { max-width: 515px; border-radius: 14px; }
.enroll-head { display: flex; justify-content: space-between; align-items: center; padding: 20px 24px 17px; border-bottom: 1px solid var(--fg2-line-strong); }
.enroll-head h2 { margin: 0; font-size: 17px; }
.enroll-body { display: grid; gap: 17px; padding: 22px 0; }
.enroll-body p { margin: 0; font-size: 12px; line-height: 1.65; }
.enroll-secret { display: grid; gap: 10px; padding: 15px; border: 1px solid var(--fg2-line-strong); border-radius: 9px; background: var(--fg2-control-surface); }
.enroll-secret code { overflow-wrap: anywhere; user-select: all; }
.enroll-secret span { color: var(--fg2-muted); font-size: 11px; }
.enroll-status { margin: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 13px; font-size: 12px; }
.enroll-status dt { color: var(--fg2-muted); }.enroll-status dd { margin: 8px 0 0; }
.enroll-foot { display: flex; justify-content: flex-end; padding: 14px 24px; border-top: 1px solid var(--fg2-line-strong); }
</style>
