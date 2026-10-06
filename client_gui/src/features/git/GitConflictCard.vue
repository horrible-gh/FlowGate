<template>
  <section class="gx-card danger" data-test="git-conflict-card">
    <div class="gx-title">
      {{ t('gui.git.conflict.title') }}<template v-if="!unsupported && files"> · {{ t('gui.git.files_count', { n: files.length }) }}</template>
    </div>
    <p v-if="unsupported" class="gx-desc" data-test="git-conflict-unsupported">{{ t('gui.git.conflict.unsupported') }}</p>
    <template v-else>
      <div v-if="files && files.length" class="gx-files">
        <GitFileRow
          v-for="file in preview"
          :key="file.path"
          :path="file.path"
          letter="C"
          @select="ctx.openDiff('conflict', file.path, 'C')"
        />
        <button
          v-if="files.length > preview.length"
          type="button"
          class="gx-files-more"
          data-test="git-conflict-more"
          @click="openWindow"
        >
          {{ t('gui.git.files_more', { n: files.length - preview.length }) }}
        </button>
      </div>
      <p v-else-if="session.state.conflictLoad === 'loading'" class="gx-desc">{{ t('gui.git.conflict.loading') }}</p>
      <div class="gx-actions">
        <button type="button" class="gx-btn ai" :disabled="!actionable" data-test="git-conflict-ai" @click="session.startAi('conflict')">
          🤖 {{ t('gui.git.conflict.ai') }}
        </button>
        <button type="button" class="gx-btn danger" :disabled="!actionable" data-test="git-conflict-manual" @click="openWindow">
          {{ t('gui.git.conflict.manual') }}
        </button>
        <button type="button" class="gx-btn ghost" :disabled="!actionable" data-test="git-conflict-abort" @click="askAbort">
          {{ t('gui.git.conflict.abort') }}
        </button>
      </div>
    </template>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — merge conflict card (0033-D §3.2). `unsupported` is the TR-revert (or
// unknown) conflict this screen does not process: title and one line, no buttons (§3.7).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import { FILE_PREVIEW, useGitPanel } from './gitSession'

defineProps<{ unsupported?: boolean }>()
const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const files = computed(() => session.state.conflict?.files ?? null)
const preview = computed(() => (files.value ?? []).slice(0, FILE_PREVIEW))
// No action before the conflicts answer says this is an ordinary merge, not a TR conflict;
// readOnly / 403 / busy turn all three off, `직접 해결` included (0033-D §5.1). `외 N개 보기`
// stays: it only shows the list.
const actionable = computed(() => session.canAct.value && session.conflictActionable.value)

const noticeText = computed(() => {
  const notice = session.state.notices.conflict
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function openWindow(): void {
  session.openConflictWindow(t('gui.git.window.conflict_title'))
}

// Abort drops the final approval intent with it; the confirm text says so (§3.2).
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
