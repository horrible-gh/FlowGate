<template>
  <section class="gx-card danger" data-test="git-review-card">
    <div class="gx-title">
      {{ t('gui.git.review.title') }}<template v-if="review"> · {{ t('gui.git.files_count', { n: review.changes.length }) }}</template>
    </div>
    <template v-if="review">
      <div v-if="review.changes.length" class="gx-files">
        <GitFileRow
          v-for="change in preview"
          :key="change.path"
          :path="change.path"
          :letter="letterOf(change.status)"
          @select="ctx.openDiff('review', change.path, letterOf(change.status))"
        />
        <div v-if="review.changes.length > preview.length" class="gx-files-more is-static">
          {{ t('gui.git.files_more', { n: review.changes.length - preview.length }) }}
        </div>
      </div>
      <p class="gx-desc" data-test="git-review-resolver">
        {{ review.resolverProvider ? t('gui.git.review.resolver', { name: review.resolverProvider }) : t('gui.git.review.resolver_manual') }}
      </p>
    </template>
    <p v-else-if="session.state.reviewLoad === 'loading'" class="gx-desc">{{ t('gui.git.review.loading') }}</p>
    <p v-if="stateLine" class="gx-kicker" data-test="git-review-state">{{ stateLine }}</p>

    <div v-if="session.state.rejectOpen" class="gx-reject">
      <label class="gx-reject-label" for="gx-reject-reason">{{ t('gui.git.review.reject_label') }}</label>
      <textarea
        id="gx-reject-reason"
        class="gx-input gx-textarea"
        :value="session.state.rejectReason"
        :placeholder="t('gui.git.review.reject_placeholder')"
        :maxlength="REJECT_REASON_MAX"
        rows="3"
        data-test="git-reject-reason"
        @input="session.setRejectReason(($event.target as HTMLTextAreaElement).value)"
      />
      <div class="gx-actions">
        <button
          type="button"
          class="gx-btn danger"
          :disabled="!canReject || !session.state.rejectReason.trim()"
          data-test="git-reject-submit"
          @click="session.reject()"
        >
          {{ t('gui.git.review.reject_submit') }}
        </button>
        <button type="button" class="gx-btn ghost" data-test="git-reject-close" @click="session.openReject(false)">
          {{ t('gui.git.review.reject_close') }}
        </button>
      </div>
    </div>
    <div v-else class="gx-actions">
      <button type="button" class="gx-btn primary" :disabled="!canApprove" data-test="git-review-approve" @click="askApprove">
        {{ t('gui.git.review.approve') }}
      </button>
      <button type="button" class="gx-btn danger" :disabled="!canReject" data-test="git-review-reject" @click="session.openReject(true)">
        {{ t('gui.git.review.reject') }}
      </button>
      <button type="button" class="gx-btn ghost" :disabled="!canAbort" data-test="git-review-abort" @click="askAbort">
        {{ t('gui.git.review.abort') }}
      </button>
    </div>
    <p v-if="review && review.canReject && !session.defaultProviderId.value" class="gx-reason" data-test="git-review-no-provider">
      {{ t('gui.git.review.no_provider') }}
    </p>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — merge awaiting approval card (0033-D §3.7, Q 394). Filled from GET
// review; approve / reject availability is the server's can_approve / can_reject as-is.
// Reject pins the project's default AI (no picker in R/v7) and is disabled without one.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import { FILE_PREVIEW, REJECT_REASON_MAX, useGitPanel } from './gitSession'
import type { GitFileLetter } from './types'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const LETTERS: readonly GitFileLetter[] = ['C', 'A', 'D', 'M', '?']
const RECONCILE_REASONS = [
  'push_rejected',
  'push_remote_third',
  'push_remote_unknown',
  'apply_restore_failed',
  'restoration_verification_failed',
]

const review = computed(() => session.state.review)
const preview = computed(() => (review.value?.changes ?? []).slice(0, FILE_PREVIEW))
const reviewState = computed(() => review.value?.reviewState ?? session.pendingRow.value?.reviewState ?? null)
// applying / reconciling: every button is off until the server settles it.
const settling = computed(() => reviewState.value === 'applying' || reviewState.value === 'reconciling')

const letterOf = (status: string): GitFileLetter | null => LETTERS.find((l) => l === status.slice(0, 1)) ?? null

const canApprove = computed(
  () => session.canAct.value && !settling.value && !!review.value?.canApprove && !!review.value.fingerprint,
)
const canReject = computed(
  () => session.canAct.value && !settling.value && !!review.value?.canReject && !!session.defaultProviderId.value,
)
const canAbort = computed(() => session.canAct.value && !settling.value)

// Fixed text per reconciliation kind; the server's last_error is never shown (§3.7).
const stateLine = computed(() => {
  if (reviewState.value === 'applying') return t('gui.git.review.applying')
  const kind = review.value?.reconciliationKind ?? null
  const reason = kind && RECONCILE_REASONS.includes(kind) ? kind : 'generic'
  if (reviewState.value === 'reconciling') return `${t('gui.git.review.reconciling')} — ${t(`gui.git.review.reason.${reason}`)}`
  if (reviewState.value === 're_review') return t(`gui.git.review.reason.${reason}`)
  return null
})

const noticeText = computed(() => {
  const notice = session.state.notices.review
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function targetBranch(): string {
  const conflict = session.state.conflict
  return conflict?.targetBranch ?? conflict?.baseBranch ?? session.state.status?.baseBranch ?? t('gui.git.header.base_unset')
}

function askApprove(): void {
  ctx.confirm({
    title: t('gui.git.review.approve_title'),
    message: t('gui.git.review.approve_message', { target: targetBranch(), n: review.value?.changes.length ?? 0 }),
    confirmLabel: t('gui.git.review.approve'),
    run: () => void session.approve(),
  })
}

function askAbort(): void {
  const bound = session.pendingRow.value?.finalApprovalBound === true
  ctx.confirm({
    title: t('gui.git.conflict.abort_title'),
    message: bound ? `${t('gui.git.conflict.abort_message')} ${t('gui.git.conflict.abort_bound')}` : t('gui.git.conflict.abort_message'),
    confirmLabel: t('gui.git.conflict.abort'),
    danger: true,
    run: () => void session.abortMerge(),
  })
}
</script>
