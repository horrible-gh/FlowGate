<template>
  <section v-if="dirty" class="gx-card warn" data-test="git-dirty-card">
    <div class="gx-title" data-test="git-dirty-title">{{ t('gui.git.dirty.title') }} · {{ countText }}</div>
    <div class="gx-files">
      <GitFileRow
        v-for="row in visibleRows"
        :key="`${row.letterKind}:${row.path}`"
        :path="row.path"
        :letter="row.letter"
        @select="ctx.openDiff('dirty', row.path, row.letter)"
      />
      <button v-if="hiddenRows > 0" type="button" class="gx-files-more" data-test="git-dirty-more" @click="expanded = true">
        {{ t('gui.git.files_more', { n: hiddenRows }) }}
      </button>
      <div v-if="dirty.hiddenUntracked > 0 && (expanded || hiddenRows === 0)" class="gx-files-more is-static" data-test="git-dirty-partial">
        {{ t('gui.git.dirty.more_partial', { n: dirty.hiddenUntracked }) }}
      </div>
    </div>
    <input
      class="gx-input"
      :value="session.state.commitMessage"
      :placeholder="t('gui.git.dirty.message')"
      :aria-label="t('gui.git.dirty.message')"
      :disabled="!actionable"
      data-test="git-dirty-message"
      @input="session.setCommitMessage(($event.target as HTMLInputElement).value)"
    />
    <div class="gx-actions">
      <button type="button" class="gx-btn primary" :disabled="!actionable" data-test="git-dirty-commit" @click="session.commit()">
        {{ t('gui.git.dirty.commit') }}
      </button>
      <button type="button" class="gx-btn danger" :disabled="!actionable" data-test="git-dirty-revert" @click="askRevert">
        {{ t('gui.git.dirty.revert') }}
      </button>
    </div>
    <p v-if="reason" class="gx-reason" data-test="git-dirty-reason">{{ reason }}</p>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — uncommitted Base changes card (0033-D §3.5, Q 396). tracked rows get
// their A/D/M letter only from the Base diff answer, asked for the rows actually drawn, once
// per git/status generation; until then (or on any failure) they show "status unknown".
// New files are `?`. Stage 1 with a cut new-file list shows "N + 200개 이상" and keeps commit /
// revert off: the received part is never sent as if it were everything.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import { FILE_PREVIEW, useGitPanel } from './gitSession'
import type { GitFileLetter } from './types'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const dirty = computed(() => session.dirtyView.value)
const expanded = ref(false)

interface Row {
  path: string
  letter: GitFileLetter | null
  letterKind: 'tracked' | 'untracked'
}

const rows = computed<Row[]>(() => {
  const view = dirty.value
  if (!view) return []
  return [
    ...view.tracked.map((path) => ({ path, letter: session.state.fileStatus[path] ?? null, letterKind: 'tracked' as const })),
    ...view.untracked.map((path) => ({ path, letter: '?' as const, letterKind: 'untracked' as const })),
  ]
})
const visibleRows = computed(() => (expanded.value ? rows.value : rows.value.slice(0, FILE_PREVIEW)))
const hiddenRows = computed(() => rows.value.length - visibleRows.value.length)

// Only drawn tracked rows are asked about, and only once per status generation.
watch(
  () => [session.state.statusGen, visibleRows.value.filter((r) => r.letterKind === 'tracked').map((r) => r.path).join('\n')] as const,
  ([, joined]) => {
    if (joined) session.ensureFileStatus(joined.split('\n'))
  },
  { immediate: true },
)

const countText = computed(() => {
  const view = dirty.value
  if (!view) return ''
  if (view.count === null) return t('gui.git.dirty.split', { n: view.tracked.length })
  return t('gui.git.files_count', { n: view.count })
})

const actionable = computed(() => session.canAct.value && !!dirty.value && !dirty.value.blockReason)

const reason = computed(() => {
  const block = dirty.value?.blockReason
  if (block === 'truncated') return t('gui.git.dirty.truncated')
  if (block === 'ai_run') return t('gui.git.dirty.ai_run')
  return null
})

const noticeText = computed(() => {
  const notice = session.state.notices.dirty
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function askRevert(): void {
  const view = dirty.value
  if (!view) return
  const untracked = view.truncated ? view.untrackedTotal ?? view.untracked.length : view.untracked.length
  const lines: string[] = []
  if (view.tracked.length) lines.push(t('gui.git.dirty.revert_tracked', { n: view.tracked.length }))
  if (untracked) lines.push(t('gui.git.dirty.revert_untracked', { n: untracked }))
  ctx.confirm({
    title: t('gui.git.dirty.revert_title'),
    message: lines.join(' '),
    confirmLabel: t('gui.git.dirty.revert'),
    danger: true,
    run: () => void session.revertAll(),
  })
}
</script>
