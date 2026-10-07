<template>
  <section v-if="hold" class="gx-card hold" data-test="git-hold-card">
    <div class="gx-title">{{ t('gui.git.hold.title') }}</div>
    <div class="gx-kicker" data-test="git-hold-plan">{{ target }} · {{ actionLabel }}</div>
    <div class="gx-actions">
      <button
        type="button"
        class="gx-btn primary"
        :disabled="!session.canAct.value || !!hold.blockReason"
        data-test="git-hold-apply"
        @click="askApply"
      >
        {{ t('gui.git.hold.apply') }}
      </button>
      <button type="button" class="gx-btn ghost" :disabled="!canOpenSettings" data-test="git-hold-settings" @click="ctx.openSettings()">
        {{ t('gui.git.hold.settings') }}
      </button>
    </div>
    <p v-if="reason" class="gx-reason" data-test="git-hold-reason">{{ reason }}</p>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — apply on hold card (0033-D §3.4, Q 395). The project default action is
// never shown or sent as the group's plan. Stage 1 (no S1 fields): "반영 방식 미확인" and
// `지금 반영` disabled. Stage 2: the S1 planned action / target, sent explicitly to finalize.
// `설정` only raises the open-confirm-settings intent (C4).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitPanel } from './gitSession'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const hold = computed(() => session.holdView.value)
// `설정` sends no request, so a busy session leaves it on; readOnly / 403 turn it off (§5.1).
const canOpenSettings = computed(() => !session.state.readOnly && !session.state.forbidden)
const target = computed(() => hold.value?.targetBranch || t('gui.git.header.base_unset'))
const actionLabel = computed(() => {
  const view = hold.value
  if (!view || view.stage === 1) return t('gui.git.hold.unconfirmed')
  return view.action ? t(`gui.git.hold.action.${view.action}`) : t('gui.git.hold.undecided')
})

const reason = computed(() => {
  const block = hold.value?.blockReason
  if (block === 'stage1' || block === 'no_plan') return t('gui.git.hold.no_plan')
  if (block === 'final_approval_bound') return t('gui.git.hold.bound')
  return null
})

const noticeText = computed(() => {
  const notice = session.state.notices.hold
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function askApply(): void {
  ctx.confirm({
    title: t('gui.git.hold.apply_title'),
    message: t('gui.git.hold.apply_message', { target: target.value, action: actionLabel.value }),
    confirmLabel: t('gui.git.hold.apply'),
    run: () => void session.applyNow(),
  })
}
</script>
