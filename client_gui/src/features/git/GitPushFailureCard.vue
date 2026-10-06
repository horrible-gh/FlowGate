<template>
  <section class="gx-card danger" data-test="git-push-card">
    <div class="gx-title-row">
      <div class="gx-title">{{ t('gui.git.push.title') }}</div>
      <button
        type="button"
        class="gx-error-icon"
        :title="t('gui.git.push.detail')"
        :aria-label="t('gui.git.push.detail')"
        data-test="git-push-detail"
        @click="session.openPushErrorWindow(t('gui.git.window.push_title'))"
      >
        !
      </button>
    </div>
    <div class="gx-actions">
      <button type="button" class="gx-btn primary" :disabled="!session.canAct.value" data-test="git-push-retry" @click="session.retryApply()">
        {{ t('gui.git.push.retry') }}
      </button>
    </div>
    <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — remote apply failure card, minimal as v7 fixes it (0033-D §3.6): title,
// `!` (details window only) and `다시 반영`, which resends the recorded request as it was. No
// stderr, no long text, no cause guessed on the client.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitPanel } from './gitSession'

const { t } = useI18n()
const session = useGitPanel().session

const noticeText = computed(() => {
  const notice = session.state.notices.push
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})
</script>
