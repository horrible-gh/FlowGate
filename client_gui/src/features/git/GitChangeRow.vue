<template>
  <div
    class="gw-row"
    :class="{ 'is-selected': selected, 'is-locked': locked }"
    data-test="git-row"
    :data-path="row.file.path"
    :data-section="row.section"
  >
    <input
      type="checkbox"
      class="gw-check"
      data-test="git-row-select"
      :checked="selected"
      :disabled="!selectable"
      :aria-label="t('gui.git.worktree.row_select', { name })"
      @change="wt.setSelected(row.file.path, ($event.target as HTMLInputElement).checked)"
    />
    <button type="button" class="gw-row-main" data-test="git-row-open" :title="row.file.path" @click="openFileDiff(row.file.path, row.letter)">
      <span class="gw-mark" :class="`mk-${markClass}`" data-test="git-row-letter">{{ row.letter }}</span>
      <span class="gw-row-name">{{ name }}</span>
      <span v-if="tag" class="gw-tag" :class="`tag-${tag}`" data-test="git-row-tag" :title="t(`gui.git.worktree.tag.${tag}_tip`)">
        {{ t(`gui.git.worktree.tag.${tag}`) }}
      </span>
    </button>
    <span class="gw-row-btns">
      <button
        v-if="row.file.canRevert"
        type="button"
        class="gw-icon danger"
        data-test="git-row-revert"
        :disabled="reasons.stage.disabled"
        :title="tip('stage', 'gui.git.worktree.revert')"
        :aria-label="t('gui.git.worktree.revert')"
        @click="askRevert"
      >↶</button>
      <button
        v-else-if="row.file.canRemove"
        type="button"
        class="gw-icon danger"
        data-test="git-row-remove"
        :disabled="reasons.stage.disabled"
        :title="tip('stage', 'gui.git.worktree.remove')"
        :aria-label="t('gui.git.worktree.remove')"
        @click="askRemove"
      >🗑</button>
      <button
        v-if="row.section === 'changes' && row.file.canStage"
        type="button"
        class="gw-icon"
        data-test="git-row-stage"
        :disabled="reasons.stage.disabled"
        :title="tip('stage', 'gui.git.worktree.stage')"
        :aria-label="t('gui.git.worktree.stage')"
        @click="wt.stagePath(row.file.path)"
      >+</button>
      <button
        v-else-if="row.section === 'staged' && row.file.canUnstage"
        type="button"
        class="gw-icon"
        data-test="git-row-unstage"
        :disabled="reasons.unstage.disabled"
        :title="tip('unstage', 'gui.git.worktree.unstage')"
        :aria-label="t('gui.git.worktree.unstage')"
        @click="wt.unstagePath(row.file.path)"
      >−</button>
    </span>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — one worktree file line (0066 D#2 §3-6(4)): selection check, state letter,
// file name (full path as tooltip), protection tag, revert or remove, Stage or unstage. What it
// may do is the server's `can*` flags only — nothing is guessed from the path. The name opens the
// file's Center Diff; that is a read and stays open to read-only users too. The 0033 GitFileRow
// is a card row and is not used here.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { fileNameOf, isSelectable, type WorktreeListRow } from './worktreeList'
import { reasonMessage, type WorktreeButton } from './worktreeReasons'

const props = defineProps<{ row: WorktreeListRow }>()

const { t } = useI18n()
const { worktree: wt, openFileDiff, confirm } = useGitWorktree()
const reasons = computed(() => wt.reasons.value)

const name = computed(() => fileNameOf(props.row.file.path))
const selected = computed(() => wt.state.selected.includes(props.row.file.path))
const selectable = computed(() => isSelectable(props.row.file))
const markClass = computed(() => {
  const letter = props.row.letter
  return letter === '?' ? 'u' : letter.toLowerCase() || 'none'
})

// What the row is, when it is not an ordinary file (§3-6(4) table).
const tag = computed(() => {
  const file = props.row.file
  if (file.unsupported) return 'unsupported'
  if (file.fileClass === 'managed' || file.protectedReason === 'managed') return 'managed'
  if (file.protectedReason === 'excluded_new') return 'excluded_new'
  if (file.fileClass === 'unverified' || file.protectedReason === 'ownership_unavailable') return 'unverified'
  if (file.fileClass === 'excluded') return 'excluded'
  return null
})
const locked = computed(() => tag.value !== null && tag.value !== 'excluded')

function tip(button: WorktreeButton, help: string): string {
  const message = reasonMessage(reasons.value[button])
  return message ? t(message.key, message.params) : t(help)
}

// Both dialogs are bound to the context they were opened in: the path is this group's path.
function askRevert(): void {
  const path = props.row.file.path
  const ticket = wt.ticket()
  if (!ticket) return
  confirm(
    {
      title: t('gui.git.worktree.revert_title'),
      message: t('gui.git.worktree.revert_message', { path }),
      confirmLabel: t('gui.git.worktree.revert'),
      run: () => void wt.revert(path, ticket),
    },
    ticket,
  )
}

function askRemove(): void {
  const path = props.row.file.path
  const ticket = wt.ticket()
  if (!ticket) return
  confirm(
    {
      title: t('gui.git.worktree.remove_title'),
      message: t('gui.git.worktree.remove_message', { path }),
      confirmLabel: t('gui.git.worktree.remove'),
      danger: true,
      run: () => void wt.remove(path, ticket),
    },
    ticket,
  )
}
</script>
