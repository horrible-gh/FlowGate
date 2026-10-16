<template>
  <section class="gw-section" :data-test="`git-section-${section.key}`">
    <div class="gw-section-head">
      <span class="gw-section-title">{{ t(`gui.git.worktree.section_${section.key}`) }}</span>
      <span class="gw-num" :data-test="`git-section-${section.key}-count`">{{ countText }}</span>
      <span class="gw-grow" />
      <button
        v-if="section.key === 'changes' && canStageSection"
        type="button"
        class="gw-small"
        data-test="git-section-stage-all"
        :disabled="reasons.stage.disabled"
        :title="tip('stage', 'gui.git.worktree.all_tip')"
        @click="wt.stageAll()"
      >{{ t('gui.git.worktree.stage_all') }}</button>
      <button
        v-if="section.key === 'staged'"
        type="button"
        class="gw-small"
        data-test="git-section-unstage-all"
        :disabled="reasons.unstageAll.disabled"
        :title="tip('unstageAll', 'gui.git.worktree.unstage_all_tip')"
        @click="wt.unstageAll()"
      >{{ t('gui.git.worktree.unstage_all') }}</button>
    </div>
    <GitChangeFolder v-for="folder in section.folders" :key="folder.key" :folder="folder" />
    <p v-if="section.total > 0 && !section.matched" class="gw-empty" :data-test="`git-section-${section.key}-nomatch`">
      {{ t('gui.git.worktree.no_match') }}
    </p>
    <p v-if="section.hiddenStaged > 0" class="gw-hidden-staged" data-test="git-section-hidden-staged">
      🔒 {{ t('gui.git.worktree.hidden_staged', { n: section.hiddenStaged }) }}
    </p>
  </section>
</template>

<script setup lang="ts">
// flowgate.v02.0066 — one section of the changed-files list: 변경 사항 (working changes and new
// files) or Stage됨 (index changes, plus the pathless line for a Stage the list does not show).
// Its full Stage / full unstage is the whole-group request, whatever the search shows (§3-6(2)).
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitChangeFolder from './GitChangeFolder.vue'
import { useGitWorktree } from './gitWorktreeSession'
import type { WorktreeSectionView } from './worktreeList'
import { reasonMessage, type WorktreeButton } from './worktreeReasons'

const props = defineProps<{ section: WorktreeSectionView }>()

const { t } = useI18n()
const { worktree: wt } = useGitWorktree()
const reasons = computed(() => wt.reasons.value)

// "일치 / 전체" while a search or filter narrows the section.
const countText = computed(() =>
  props.section.matched === props.section.total ? String(props.section.total) : `${props.section.matched} / ${props.section.total}`,
)
const canStageSection = computed(() => wt.state.status?.files.some((f) => f.canStage) ?? false)

function tip(button: WorktreeButton, help: string): string {
  const message = reasonMessage(reasons.value[button])
  return message ? t(message.key, message.params) : t(help)
}
</script>
