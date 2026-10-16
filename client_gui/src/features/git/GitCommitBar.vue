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
        @input="wt.setMessage(($event.target as HTMLInputElement).value)"
        @keydown.enter.prevent
      />
      <button
        type="button"
        class="gw-ai"
        data-test="git-commit-ai"
        :disabled="reasons.ai.disabled"
        :aria-label="t('gui.git.worktree.ai')"
        :title="tip('ai', 'gui.git.worktree.ai')"
        @click="onSuggest"
      >🤖</button>
      <button
        type="button"
        class="gw-act primary gw-commit-btn"
        data-test="git-commit"
        :disabled="reasons.commit.disabled"
        :title="tip('commit', null)"
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
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree, WORKTREE_MESSAGE_MAX } from './gitWorktreeSession'
import { reasonMessage, type WorktreeButton } from './worktreeReasons'

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
  const message = reasonMessage(reasons.value.commit)
  return message ? t(message.key, message.params) : null
})

function tip(button: WorktreeButton, help: string | null): string | undefined {
  const message = reasonMessage(reasons.value[button])
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
  if (reasons.value.ai.disabled || !ticket) return
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
