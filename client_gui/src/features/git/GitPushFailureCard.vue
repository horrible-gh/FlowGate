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
    <div class="gx-actions">
      <button type="button" class="gx-btn primary" :disabled="operation ? !operation.gate.recovery?.retryable || gateJobs?.busy.value || gateJobs?.readOnly() : !session.canAct.value" data-test="git-push-retry" @click="retry">
        {{ t('gui.git.push.retry') }}
      </button>
    </div>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — remote apply failure card, minimal as v7 fixes it (0033-D §3.6): title,
// `!` (details window only) and `다시 반영`, which resends the recorded request as it was. No
// stderr, no long text, no cause guessed on the client.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitGateOperations, type GateOperation } from './gateOperations'
import { useGitPanel } from './gitSession'

const props = defineProps<{ operation?: GateOperation }>()
const gateJobs = useGitGateOperations()
const { t } = useI18n()
const session = useGitPanel().session

const noticeText = computed(() => {
  if (props.operation) return gateJobs?.notice.value ?? null
  const notice = session.state.notices.push
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
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
