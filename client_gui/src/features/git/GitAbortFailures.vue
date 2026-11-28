<template>
  <!-- How the last forced cleanup of another group's session ended: its approval is cancelled here too (or the button that still
       does it is named), and the way to that group's check card is one press. Stays after the list item is gone. -->
  <section v-if="isAdmin && outcome" class="gx-card" data-test="git-abort-failure-done">
    <p class="gx-desc" data-test="git-abort-failure-done-text">{{ outcomeText }}</p>
    <div v-if="outcome.nodeKey && outcome.kind !== 'none'" class="gx-actions">
      <button type="button" class="gx-btn primary" data-test="git-abort-failure-open-card" @click="openGateCard(outcome.groupId, outcome.nodeKey)">
        {{ t('gui.git.abort_failures.open_card') }}
      </button>
    </div>
  </section>
  <!-- flowgate.v02test.0052 (0008 task 3): administrators only — the open merges marked "정리 실패" (the abort was refused
       and nothing could close them), each with the reason git gave and the forced cleanup. Nobody else sees it or asks for it. -->
  <section v-if="isAdmin && items.length" class="gx-card danger" data-test="git-abort-failures">
    <div class="gx-title">{{ t('gui.git.abort_failures.title', { n: items.length }) }}</div>
    <p class="gx-desc">{{ t('gui.git.abort_failures.desc') }}</p>
    <div v-for="item in items" :key="item.mergeId" class="gx-abort-failure-item" data-test="git-abort-failure-item">
      <div class="gx-desc">{{ item.targetBranch ? t('gui.git.abort_failures.item', { id: item.mergeId, group: item.groupId ?? '—', target: item.targetBranch }) : t('gui.git.abort_failures.item_unknown_target', { id: item.mergeId, group: item.groupId ?? '—' }) }}</div>
      <code v-if="item.abortFailure?.reason" class="gx-abort-failure-reason" data-test="git-abort-failure-reason">{{ item.abortFailure.reason }}</code>
      <small v-if="item.abortFailure?.at" class="gx-kicker">{{ t('gui.git.abort_failures.at', { at: item.abortFailure.at }) }}</small>
      <div class="gx-actions">
        <button v-if="item.groupId" type="button" class="gx-btn danger" :disabled="!session.canMerge.value" data-test="git-abort-failure-force" @click="force(item)">
          {{ t('gui.git.abort_failures.force') }}
        </button>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useAbortMergeConfirm, type ForceCleanupOutcome } from './abortMerge'
import { openGateCard } from './gateOperations'
import { useGitPanel } from './gitSession'
import type { GitAbortFailureSession } from './types'

const { t } = useI18n()
const ctx = useGitPanel()
const session = ctx.session
const { askForceCleanup } = useAbortMergeConfirm()
const isAdmin = computed(() => ctx.isAdmin?.() === true)
const items = ref<GitAbortFailureSession[]>([])
const outcome = ref<ForceCleanupOutcome | null>(null)
const outcomeText = computed(() => {
  const o = outcome.value
  if (!o) return ''
  const params = { id: o.mergeId, group: o.groupId, code: o.code ?? '' }
  return t(`gui.git.abort_failures.done_${o.kind}`, params)
})
let alive = true
onBeforeUnmount(() => { alive = false })

// Read again whenever git/status was read (an abort, a forced cleanup and every refresh end in one). A person who is
// not an administrator never reaches the request.
async function load(): Promise<void> {
  if (!isAdmin.value || !session.state.projectId) {
    items.value = []
    return
  }
  const list = await session.loadAbortFailures()
  if (alive) items.value = list
}
watch(() => [isAdmin.value, session.state.projectId, session.state.statusGen] as const, () => void load(), { immediate: true })

async function force(item: GitAbortFailureSession): Promise<void> {
  if (!item.groupId) return
  await askForceCleanup({ groupId: item.groupId, mergeId: item.mergeId }, done => { if (alive) outcome.value = done })
}
</script>

<style scoped>
.gx-abort-failure-item { display: flex; flex-direction: column; gap: 3px; margin-top: 8px; }
.gx-abort-failure-reason { display: block; overflow-wrap: anywhere; font-size: 11.5px; }
</style>
