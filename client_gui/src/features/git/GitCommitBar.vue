<template>
  <div class="gw-commit" data-test="git-commit-bar">
    <div class="gw-commit-row">
      <input
        type="text"
        class="gw-input gw-commit-input"
        data-test="git-commit-message"
        :placeholder="t('gui.git.worktree.message_placeholder')"
        :aria-label="t('gui.git.worktree.message_placeholder')"
        :maxlength="WORKTREE_MESSAGE_MAX"
        :value="wt.state.message"
        :disabled="inputDisabled"
        :title="inputDisabled ? tip(reasons.commit, null) : undefined"
        @input="wt.setMessage(($event.target as HTMLInputElement).value)"
        @keydown.enter.prevent
      />
      <button
        type="button"
        class="gw-ai"
        data-test="git-commit-ai"
        :disabled="aiReason.disabled"
        :aria-label="t('gui.git.worktree.ai')"
        :title="tip(aiReason, 'gui.git.worktree.ai')"
        @click="onSuggest"
      >🤖</button>
      <button
        type="button"
        class="gw-act primary gw-commit-btn"
        data-test="git-commit"
        :disabled="reasons.commit.disabled"
        :title="tip(reasons.commit, null)"
        @click="onCommit"
      >{{ staged ? t('gui.git.worktree.commit_count', { n: staged }) : t('gui.git.worktree.commit') }}</button>
    </div>
    <p v-if="notice" class="gw-line" :class="{ 'is-error': notice.tone === 'error' }" data-test="git-commit-notice">
      <span>{{ t(`gui.git.worktree.notice.${notice.code}`, { ...(notice.params ?? {}) }) }}</span>
      <button
        v-if="notice.link === 'unstage_all'"
        type="button"
        class="gw-link"
        data-test="git-commit-unstage-all"
        :disabled="reasons.unstageAll.disabled"
        @click="wt.unstageAll()"
      >{{ t('gui.git.worktree.unstage_all') }}</button>
      <button type="button" class="gw-link" :aria-label="t('gui.git.worktree.dismiss')" @click="wt.dismissNotice('commit')">×</button>
    </p>
    <p v-else-if="suggestionLine" class="gw-line" data-test="git-commit-suggestion">{{ suggestionLine }}</p>
    <p v-else-if="commitReason" class="gw-line is-muted" data-test="git-commit-reason">{{ commitReason }}</p>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — the commit line outside the accordion (0066 D#2 §6-3): one-line message,
// AI 🤖 draft, `커밋 · M`. Always in the same place, folded list or not. Enter never commits; the
// commit asks first with the E1 count and sends the index token that dialog showed (§3-3). Both
// dialogs are bound to the context they were opened in (the snapshot / draft ticket).
//
// flowgate.v02.0067 T#2 — the bottom of the `파일 관리` tab (0067-R §4-5): with no Stage, or while the
// commit cannot run for any reason but an empty message, the message, 🤖 and 커밋 are all disabled;
// an empty message blocks 커밋 only. No Stage is not worth a line of its own (the button tooltip
// still says it); other reasons keep their short line. The shared reasons stay as they are; this
// area alone lets a commit block (empty message aside) close 🤖 too, with the commit's reason.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree, WORKTREE_MESSAGE_MAX } from './gitWorktreeSession'
import { reasonMessage, type WorktreeReason } from './worktreeReasons'

const { t } = useI18n()
const { worktree: wt, confirm } = useGitWorktree()

const reasons = computed(() => wt.reasons.value)
const staged = computed(() => wt.state.status?.counts.staged ?? 0)
const notice = computed(() => wt.state.notices.commit ?? null)

const suggestionLine = computed(() => {
  const suggestion = wt.state.suggestion
  if (!suggestion) return null
  const lines: string[] = []
  if (suggestion.inputTruncated) lines.push(t('gui.git.worktree.suggestion_truncated'))
  if (wt.suggestionStale.value) lines.push(t('gui.git.worktree.suggestion_stale'))
  return lines.length ? lines.join(' · ') : null
})

const commitReason = computed(() => {
  if (reasons.value.commit.code === 'no_staged') return null
  const message = reasonMessage(reasons.value.commit)
  return message ? t(message.key, message.params) : null
})

// The commit is blocked by anything but the missing message (no Stage, read-only, 403, running, a
// session, an E1 commit blocker, protected Stage): the whole area is closed for that reason.
const areaBlock = computed<WorktreeReason | null>(() => {
  const commit = reasons.value.commit
  return commit.disabled && commit.code !== 'no_message' ? commit : null
})

// Typing is open exactly when only the message itself is missing (or nothing blocks).
const inputDisabled = computed(() => areaBlock.value !== null)

// 🤖 follows the area block first, then its own reason (no default AI, a suggest blocker).
const aiReason = computed(() => areaBlock.value ?? reasons.value.ai)

function tip(reason: WorktreeReason, help: string | null): string | undefined {
  const message = reasonMessage(reason)
  if (message) return t(message.key, message.params)
  return help ? t(help) : undefined
}

function onCommit(): void {
  const snapshot = wt.commitSnapshot()
  if (!snapshot) return
  confirm(
    {
      title: t('gui.git.worktree.commit_title'),
      message: t('gui.git.worktree.commit_message', { n: snapshot.count }),
      confirmLabel: t('gui.git.worktree.commit'),
      run: () => void wt.commit(snapshot),
    },
    snapshot.ticket,
  )
}

// A typed message is replaced only after asking (§3-3 AI 🤖).
function onSuggest(): void {
  const ticket = wt.ticket()
  if (aiReason.value.disabled || !ticket) return
  if (!wt.state.message.trim()) {
    void wt.suggest(ticket)
    return
  }
  confirm(
    {
      title: t('gui.git.worktree.ai_replace_title'),
      message: t('gui.git.worktree.ai_replace_message'),
      confirmLabel: t('gui.git.worktree.ai_replace_ok'),
      run: () => void wt.suggest(ticket),
    },
    ticket,
  )
}
</script>
