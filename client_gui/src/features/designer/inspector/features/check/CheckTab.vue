<template>
  <!-- flowgate.v02.0048 T#12 — the check section of the documents tab (D#1 §10.2 T#12 row): the AC's check
       state and the Flow's Git reflection state (status, branch, ahead / behind, merge commit, a final
       approval Git finished without its approval, the final approval job still running), with
       "Git 패널 열기" (Host intent `show-tool: git`) for Git in detail and ↗ 열기 for the v0.1 AC screen.
       The final approval itself is the header action (CheckAction). A thin container: useCheck reads,
       checkModel decides the wording. -->
  <section class="inspector-check" data-test="inspector-check" :data-review="reviewStatus ?? ''" :data-git="git?.status ?? ''">
    <div class="inspector-check-line">
      <h4 class="inspector-check-heading">{{ t(k('title')) }}</h4>
      <span class="inspector-check-state" data-test="check-review" :data-key="reviewKey">{{ t(k(reviewKey), { status: reviewStatus ?? '' }) }}</span>
    </div>

    <div class="inspector-check-line">
      <span class="inspector-check-sub">{{ t(k('git.title')) }}</span>
      <InspectorPaneState v-if="gitStatus === 'loading' && !git" state="loading" />
      <span
        v-else-if="gitLine"
        class="inspector-check-state"
        data-test="check-git"
        :data-status="gitLine.status"
        :data-tone="gitLine.tone"
      >{{ t(k(gitLine.key), { status: gitLine.status }) }}</span>
      <span v-if="git?.branch && git.status !== 'none'" class="inspector-check-sub" data-test="check-git-branch">{{ t(k('git.branch'), { branch: git.branch }) }}</span>
    </div>
    <div v-if="gitErrorKey" data-test="check-git-error">
      <InspectorReason :text="t(k(gitErrorKey))" tone="warning" />
    </div>
    <p v-for="note in notes" :key="note.key" class="inspector-check-sub" data-test="check-git-note" :data-key="note.key">{{ t(k(note.key), { ...note.params }) }}</p>
    <div v-if="jobLine" data-test="check-job">
      <InspectorReason :text="t(k(jobLine.key), { ...jobLine.params })" tone="warning" />
    </div>

    <div class="inspector-check-line">
      <Button
        v-if="availability.openGit.visible"
        size="sm"
        variant="secondary"
        :title="t(k('open_git_hint'))"
        data-test="check-open-git"
        @click="openGit"
      >{{ t(k('open_git')) }}</Button>
      <Button
        v-if="availability.open.visible"
        size="sm"
        variant="ghost"
        :title="t(k('open_hint'))"
        data-test="check-open"
        @click="open"
      >{{ t(k('open')) }}</Button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Button from '../../../../../components/ui/Button.vue'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { checkGitLine, checkGitNotes, checkJobLine, checkReviewKey } from './checkModel'
import { useCheck } from './useCheck'

const props = defineProps<{
  target: InspectorTarget
}>()

const { t } = useI18n()

const k = (key: string) => `gui.inspector.check.${key}`

const { reviewStatus, git, gitStatus, gitErrorKey, availability, openGit, open } = useCheck(() => props.target)

const reviewKey = computed(() => checkReviewKey(reviewStatus.value))
const gitLine = computed(() => (git.value ? checkGitLine(git.value.status) : null))
const notes = computed(() => (git.value ? checkGitNotes(git.value) : []))
const jobLine = computed(() => checkJobLine(git.value, props.target.docId))
</script>

<style scoped>
.inspector-check {
  display: flex;
  flex-direction: column;
  gap: 6px;
  min-width: 0;
  padding-bottom: 10px;
  border-bottom: 1px solid var(--fg2-border, transparent);
}

.inspector-check-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  min-width: 0;
}

.inspector-check-heading {
  margin: 0;
  font-size: 12.5px;
  font-weight: 700;
}

.inspector-check-state {
  font-size: 12px;
  font-weight: 600;
}

.inspector-check-state[data-tone='warning'] {
  color: var(--fg2-status-warning-fg);
}

.inspector-check-state[data-tone='danger'] {
  color: var(--fg2-status-danger-fg);
}

.inspector-check-state[data-tone='success'] {
  color: var(--fg2-status-success-fg, inherit);
}

.inspector-check-sub {
  margin: 0;
  color: var(--fg2-muted);
  font-size: 11.5px;
}
</style>
