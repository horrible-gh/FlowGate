<template>
  <div class="gw-folder" data-test="git-folder" :data-folder="folder.folder" :data-open="folder.open ? 'true' : 'false'">
    <div class="gw-folder-head">
      <!-- v27 `gw-dir-select`: its own control, apart from the fold button; all / some / none of the
           folder's selectable files (folded or past the limit included). -->
      <input
        type="checkbox"
        class="gw-check gw-folder-check"
        data-test="git-folder-select"
        :checked="selection === 'all'"
        :indeterminate="selection === 'some'"
        :data-state="selection"
        :disabled="!folder.selectable.length"
        :aria-label="t('gui.git.worktree.folder_select', { folder: label })"
        @change="wt.selectPaths(folder.selectable, selection !== 'all')"
      />
      <button
        type="button"
        class="gw-folder-toggle"
        data-test="git-folder-toggle"
        :aria-expanded="folder.open ? 'true' : 'false'"
        :title="label"
        @click="wt.toggleFolder(folder.folder, folder.open)"
      >
        <span class="gw-caret" aria-hidden="true">{{ folder.open ? '▾' : '▸' }}</span>
        <span class="gw-folder-name">{{ label }}</span>
        <span class="gw-folder-count" data-test="git-folder-count">{{ folder.count }}</span>
      </button>
    </div>
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
// flowgate.v02.0067 T#2 — plus the folder checkbox (0067-R §4-2): checked when every selectable
// file of the folder is, mixed when some are; a press checks them all, or clears them when all
// were. Managed / protected files are never among them (isSelectable). It only selects: Stage and
// Stage 해제 stay the selection bar's, and each path still goes by its own `can*` flags.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitChangeRow from './GitChangeRow.vue'
import { useGitWorktree } from './gitWorktreeSession'
import { folderSelection, WORKTREE_FOLDER_PAGE, type WorktreeFolderView } from './worktreeList'

const props = defineProps<{ folder: WorktreeFolderView }>()

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()

const label = computed(() => props.folder.folder || t('gui.git.worktree.root_folder'))
const selection = computed(() => folderSelection(props.folder.selectable, wt.state.selected))
</script>
