<template>
  <!-- flowgate.v02.0048 T#12 — the check section of the documents tab (D#1 §10.2 T#12 row): the AC's check
       state and the Flow's Git reflection state (status, branch, ahead / behind, merge commit, a final
       approval Git finished without its approval, the final approval job still running), with
       "Git 패널 열기" (Host intent `show-tool: git`) for Git in detail and ↗ 열기 for the AC in its document window (✎ in its editor).
       The final approval itself is the header action (CheckAction). A thin container: useCheck reads,
       checkModel decides the wording.
       flowgate.v02.0051 stage 2 — the v7 확인 card (0035 cmid.* / cend.*): the one 확인 결과 result line
       (check state pill · Git state and branch as its summary · "Git 패널 열기 ↗" · ↗), built from the
       common slot list part; the Git notes and the running-job line stay below. -->
  <section class="inspector-check" data-test="inspector-check" :data-review="reviewStatus ?? ''" :data-git="git?.status ?? ''">
    <InspectorSlotList :slots="lines" :editable="!target.readOnly" @open="open" @nav="openGit" @edit="edit" @history="history">
      <template #summary>
        <span v-if="gitLine" class="inspector-check-git" data-test="check-git" :data-status="gitLine.status" :data-tone="gitLine.tone">{{ t(k(gitLine.key), { status: gitLine.status }) }}</span>
        <span v-if="git?.branch && git.status !== 'none'" class="inspector-check-sub" data-test="check-git-branch">{{ t(k('git.branch'), { branch: git.branch }) }}</span>
      </template>
    </InspectorSlotList>

    <details v-if="(gitStatus === 'loading' && !git) || gitErrorKey || notes.length || jobLine" :key="target.docId ?? undefined" class="inspector-check-details" data-test="check-details">
      <summary>{{ t('gui.designer.inspector.pane.details') }}</summary>
    <InspectorPaneState v-if="gitStatus === 'loading' && !git" state="loading" />
    <div v-if="gitErrorKey" data-test="check-git-error">
      <InspectorReason :text="t(k(gitErrorKey))" tone="warning" />
    </div>
    <p v-for="note in notes" :key="note.key" class="inspector-check-sub" data-test="check-git-note" :data-key="note.key">{{ t(k(note.key), { ...note.params }) }}</p>
    <div v-if="jobLine" data-test="check-job">
      <InspectorReason :text="t(k(jobLine.key), { ...jobLine.params })" tone="warning" />
    </div>
    </details>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import InspectorPaneState from '../../parts/InspectorPaneState.vue'
import InspectorReason from '../../parts/InspectorReason.vue'
import InspectorSlotList, { type InspectorSlotView } from '../../parts/InspectorSlotList.vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { openDocument, openDocumentHistory } from '../../openDocument'
import { checkGitLine, checkGitNotes, checkJobLine, checkReviewKey, checkReviewTone } from './checkModel'
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

/** The one v7 line: the check state as the pill; the Git state and branch are rendered as the summary above. */
const lines = computed<InspectorSlotView[]>(() => {
  const tone = checkReviewTone(reviewStatus.value)
  return [
    {
      key: 'result',
      kind: 'result',
      name: t(k('slot.result')),
      id: props.target.docId,
      state: { label: t(k(reviewKey.value), { status: reviewStatus.value ?? '' }), tone },
      focus: tone === 'warning',
      danger: tone === 'danger' || gitLine.value?.tone === 'danger',
      nav: availability.value.openGit.visible ? { label: t(k('open_git')), title: t(k('open_git_hint')) } : null,
      open: availability.value.open.visible ? 'enabled' : 'none',
      testIds: { state: 'check-review', nav: 'check-open-git', open: 'check-open' },
    },
  ]
})

/** ✎: the AC's document window, straight in its editor (flowgate.v02.0053 T#3). */
function edit(line: InspectorSlotView) {
  if (line.id) openDocument(line.id, 'edit')
}

/** The history button: the document's 검수·반려 이력 window (flowgate.v02.0056 F1). */
function history(line: InspectorSlotView) {
  if (line.id) openDocumentHistory(line.id)
}
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

.inspector-check-git {
  overflow: hidden;
  color: #9cb1c7;
  font-size: 11.5px;
  text-overflow: ellipsis;
}

.inspector-check-git[data-tone='warning'] {
  color: var(--fg2-status-warning-fg);
}

.inspector-check-git[data-tone='danger'] {
  color: var(--fg2-status-danger-fg);
}

.inspector-check-git[data-tone='success'] {
  color: var(--fg2-status-success-fg, inherit);
}

.inspector-check-sub {
  margin: 0;
  overflow: hidden;
  color: var(--fg2-muted);
  font-size: 11.5px;
  text-overflow: ellipsis;
}
</style>
