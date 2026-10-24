<template>
  <ModelessShell
    :open="true"
    :title="win.title"
    :minimized="win.minimized"
    :initial-width="400"
    :initial-height="280"
    @update:minimized="(value: boolean) => session.setWindowMinimized(win.id, value)"
    @close="session.closeWindow(win.id)"
  >
    <div class="gx-window" data-test="git-push-window">
      <template v-if="operation">
        <dl class="gx-tech" data-test="git-job-evidence">
          <dt>{{ t('gui.git.window.code') }}</dt><dd>{{ operation.gate.recovery?.error_code ?? '—' }}</dd>
          <dt>{{ t('gui.git.gate_job.job') }}</dt><dd>{{ operation.gate.job?.job_id ?? '—' }}</dd>
          <dt>{{ t('gui.git.gate_job.decision') }}</dt><dd>{{ operation.gate.recovery?.decision_id ?? '—' }}</dd>
          <dt>{{ t('gui.git.gate_job.phase') }}</dt><dd>{{ operation.gate.recovery?.phase ?? '—' }}</dd>
          <template v-for="effect in operation.gate.decision?.effects ?? []" :key="String(effect.phase)">
            <dt>{{ effect.phase }}</dt><dd>{{ effect.status }} · {{ effect.sha_after ?? effect.target_ref ?? effect.error_code ?? '—' }}<span v-if="effect.recovery_state"> · {{ effect.recovery_state }}</span></dd>
          </template>
        </dl>
        <div class="gx-actions">
          <button class="gx-btn primary" :disabled="locked || !operation.gate.recovery?.retryable" data-test="git-job-retry" @click="recover('retry')">{{ t('gui.git.gate_job.retry') }}</button>
          <button class="gx-btn ghost" :disabled="locked || !operation.gate.recovery?.cancelable" data-test="git-job-cancel" @click="recover('cancel')">{{ t('gui.git.gate_job.cancel') }}</button>
        </div>
        <template v-if="operation.gate.recovery?.resolvable">
          <label><input v-model="reviewed" type="checkbox" :disabled="locked" data-test="git-job-reviewed" />{{ t('gui.git.gate_job.reviewed') }}</label>
          <textarea v-model="reason" :aria-label="t('gui.git.gate_job.reason')" :placeholder="t('gui.git.gate_job.reason')" :disabled="locked" data-test="git-job-reason" />
          <div class="gx-actions">
            <button class="gx-btn primary" :disabled="locked || !reviewed || !reason.trim()" data-test="git-job-resolve" @click="recover('resolve', 'effects_confirmed')">{{ t('gui.git.gate_job.confirm_effects') }}</button>
            <button class="gx-btn danger" :disabled="locked || !reviewed || !reason.trim()" data-test="git-job-abandon" @click="recover('resolve', 'abandon')">{{ t('gui.git.gate_job.abandon') }}</button>
          </div>
        </template>
        <p v-if="gateJobs?.notice.value" class="gx-notice" role="alert">{{ gateJobs.notice.value }}</p>
      </template>
      <dl v-else-if="failure" class="gx-tech">
        <dt>{{ t('gui.git.window.code') }}</dt>
        <dd data-test="git-push-code">{{ failure.code }}</dd>
        <dt>{{ t('gui.git.window.push_target') }}</dt>
        <dd data-test="git-push-target">{{ failure.target || t('gui.git.header.none') }}</dd>
        <dt>{{ t('gui.git.window.at') }}</dt>
        <dd data-test="git-push-at">{{ at }}</dd>
        <dt>{{ t('gui.git.window.diagnostic') }}</dt>
        <dd data-test="git-push-diagnostic">{{ t('gui.git.window.no_diagnostic') }}</dd>
      </dl>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — remote apply failure details (0033-D §6.2): only what the server really
// gives — the error code — plus the record's target and time. The server keeps the git stderr
// in its log only, so diagnostics read "not provided" (G2); its `message` is not UI text.
// There is no retry here: `다시 반영` stays on the card.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import { useGitGateOperations, type GateOperation } from './gateOperations'
import { useGitPanel } from './gitSession'
import type { GitWindowDescriptor } from './types'

const props = defineProps<{ win: GitWindowDescriptor; operation?: GateOperation | null }>()
const { t, locale } = useI18n()
const session = useGitPanel().session

const gateJobs = useGitGateOperations()
const reason = ref('')
const reviewed = ref(false)
const locked = computed(() => !!gateJobs?.busy.value || gateJobs?.readOnly() !== false)
watch(() => props.operation, () => { reason.value = ''; reviewed.value = false })
async function recover(action: 'retry' | 'cancel' | 'resolve', resolution?: 'effects_confirmed' | 'abandon') {
  if (locked.value || (action === 'resolve' && (!reviewed.value || !reason.value.trim()))) return
  if (await gateJobs?.act(action, resolution, reason.value)) session.closeWindow(props.win.id)
}
const failure = computed(() => session.state.pushFailure)
const at = computed(() => {
  const value = failure.value?.at
  if (!value) return t('gui.git.header.none')
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
})
</script>
