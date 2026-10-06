<template>
  <ModelessShell
    :open="true"
    :title="win.title"
    :minimized="win.minimized"
    :initial-width="440"
    :initial-height="460"
    @update:minimized="(value: boolean) => session.setWindowMinimized(win.id, value)"
    @close="session.closeWindow(win.id)"
  >
    <div class="gx-window" data-test="git-conflict-window">
      <div class="gx-window-hero">
        <div class="gx-kicker">{{ t('gui.git.window.conflict_heading') }}</div>
        <p class="gx-desc">{{ t('gui.git.window.conflict_desc') }}</p>
      </div>
      <div class="gx-actions">
        <button
          type="button"
          class="gx-btn ai"
          :disabled="resolving || !session.canAct.value || !session.conflictActionable.value"
          data-test="git-window-ai"
          @click="session.startAi('window')"
        >
          🤖 {{ resolving ? t('gui.git.window.ai_running') : t('gui.git.window.ai') }}
        </button>
        <button type="button" class="gx-btn danger" data-test="git-window-manual" @click="showManual">
          {{ t('gui.git.window.manual') }}
        </button>
      </div>
      <button type="button" class="gx-btn ghost sm" data-test="git-window-tech" @click="techOpen = !techOpen">
        {{ techOpen ? t('gui.git.window.tech_hide') : t('gui.git.window.tech_show') }}
      </button>
      <dl v-if="techOpen" class="gx-tech" data-test="git-window-tech-info">
        <dt>{{ t('gui.git.window.target') }}</dt>
        <dd data-test="git-window-target">{{ target }}</dd>
        <dt>{{ t('gui.git.window.files') }}</dt>
        <dd data-test="git-window-count">{{ conflict ? t('gui.git.window.files_value', { n: conflict.files.length }) : t('gui.git.header.none') }}</dd>
        <dt>{{ t('gui.git.window.branch') }}</dt>
        <dd data-test="git-window-branch">{{ conflict?.branch || t('gui.git.header.none') }}</dd>
      </dl>
      <div v-if="filesOpen && conflict && conflict.files.length" class="gx-files" data-test="git-window-files">
        <GitFileRow
          v-for="file in conflict.files"
          :key="file.path"
          :path="file.path"
          :letter="file.conflictCount === 0 ? 'M' : 'C'"
          @select="ctx.openDiff('conflict', file.path, file.conflictCount === 0 ? 'M' : 'C')"
        />
      </div>
      <p v-if="session.state.conflictLoad === 'loading'" class="gx-desc">{{ t('gui.git.conflict.loading') }}</p>
      <p v-if="noticeText" class="gx-notice" data-test="git-notice">{{ noticeText }}</p>
    </div>
  </ModelessShell>
</template>

<script setup lang="ts">
// flowgate.v02.0033 — conflict resolution modeless (0033-D §6.3) on the non-blocking
// ModelessShell. Opened by `직접 해결`, `외 N개 보기` and `진행 보기`. `직접 해결` here unfolds the
// technical details and the full conflict file list (v7); a marker editor and resolve
// submission are not part of this group. Minimized state is the session's (§6.4).
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '../../components/ui/ModelessShell.vue'
import GitFileRow from './GitFileRow.vue'
import { useGitPanel } from './gitSession'
import type { GitWindowDescriptor } from './types'

const props = defineProps<{ win: GitWindowDescriptor }>()
const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session

const techOpen = ref(false)
const filesOpen = ref(false)

const conflict = computed(() => {
  const value = session.state.conflict
  return value && value.mergeId === props.win.mergeId ? value : null
})
const resolving = computed(() => session.groupCard.value === 'ai')
const target = computed(() => conflict.value?.targetBranch ?? conflict.value?.baseBranch ?? t('gui.git.header.none'))

const noticeText = computed(() => {
  const notice = session.state.notices.window
  return notice ? t(`gui.git.notice.${notice.code}`, { ...(notice.params ?? {}) }) : null
})

function showManual(): void {
  techOpen.value = true
  filesOpen.value = true
}
</script>
