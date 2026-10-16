<template>
  <div class="gw-apply" data-test="git-apply" :data-open="open ? 'true' : 'false'">
    <button
      type="button"
      class="gw-apply-toggle"
      data-test="git-apply-toggle"
      :aria-expanded="open ? 'true' : 'false'"
      @click="wt.toggleApply()"
    >
      <span class="gw-apply-title">↗ {{ t('gui.git.apply.title') }}</span>
      <span v-if="summary" class="gw-apply-status" data-test="git-apply-status">{{ summary }}</span>
      <span class="gw-caret" aria-hidden="true">{{ open ? '▴' : '▾' }}</span>
    </button>

    <div v-if="open" class="gw-apply-body" data-test="git-apply-body">
      <template v-if="workspace">
        <div class="gw-merge-target">
          <label :for="selectId">{{ t('gui.git.apply.target_label') }}</label>
          <select
            :id="selectId"
            class="gw-select"
            data-test="git-apply-target"
            :aria-label="t('gui.git.apply.target_aria')"
            :value="wt.state.mergeTarget ?? ''"
            :disabled="!targets.length"
            @change="onTarget(($event.target as HTMLSelectElement).value)"
          >
            <option v-if="!wt.state.mergeTarget" value="" disabled>{{ t('gui.git.apply.target_none') }}</option>
            <option v-for="target in targets" :key="target.name" :value="target.name" :disabled="!target.eligible">
              {{ targetLabel(target) }}
            </option>
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
        <p v-for="warning in warnings" :key="warning" class="gw-line is-warn" data-test="git-apply-warning">⚠ {{ warning }}</p>
        <div class="gw-merge-options">
          <label>
            <input
              type="checkbox"
              data-test="git-apply-push-after"
              :checked="wt.state.pushAfterMerge"
              @change="wt.setPushAfterMerge(($event.target as HTMLInputElement).checked)"
            />
            <span>{{ t('gui.git.apply.push_after') }}</span>
          </label>
          <button
            type="button"
            class="gw-mini-link"
            :class="{ 'is-on': wt.state.infoPane === 'diff' }"
            data-test="git-apply-diff"
            :aria-pressed="wt.state.infoPane === 'diff' ? 'true' : 'false'"
            @click="wt.toggleInfo('diff')"
          >{{ t('gui.git.apply.diff') }}</button>
          <button
            type="button"
            class="gw-mini-link"
            :class="{ 'is-on': wt.state.infoPane === 'status' }"
            data-test="git-apply-state"
            :aria-pressed="wt.state.infoPane === 'status' ? 'true' : 'false'"
            @click="wt.toggleInfo('status')"
          >{{ t('gui.git.apply.status') }}</button>
        </div>
        <GitApplyInfo v-if="wt.state.infoPane" />
        <div class="gw-apply-actions">
          <button
            type="button"
            class="gw-act primary"
            data-test="git-apply-merge"
            :disabled="reasons.merge.disabled"
            :title="tip('merge', null)"
            @click="onMerge"
          >{{ wt.state.pushAfterMerge ? t('gui.git.apply.merge_push') : t('gui.git.apply.merge') }}</button>
          <span v-if="checking" class="gw-progress" data-test="git-apply-checking">{{ t('gui.git.apply.checking') }}</span>
        </div>
        <p v-if="mergeReason" class="gw-line is-muted" data-test="git-apply-reason">{{ mergeReason }}</p>
        <p v-if="artifacts" class="gw-line is-muted" data-test="git-apply-artifacts">{{ t('gui.git.apply.artifacts_info', { n: artifacts }) }}</p>
      </template>
      <div v-else class="gw-merge-target">
        <span class="gw-grow gw-line is-muted">{{ t('gui.git.apply.fetch_only') }}</span>
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
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 T#7 — the folded `Base 반영` accordion (0066 D#2 §3-4, §6-4). Its header says
// one word of state even folded; opened: the merge target (E8 `targets[]` only, never a typed or
// fixed branch), the small Fetch icon (E12 only), `머지 후 Push`, the `Diff` / `상태` panes, the
// `머지` / `머지 + Push` button with its one reason, and the follow-up Push part. No commit and no
// approval button: committing is the commit line's, approving is the approval flow's. Without a
// workspace (W2) only Fetch and the follow-up Push stay. Every value is a server answer.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitApplyInfo from './GitApplyInfo.vue'
import GitMergeResultPush from './GitMergeResultPush.vue'
import { useGitWorktree, type WorktreeApplySummary } from './gitWorktreeSession'
import type { ManualMergeTarget } from './types'
import { reasonMessage, type WorktreeButton } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt, confirm } = useGitWorktree()

const selectId = `gw-merge-target-${Math.random().toString(36).slice(2, 8)}`
const open = computed(() => wt.state.applyOpen)
const workspace = computed(() => wt.state.status?.worktreeAvailable === true)
const reasons = computed(() => wt.reasons.value)
const targets = computed(() => wt.state.mergeTargets as ManualMergeTarget[])
const checking = computed(() => wt.state.mergeLoad === 'loading')
const notice = computed(() => wt.state.notices.apply ?? null)

const SUMMARY_KEYS: Record<WorktreeApplySummary['kind'], string> = {
  merging: 'gui.git.apply.summary.merging',
  conflict: 'gui.git.apply.summary.conflict',
  remote_pending: 'gui.git.apply.summary.remote_pending',
  checking: 'gui.git.apply.summary.checking',
  ahead: 'gui.git.apply.summary.ahead',
  recovery: 'gui.git.apply.summary.recovery',
  nothing: 'gui.git.apply.summary.nothing',
  unknown: 'gui.git.apply.summary.unknown',
}

const summary = computed(() => {
  const s = wt.applySummary.value
  return s ? t(SUMMARY_KEYS[s.kind], { n: s.n ?? 0 }) : null
})

const TARGET_REASONS: Record<string, string> = {
  remote_only: 'gui.git.apply.target_reason.remote_only',
  internal_slot: 'gui.git.apply.target_reason.internal_slot',
  workspace_busy: 'gui.git.apply.target_reason.workspace_busy',
  owner_mismatch: 'gui.git.apply.target_reason.owner_mismatch',
}

// "(기본)" on the work base; a target that cannot be chosen says why.
function targetLabel(target: ManualMergeTarget): string {
  const name = target.isWorkBase ? t('gui.git.apply.target_default', { name: target.name }) : target.name
  if (target.eligible) return name
  const key = TARGET_REASONS[target.blockedReason ?? ''] ?? 'gui.git.apply.target_reason.unknown'
  return `${name} — ${t(key)}`
}

function onTarget(value: string): void {
  if (value) wt.setMergeTarget(value)
}

const WARNING_KEYS: Record<string, string> = {
  target_differs_from_work_base: 'gui.git.apply.warning.target_differs_from_work_base',
  unmerge_unsupported_for_non_base_target: 'gui.git.apply.warning.unmerge_unsupported_for_non_base_target',
}

// The E8 preview warnings of a target other than the work base, one line each (§3-4(2)).
const warnings = computed(() => {
  const merge = wt.state.merge
  const target = targets.value.find((x) => x.name === wt.state.mergeTarget)
  if (!merge?.preview || !target || target.isWorkBase) return []
  return merge.preview.warnings.filter((w) => w in WARNING_KEYS).map((w) => t(WARNING_KEYS[w]))
})

// New tool artifacts never block a merge: they stay in the workspace (information only).
const artifacts = computed(() => wt.state.merge?.uncommitted?.counts.excluded_new_untracked ?? 0)

const mergeReason = computed(() => {
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
