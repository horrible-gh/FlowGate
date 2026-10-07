<template>
  <section class="gx-card ai" data-test="git-ai-card">
    <div class="gx-title">
      🤖 {{ t('gui.git.ai.title') }}<template v-if="files"> · <span data-test="git-ai-progress">{{ done }} / {{ files.length }}</span></template>
    </div>
    <div v-if="files && files.length" class="gx-files">
      <GitFileRow
        v-for="file in preview"
        :key="file.path"
        :path="file.path"
        :letter="letterOf(file.conflictCount)"
        @select="ctx.openDiff('conflict', file.path, letterOf(file.conflictCount))"
      />
      <button v-if="files.length > preview.length" type="button" class="gx-files-more" @click="openWindow">
        {{ t('gui.git.files_more', { n: files.length - preview.length }) }}
      </button>
    </div>
    <div class="gx-actions">
      <button type="button" class="gx-btn" data-test="git-ai-watch" @click="openWindow">{{ t('gui.git.ai.watch') }}</button>
      <button type="button" class="gx-btn ghost" :disabled="!session.canAct.value" data-test="git-ai-stop" @click="session.cancelAi()">
        {{ t('gui.git.ai.stop') }}
      </button>
    </div>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — AI resolving card (0033-D §3.3). Progress is the conflicts answer's
// files whose server-counted markers reached 0 over all files — counted, never estimated
// (an AI that submits everything at once shows 0 until the end, G4).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitFileRow from './GitFileRow.vue'
import { FILE_PREVIEW, useGitPanel } from './gitSession'
import type { GitFileLetter } from './types'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const files = computed(() => session.state.conflict?.files ?? null)
const preview = computed(() => (files.value ?? []).slice(0, FILE_PREVIEW))
const done = computed(() => (files.value ?? []).filter((f) => f.conflictCount === 0).length)

// A file with no markers left reads as modified (v7 mock-up), one still marked as conflicted.
const letterOf = (conflictCount: number): GitFileLetter => (conflictCount === 0 ? 'M' : 'C')

const noticeText = computed(() => {
  const notice = session.state.notices.conflict
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function openWindow(): void {
  session.openConflictWindow(t('gui.git.window.conflict_title'))
}
</script>
