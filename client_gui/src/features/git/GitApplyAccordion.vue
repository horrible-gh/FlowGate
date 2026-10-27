<template>
  <div class="gw-apply" data-test="git-apply">
    <template v-if="workspace">
      <div class="gw-files-stats" data-test="git-apply-stats">
        <span>{{ t('gui.git.worktree.status.ahead') }}<strong data-test="git-apply-ahead">{{ ahead ?? '—' }}</strong></span>
        <span v-if="checking" class="gw-progress" data-test="git-apply-checking">{{ t('gui.git.apply.checking') }}</span>
      </div>
      <div class="gw-merge-field" data-test="git-apply-target-row">
        <label :for="selectId">{{ t('gui.git.apply.target_label') }}</label>
        <select
          :id="selectId"
          class="gw-select"
          data-test="git-apply-target"
          :value="chosen ?? ''"
          :disabled="!targets.length"
          @change="onTarget(($event.target as HTMLSelectElement).value)"
        >
          <option v-if="!chosen" value="" disabled>{{ t('gui.git.apply.target_none') }}</option>
          <option v-for="target in targets" :key="target.name" :value="target.name">{{ targetLabel(target) }}</option>
        </select>
        <button
          type="button"
          class="gw-iconbtn gw-fetch"
          data-test="git-apply-fetch"
          :disabled="reasons.fetch.disabled"
          :aria-label="t('gui.git.apply.fetch')"
          :title="tip('fetch', 'gui.git.apply.fetch_tip')"
          @click="wt.fetchRemote()"
        >⟳</button>
      </div>
      <label class="gw-merge-switch-row" data-test="git-apply-push-row" :for="switchId">
        <span>{{ t('gui.git.apply.push_after') }}</span>
        <span class="gw-merge-switch">
          <input
            :id="switchId"
            type="checkbox"
            role="switch"
            data-test="git-apply-push-after"
            :checked="wt.state.pushAfterMerge"
            @change="wt.setPushAfterMerge(($event.target as HTMLInputElement).checked)"
          />
          <span class="gw-merge-switch-track" aria-hidden="true" />
        </span>
      </label>
      <p v-for="warning in warnings" :key="warning" class="gw-line is-warn" data-test="git-apply-warning">⚠ {{ warning }}</p>
      <button
        type="button"
        class="gw-act primary gw-merge-submit"
        data-test="git-apply-merge"
        :disabled="reasons.merge.disabled"
        :title="tip('merge', null)"
        @click="onMerge"
      >{{ wt.state.pushAfterMerge ? t('gui.git.apply.merge_push') : t('gui.git.apply.merge') }}</button>
      <p v-if="mergeReason" class="gw-line is-muted" data-test="git-apply-reason">{{ mergeReason }}</p>
    </template>
    <div v-else class="gw-merge-field">
      <span class="gw-merge-field-wide gw-line is-muted" data-test="git-apply-unavailable">{{ t('gui.git.reason.worktree_unavailable') }}</span>
      <button
        type="button"
        class="gw-iconbtn gw-fetch"
        data-test="git-apply-fetch"
        :disabled="reasons.fetch.disabled"
        :aria-label="t('gui.git.apply.fetch')"
        :title="tip('fetch', 'gui.git.apply.fetch_tip')"
        @click="wt.fetchRemote()"
      >⟳</button>
    </div>
    <p v-if="notice" class="gw-line" :class="{ 'is-error': notice.tone === 'error' }" data-test="git-apply-notice">
      <span>{{ t(`gui.git.worktree.notice.${notice.code}`, { ...(notice.params ?? {}) }) }}</span>
      <button type="button" class="gw-link" :aria-label="t('gui.git.worktree.dismiss')" @click="wt.dismissNotice('apply')">×</button>
    </p>
    <p v-if="rejectLine" class="gw-line" data-test="git-apply-reject-hint">{{ rejectLine }}</p>
    <GitMergeResultPush />
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the Base apply part (0066 D#2 §3-4, §6-4): the merge target (E8
// `targets[]` only, never a typed or fixed branch), the small Fetch icon (E12 only), `머지 후 Push`,
// the `머지` / `머지 + Push` button with its one reason, and the follow-up Push part. No commit and
// no approval button: committing is the commit line's, approving is the approval flow's. Without a
// workspace (W2) only Fetch and the follow-up Push stay. Every value is a server answer.
// flowgate.v02.0067 T#1 — it is the `Base 반영` tab body of the Git modeless; its `Diff` / `상태`
// panes left for the window's own `Diff` / `상태` tabs (GitApplyInfo, GitWorktreeStatus).
// flowgate.v02.0067 T#3 — the v27 `Base 반영` body (R §5, `modalMergeHTML`), no longer folded: the
// `미반영 커밋` count alone on top (the same E8 number as the Git tab), the merge target and the
// `머지 후 Push` switch (ON/OFF, OFF at first) on one label column and one input start column, then
// one full-width `머지` / `머지 + Push`. Only the targets E8 admits are listed (mergeTargetChoices —
// internal work branches by the server's `internal_slot`, never by a name prefix). The merge
// judgment, the dialogs, Fetch and the follow-up Push are the session's, unchanged.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitMergeResultPush from './GitMergeResultPush.vue'
import { useGitWorktree } from './gitWorktreeSession'
import type { ManualMergeTarget } from './types'
import { mergeTargetChoices, reasonMessage, type WorktreeButton } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt, confirm } = useGitWorktree()

