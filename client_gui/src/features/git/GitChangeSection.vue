<template>
  <section class="gw-section" :data-test="`git-section-${section.key}`">
    <div class="gw-section-head">
      <span class="gw-section-title">{{ t(`gui.git.worktree.section_${section.key}`) }}</span>
      <span class="gw-num" :data-test="`git-section-${section.key}-count`">{{ countText }}</span>
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
// flowgate.v02.0067 T#2: the full Stage / full unstage live once, in the selection bar above the
// list (0067-R §4-4), so the section head carries only its title and count.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import GitChangeFolder from './GitChangeFolder.vue'
import type { WorktreeSectionView } from './worktreeList'

const props = defineProps<{ section: WorktreeSectionView }>()

const { t } = useI18n()

// "일치 / 전체" while a search or filter narrows the section.
const countText = computed(() =>
  props.section.matched === props.section.total ? String(props.section.total) : `${props.section.matched} / ${props.section.total}`,
)
</script>
