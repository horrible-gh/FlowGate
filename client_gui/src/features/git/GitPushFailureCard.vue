<template>
  <section class="gx-card danger" data-test="git-push-card">
    <div class="gx-title-row">
      <div class="gx-title">{{ operation ? t('gui.git.gate_job.title') : t('gui.git.push.title') }}</div>
      <button
        type="button"
        class="gx-error-icon"
        :title="t('gui.git.push.detail')"
        :aria-label="t('gui.git.push.detail')"
        data-test="git-push-detail"
        @click="detail"
      >
        !
      </button>
    </div>
    <GateSettingsSummary v-if="operation" :decision="operation.gate.decision" :error-code="gateJobs?.notice.value ?? operation.gate.recovery?.error_code" />
    <p v-else-if="session.state.pushFailure?.code === 'push_rejected'" class="gx-notice" data-test="git-push-rejected">{{ t('gui.inspector.checkSettings.target.push_rejected') }}</p>
    <!-- flowgate.v02test.0052 (0008 할 일 4): the approval's merge session is closed, so [다시 반영] can never succeed (it only
         re-reads the closed session). It is off and says why; the one button that works is [이 승인 취소], then the check card. -->
    <p v-if="closed" class="gx-reason" data-test="git-closed-note">{{ t('gui.git.gate_job.closed_note') }}</p>
    <div class="gx-actions">
      <button v-if="closed" type="button" class="gx-btn primary" :disabled="cancelBlocked" data-test="git-push-cancel" @click="cancel">
        {{ t('gui.git.gate_job.cancel_approval') }}
      </button>
      <button type="button" class="gx-btn" :class="closed ? 'ghost' : 'primary'" :disabled="closed || (operation ? !operation.gate.recovery?.retryable || gateJobs?.busy.value || gateJobs?.readOnly() : !session.canAct.value)" data-test="git-push-retry" @click="retry">
        {{ t('gui.git.push.retry') }}
      </button>
      <button v-if="closed" type="button" class="gx-btn ghost" data-test="git-goto-gate" @click="gotoGate">
        {{ t('gui.git.gate_job.goto_gate') }}
      </button>
    </div>
    <p v-if="closed" class="gx-desc" data-test="git-closed-next">{{ t('gui.git.gate_job.closed_next') }}</p>
    <p v-if="retryBlockedText" class="gx-reason" data-test="git-retry-blocked">{{ retryBlockedText }}</p>
    <p v-if="stateText" class="gx-notice" data-test="git-state">{{ stateText }}</p>
    <p v-if="retryText" class="gx-notice" data-test="git-retry-outcome">{{ retryText }}</p>
    <p v-if="retryNext" class="gx-reason" data-test="git-retry-next">{{ retryNext }}</p>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — remote apply failure card, minimal as v7 fixes it (0033-D §3.6): title,
// `!` (details window only) and `다시 반영`, which resends the recorded request as it was. No
// stderr, no long text, no cause guessed on the client.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { checkOperationErrorKey } from '@/features/document'
import GateSettingsSummary from '../designer/inspector/features/gateCard/GateSettingsSummary.vue'
import { isClosedSessionOperation, openGateCard, retryNextText, retryOutcomeText, useGitGateOperations, type GateOperation } from './gateOperations'
import { useGitPanel } from './gitSession'

const props = defineProps<{ operation?: GateOperation }>()
const gateJobs = useGitGateOperations()
const { t } = useI18n()
const session = useGitPanel().session

const noticeText = computed(() => {
  if (props.operation) {
    const code = gateJobs?.notice.value
    const key = checkOperationErrorKey(code, props.operation.gate.decision)
    return key ? t(`gui.inspector.checkSettings.${key}`) : code ? t('gui.inspector.checkSettings.target.operation_unknown') : null
  }
  const notice = session.state.notices.push
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})
// flowgate.v02.0132 T#4: syncing automatically / waiting for a recovery, and how an accepted retry ended.
const stateText = computed(() => props.operation?.gate.git_active ? t('gui.inspector.checkSettings.target.syncing')
  : props.operation?.gate.parked ? t('gui.inspector.checkSettings.target.parked') : null)
const retryText = computed(() => retryOutcomeText(gateJobs?.retryOutcomeOf(props.operation) ?? null, t))
// flowgate.v02test.0052 (0008 할 일 5): why trying again does not help and where to press next, under the outcome line.
const retryNext = computed(() => retryNextText(gateJobs?.retryOutcomeOf(props.operation) ?? null, t))
const closed = computed(() => !!props.operation && isClosedSessionOperation(props.operation.gate))
const cancelBlocked = computed(() => !props.operation?.gate.recovery?.cancelable || !!gateJobs?.busy.value || gateJobs?.readOnly() !== false)
function cancel() {
  if (!props.operation || cancelBlocked.value) return
  gateJobs?.select(props.operation)
  void gateJobs?.act('cancel')
}
function gotoGate() {
  openGateCard(session.state.flow?.groupId, props.operation?.nodeKey)
}
// flowgate.v02gui.0004 (0001-B 관찰 5): why [다시 반영] is off, instead of a button that only looks usable.
const retryBlockedText = computed(() => {
  const operation = props.operation
  if (!operation) return null
  if (closed.value) return t('gui.git.gate_job.retry_blocked_closed')
  if (operation.gate.recovery?.retryable) return null
  if (operation.gate.git_active) return null
  return t(operation.gate.recovery?.cancelable ? 'gui.git.gate_job.retry_blocked_cancel' : 'gui.git.gate_job.retry_blocked')
})
function detail() {
  if (props.operation) {
    gateJobs?.select(props.operation)
    session.openPushErrorWindow(t('gui.git.gate_job.title'), session.state.flow?.groupId)
  } else session.openPushErrorWindow(t('gui.git.window.push_title'))
}
function retry() {
  if (props.operation) { gateJobs?.select(props.operation); void gateJobs?.act('retry') }
  else void session.retryApply()
}
</script>
