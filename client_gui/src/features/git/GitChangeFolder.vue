<template>
  <div class="gw-folder" data-test="git-folder" :data-folder="folder.folder" :data-open="folder.open ? 'true' : 'false'">
    <button
      type="button"
      class="gw-folder-head"
      data-test="git-folder-toggle"
      :aria-expanded="folder.open ? 'true' : 'false'"
      :title="folder.folder || t('gui.git.worktree.root_folder')"
      @click="wt.toggleFolder(folder.folder, folder.open)"
    >
      <span class="gw-caret" aria-hidden="true">{{ folder.open ? '▾' : '▸' }}</span>
      <span class="gw-folder-name">{{ folder.folder || t('gui.git.worktree.root_folder') }}</span>
      <span class="gw-folder-count" data-test="git-folder-count">{{ folder.count }}</span>
    </button>
    <template v-if="folder.open">
      <GitChangeRow v-for="row in folder.rows" :key="row.file.path" :row="row" />
      <button v-if="folder.remaining > 0" type="button" class="gw-more" data-test="git-folder-more" @click="wt.showMore(folder.key)">
        {{ t('gui.git.worktree.more', { n: folder.remaining, page: WORKTREE_FOLDER_PAGE }) }}
      </button>
    </template>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — one folder of a section: fold / unfold, its matching file count, the rows up
// to the display limit and `N개 더 있음 · 더 보기` adding one page (0066 D#2 §3-6(2)).
import { useI18n } from 'vue-i18n'
import GitChangeRow from './GitChangeRow.vue'
import { useGitWorktree } from './gitWorktreeSession'
import { WORKTREE_FOLDER_PAGE, type WorktreeFolderView } from './worktreeList'

defineProps<{ folder: WorktreeFolderView }>()

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()
</script>