const uid = Math.random().toString(36).slice(2, 8)
const selectId = `gw-merge-target-${uid}`
const switchId = `gw-merge-push-${uid}`
const workspace = computed(() => wt.state.status?.worktreeAvailable === true)
const reasons = computed(() => wt.reasons.value)
const targets = computed(() => mergeTargetChoices(wt.state.mergeTargets as ManualMergeTarget[]))
const checking = computed(() => wt.state.mergeLoad === 'loading')
const notice = computed(() => wt.state.notices.apply ?? null)

// The chosen target as the choice shows it: one the list does not offer reads as "대상 없음".
const chosen = computed(() => {
  const target = wt.state.mergeTarget
  return target && targets.value.some((x) => x.name === target) ? target : null
})

// The E8 count of the chosen target (0066 D#2 §6-1), as on the Git tab: unknown while it is void
// or being checked.
const ahead = computed(() => {
  if (checking.value) return null
  const merge = wt.state.merge
  if (!merge || !wt.state.mergeTarget) return null
  return merge.aheadOfTarget
})

// "(기본)" on the work base.
function targetLabel(target: ManualMergeTarget): string {
  return target.isWorkBase ? t('gui.git.apply.target_default', { name: target.name }) : target.name
}

function onTarget(value: string): void {
  if (value) wt.setMergeTarget(value)
}

const WARNING_KEYS: Record<string, string> = {
  target_differs_from_work_base: 'gui.git.apply.warning.target_differs_from_work_base',
}

// The E8 preview warnings of a target other than the work base, one line each (§3-4(2)).
const warnings = computed(() => {
  const merge = wt.state.merge
  const target = wt.state.mergeTargets.find((x) => x.name === wt.state.mergeTarget)
  if (!merge?.preview || !target || target.isWorkBase) return []
  return merge.preview.warnings.filter((w) => w in WARNING_KEYS).map((w) => t(WARNING_KEYS[w]))
})

// 0067 R §1 / §2-5: "nothing to merge" and "no target" only repeat the count and the choice above,
// so they stay in the button's tooltip; every other reason keeps its one line.
const QUIET_REASONS: readonly string[] = ['nothing_to_merge', 'no_target']

const mergeReason = computed(() => {
  if (QUIET_REASONS.includes(reasons.value.merge.code ?? '')) return null
  const message = reasonMessage(reasons.value.merge)
  return message ? t(message.key, message.params) : null
})

// §3-4(4-1): the line the re-read judgment settled after a refused push.
const rejectLine = computed(() => {
  const target = wt.state.mergeTarget ?? ''
  if (wt.state.rejectHint === 'recovery') return t('gui.git.apply.reject_recovery', { target })
  if (wt.state.rejectHint === 'rewound') return t('gui.git.apply.reject_rewound', { target })
  return null
})

function tip(button: WorktreeButton, help: string | null): string | undefined {
  const message = reasonMessage(reasons.value[button])
  if (message) return t(message.key, message.params)
  return help ? t(help) : undefined
}

const CONFIRM_KEYS = {
  merge: 'gui.git.apply.merge_confirm',
  mergePush: 'gui.git.apply.merge_push_confirm',
  recovery: 'gui.git.apply.recovery_confirm',
  recoveryPush: 'gui.git.apply.recovery_push_confirm',
} as const

// The dialog shows the fixed judgment (count, target, recovery) and closes if the target changes;
// it sends nothing once another judgment is on screen (⑥).
function onMerge(): void {
  const snapshot = wt.mergeSnapshot()
  if (!snapshot) return
  const key = snapshot.recovery
    ? snapshot.push
      ? CONFIRM_KEYS.recoveryPush
      : CONFIRM_KEYS.recovery
    : snapshot.push
      ? CONFIRM_KEYS.mergePush
      : CONFIRM_KEYS.merge
  const label = snapshot.push ? t('gui.git.apply.merge_push') : t('gui.git.apply.merge')
  confirm(
    {
      title: label,
      message: t(key, { n: snapshot.ahead, target: snapshot.target }),
      confirmLabel: label,
      run: () => void wt.merge(snapshot),
    },
    snapshot.ticket,
    () => wt.state.mergeTarget === snapshot.target,
  )
}
</script>
