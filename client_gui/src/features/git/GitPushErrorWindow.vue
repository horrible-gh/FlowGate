<template>
  <ModelessShell
    :open="true"
    :title="win.title"
    :minimized="win.minimized"
    :initial-width="400"
    :initial-height="280"
    @update:minimized="(value: boolean) => session.setWindowMinimized(win.id, value)"
    @close="session.closeWindow(win.id)"
  >
    <div class="gx-window" data-test="git-push-window">
      <dl v-if="failure" class="gx-tech">
        <dt>{{ t('gui.git.window.code') }}</dt>
        <dd data-test="git-push-code">{{ failure.code }}</dd>
        <dt>{{ t('gui.git.window.push_target') }}</dt>
        <dd data-test="git-push-target">{{ failure.target || t('gui.git.header.none') }}</dd>
        <dt>{{ t('gui.git.window.at') }}</dt>
        <dd data-test="git-push-at">{{ at }}</dd>
        <dt>{{ t('gui.git.window.diagnostic') }}</dt>
        <dd data-test="git-push-diagnostic">{{ t('gui.git.window.no_diagnostic') }}</dd>
      </dl>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — remote apply failure details (0033-D §6.2): only what the server really
// gives — the error code — plus the record's target and time. The server keeps the git stderr
// in its log only, so diagnostics read "not provided" (G2); its `message` is not UI text.
// There is no retry here: `다시 반영` stays on the card.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import { useGitPanel } from './gitSession'
import type { GitWindowDescriptor } from './types'

defineProps<{ win: GitWindowDescriptor }>()
const { t, locale } = useI18n()
const session = useGitPanel().session

const failure = computed(() => session.state.pushFailure)
const at = computed(() => {
  const value = failure.value?.at
  if (!value) return t('gui.git.header.none')
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString(locale.value)
})
</script>
