<template>
  <section class="gx-card ai" data-test="git-ai-card">
    <div class="gx-title">
      🤖 {{ t('gui.git.ai.title') }}<template v-if="files"> · <span data-test="git-ai-progress">{{ done }} / {{ files.length }}</span></template>
    </div>
    <div class="gx-actions">
      <button type="button" class="gx-btn" :title="t('gui.git.ai.watch')" :aria-label="t('gui.git.ai.watch')" data-test="git-ai-watch" @click="openWindow">{{ t('gui.git.ai.watch') }}</button>
      <button type="button" class="gx-btn ghost" :disabled="!session.canAct.value" :title="t('gui.git.ai.stop')" :aria-label="t('gui.git.ai.stop')" data-test="git-ai-stop" @click="session.cancelAi()">
        ■ {{ t('gui.git.ai.stop') }}
      </button>
    </div>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// AI resolving card. Progress is the conflicts answer's
// files whose server-counted markers reached 0 over all files — counted, never estimated
// (an AI that submits everything at once shows 0 until the end, G4).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitPanel } from './gitSession'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const files = computed(() => session.state.conflict?.files ?? null)
const done = computed(() => (files.value ?? []).filter((f) => f.conflictCount === 0).length)

// A file with no markers left reads as modified (v7 mock-up), one still marked as conflicted.

const noticeText = computed(() => {
  const notice = session.state.notices.conflict
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function openWindow(): void {
  session.openConflictWindow(t('gui.git.window.conflict_title'))
}
</script>
