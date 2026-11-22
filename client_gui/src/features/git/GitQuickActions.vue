<template>
  <div class="gq" data-test="git-quick-actions">
    <section class="gq-main">
      <div class="gq-row">
        <div class="gq-scopes" role="group" :aria-label="words.scope">
          <button v-for="item in scopes" :key="item" :disabled="busy || paused" :aria-pressed="scope === item" @click="scope = item">{{ words[item + 'Scope'] }}</button>
        </div>
        <button class="gq-run" data-test="git-execute" :disabled="disabled || paused" @click="execute(false)">{{ busy ? words.running : words.execute }}</button>
      </div>
      <div class="gq-row">
        <button class="gq-files" :disabled="busy" :aria-expanded="filesOpen" @click="filesOpen = !filesOpen">{{ words.files }} {{ selectedCount }}/<span data-test="git-worktree-summary-changed">{{ wt.state.status?.counts.changed ?? '—' }}</span></button>
        <input :value="wt.state.message" :disabled="busy" :placeholder="words.message" :aria-label="words.message" @input="wt.setMessage(($event.target as HTMLInputElement).value)" />
      </div>
      <div v-if="filesOpen" class="gq-pick">
        <input v-if="files.length > 8" v-model="search" :placeholder="words.search" :aria-label="words.search" />
        <label><input type="checkbox" :checked="selectedCount === files.length" :disabled="busy" @change="toggleAll" />{{ words.all }}</label>
        <div class="gq-list">
          <label v-for="file in filteredFiles" :key="file.path" :title="file.path">
            <input type="checkbox" :checked="!excluded.includes(file.path)" :disabled="busy" @change="toggle(file.path)" />
            <span>{{ file.path }}</span>
          </label>
        </div>
      </div>
      <div v-if="scope !== 'commit'" class="gq-row">
        <span>{{ words.target }}</span>
        <input v-if="wt.state.mergeTargets.length > 8" v-model="targetSearch" :placeholder="words.targetSearch" :aria-label="words.targetSearch" :disabled="busy || paused" />
        <select :value="wt.state.mergeTarget ?? ''" :disabled="busy || paused" :aria-label="words.target" @change="wt.setMergeTarget(($event.target as HTMLSelectElement).value)">
          <option v-for="target in filteredTargets" :key="target.name" :value="target.name" :disabled="!target.eligible">{{ target.name }}</option>
        </select>
      </div>
      <div class="gq-chain" data-test="git-execution-steps">
        <span v-for="(step, index) in shownSteps" :key="index" :class="{ done: plan && index < plan.cursor, now: busy && index === plan?.cursor, failed: paused && index === plan?.cursor }">{{ words[step] }}</span>
      </div>
      <template v-if="plan">
        <GitConflictCard v-if="session.groupCard.value === 'conflict' || session.groupCard.value === 'unsupported'" :unsupported="session.groupCard.value === 'unsupported'" />
        <GitAiResolvingCard v-else-if="session.groupCard.value === 'ai'" />
        <GitReviewCard v-else-if="session.groupCard.value === 'review'" />
      </template>
      <div v-if="plan && !busy" class="gq-result" :class="{ success: plan.status === 'complete' }" role="status">
        <span :title="plan.error ?? undefined">{{ resultText }}</span>
        <button v-if="paused" :title="words.close" :aria-label="words.close" @click="wt.clearExecution()">×</button>
        <button v-if="paused && plan.error !== 'merge_conflict'" :disabled="disabled" @click="execute(true)">{{ plan.error === 'push_rejected' ? words.syncPush : words.continue }}</button>
        <button v-if="paused && plan.error === 'merge_conflict' && !conflict" :disabled="disabled" @click="execute(true)">{{ words.continue }}</button>
        <button v-if="paused && plan.error === 'uncommitted_changes'" :disabled="disabled" @click="commitRemaining">{{ words.commitContinue }}</button>
      </div>
    </section>
    <details class="gq-individual">
      <summary>{{ words.individual }}</summary>
      <div class="gq-action"><b>{{ words.commit }}</b><span>Stage <span data-test="git-worktree-summary-staged">{{ wt.state.status?.counts.staged ?? '—' }}</span></span><button data-test="git-worktree-entry" data-tab="files" :title="words.fileDetails" :aria-label="words.fileDetails" @click="emit('details', 'files')">◫</button><button :disabled="disabled || !selectedCount" :title="words.commit" :aria-label="words.commit" @click="wt.execute('commit', selection())">⊙</button></div>
      <div class="gq-action"><b>{{ words.update }}</b><span>{{ dirty ? words.commitFirst : words.base }}</span><button :disabled="disabled || dirty" :title="words.update" :aria-label="words.update" @click="wt.updateFromBase()">↓</button></div>
      <div class="gq-action"><b>{{ words.merge }}</b><button data-test="git-worktree-entry" data-tab="merge" :title="words.mergeDetails" :aria-label="words.mergeDetails" @click="emit('details', 'merge')">◫</button><span><span data-test="git-worktree-summary-merge">{{ wt.state.merge?.aheadOfTarget ?? '—' }}</span> → {{ wt.state.mergeTarget }}</span><button :disabled="disabled || wt.reasons.value.merge.disabled" :title="words.merge" :aria-label="words.merge" @click="confirm = 'merge'">⑂</button></div>
      <div class="gq-action"><b>{{ words.push }}</b><span>{{ pushCount }}</span><button :disabled="disabled || wt.reasons.value.push.disabled" :title="words.push" :aria-label="words.push" @click="confirm = 'push'">↑</button></div>
      <div v-if="confirm" class="gq-row gq-confirm"><span>{{ words[confirm] }} → {{ confirm === 'merge' ? wt.state.mergeTarget : wt.pushRow.value?.target.targetBranch }}</span><button :disabled="disabled" :title="words.execute" :aria-label="words.execute" @click="confirmAction">✓</button><button :title="words.close" :aria-label="words.close" @click="confirm = null">×</button></div>
    </details>
    <p v-if="blocked" class="gq-muted" role="status">{{ words.blocked }}</p>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { useGitPanel } from './gitSession'
import GitConflictCard from './GitConflictCard.vue'
import GitAiResolvingCard from './GitAiResolvingCard.vue'
import GitReviewCard from './GitReviewCard.vue'
import { executionSteps, type GitExecutionScope } from './gitExecution'
import type { WorktreeSelection } from './types'
const emit = defineEmits<{ details: [tab: 'files' | 'merge'] }>()
const { worktree: wt } = useGitWorktree()
const { session } = useGitPanel()
const { locale } = useI18n()
const ko: Record<string, string> = { targetSearch: '브랜치 찾기', fileDetails: '파일 상세', mergeDetails: '병합 상세', permissionHelp: '푸시 권한 없음 · Git 설정 확인', protectedHelp: '보호된 브랜치 · 저장소 규칙 확인', syncPush: '원격 반영 후 푸시', scope: '실행 범위', commitScope: '커밋만', mergeScope: '병합까지', pushScope: '푸시까지', execute: '실행', running: '실행 중', files: '파일', message: '커밋 메시지 (비우면 AI 작성)', target: '대상', search: '파일 찾기', all: '전체 선택', individual: '개별 실행', commit: '커밋', update: '받아오기', merge: '병합', push: '푸시', continue: '계속', complete: '완료', remaining: '남은 변경 있음', stopped: '실행 멈춤', conflict: '충돌 해결 후 계속', commitFirst: '커밋 필요', commitContinue: '모두 커밋하고 계속', base: '베이스 → 작업 브랜치', blocked: '작업 중', close: '닫기', rejected: '푸시 거절 · 원격 상태 확인 필요', messageRequired: '커밋 메시지 입력', queued: '처리 대기' }
const en: Record<string, string> = { targetSearch: 'Find branches', fileDetails: 'File details', mergeDetails: 'Merge details', permissionHelp: 'Push permission denied · check Git settings', protectedHelp: 'Protected branch · check repository rules', syncPush: 'Sync remote and push', scope: 'Scope', commitScope: 'Commit', mergeScope: 'Through merge', pushScope: 'Through push', execute: 'Run', running: 'Running', files: 'Files', message: 'Commit message (AI when blank)', target: 'Target', search: 'Find files', all: 'Select all', individual: 'Individual actions', commit: 'Commit', update: 'Update', merge: 'Merge', push: 'Push', continue: 'Continue', complete: 'Complete', remaining: 'Changes remain', stopped: 'Stopped', conflict: 'Resolve conflicts to continue', commitFirst: 'Commit required', commitContinue: 'Commit all and continue', base: 'Base → work branch', blocked: 'Work in progress', close: 'Close', rejected: 'Push rejected · check remote', messageRequired: 'Enter a commit message', queued: 'Queued' }
const words = computed(() => locale.value.startsWith('ko') ? ko : en)
const scopes: GitExecutionScope[] = ['commit', 'merge', 'push']
const scope = ref<GitExecutionScope>('push')
const filesOpen = ref(false)
const excluded = ref<string[]>([])
const search = ref('')
const targetSearch = ref('')
const filteredTargets = computed(() => wt.state.mergeTargets.filter(t => t.name === wt.state.mergeTarget || t.name.toLowerCase().includes(targetSearch.value.toLowerCase())).sort((a,b) => Number(b.name === wt.state.mergeTarget)-Number(a.name === wt.state.mergeTarget)))
const confirm = ref<'merge' | 'push' | null>(null)
const files = computed(() => wt.state.status?.files.filter(f => f.canStage || f.canUnstage) ?? [])
const filteredFiles = computed(() => files.value.filter(f => f.path.toLowerCase().includes(search.value.toLowerCase())))
const selectedCount = computed(() => files.value.filter(f => !excluded.value.includes(f.path)).length)
const plan = computed(() => wt.state.execution)
const paused = computed(() => plan.value?.status === 'stopped')
const busy = computed(() => !!wt.state.busy || !!session.state.busy)
const conflict = computed(() => ['conflict', 'ai', 'review', 'unsupported'].includes(session.groupCard.value ?? ''))
const blocked = computed(() => (wt.state.status?.blockers.length ?? 0) > 0 || !!wt.state.merge?.graph?.blocker)
const disabled = computed(() => busy.value || conflict.value || blocked.value || session.state.readOnly || wt.state.forbidden || wt.state.statusLoad !== 'ready')
const dirty = computed(() => (wt.state.status?.counts.changed ?? 1) > 0)
const shownSteps = computed(() => paused.value || busy.value ? plan.value?.steps ?? executionSteps(scope.value) : executionSteps(scope.value))
const pushCount = computed(() => wt.state.results?.results.find(r => r.target.targetBranch === wt.state.mergeTarget)?.remote.localAheadCount ?? '—')
const resultText = computed(() => {
  const p = plan.value
  if (!p) return ''
  if (p.status === 'complete' || p.status === 'remaining') return words.value[p.status]
  const key = p.error === 'push_authentication_required' ? 'permissionHelp' : p.error === 'push_protected_branch' ? 'protectedHelp' : p.error === 'merge_conflict' ? 'conflict' : p.error === 'uncommitted_changes' ? 'commitFirst'
    : p.error === 'push_rejected' ? 'rejected' : p.error === 'commit_message_required' ? 'messageRequired'
    : p.error === 'queued' ? 'queued' : 'stopped'
  return words.value[key]
})
function selection(): WorktreeSelection { return excluded.value.length ? { paths: files.value.filter(f => !excluded.value.includes(f.path)).map(f => f.path) } : { all: true } }
function toggle(path: string) { excluded.value = excluded.value.includes(path) ? excluded.value.filter(p => p !== path) : [...excluded.value, path] }
function toggleAll() { excluded.value = selectedCount.value === files.value.length ? files.value.map(f => f.path) : [] }
function execute(resume: boolean) { void wt.execute(scope.value, selection(), resume) }
async function commitRemaining() {
  // Explicitly authorized additional commit; the interrupted plan is retained by the session.
  await wt.execute(scope.value, { all: true }, true, true)
}
function confirmAction() {
  const action = confirm.value; confirm.value = null
  if (action === 'merge') { wt.setPushAfterMerge(false); const s = wt.mergeSnapshot(); if (s) void wt.merge(s) }
  if (action === 'push') { const s = wt.pushSnapshot(); if (s) void wt.pushResult(s) }
}
watch(() => wt.ticket()?.groupId, () => { excluded.value = []; search.value = ''; filesOpen.value = false; confirm.value = null })
watch(() => wt.state.mergeTarget, () => { confirm.value = null })
</script>

<style scoped>
.gq { display:flex; flex-direction:column; gap:10px; font-size:12px; min-width:0 }
.gq-main { display:flex; flex-direction:column; gap:8px; padding:9px; border:1px solid #355c88; background:#10233a; border-radius:8px }
.gq-row { display:flex; gap:6px; align-items:center; min-width:0; flex-wrap:wrap }
.gq button, .gq input:not([type=checkbox]), .gq select { border:1px solid #30445f; border-radius:5px; background:#111e30; color:#dce8f5; font:inherit; min-height:28px }
.gq button { cursor:pointer; padding:3px 8px }.gq button:disabled { opacity:.4; cursor:default }
.gq input:not([type=checkbox]), .gq select { flex:1; min-width:80px; padding:4px 7px; max-width:100% }
.gq-scopes { display:flex }.gq-scopes button { border-radius:0; font-size:11px }.gq-scopes button:first-child { border-radius:5px 0 0 5px }.gq-scopes button:last-child { border-radius:0 5px 5px 0 }.gq-scopes button[aria-pressed=true] { background:#24466e; color:white }
.gq .gq-run { margin-left:auto; background:#2f80ed; border-color:#2f80ed; color:white; font-weight:700; min-width:56px }
.gq-chain { display:flex; flex-wrap:wrap; gap:5px; color:#8ea3bd; font-size:11px }.gq-chain span { padding:3px 6px; border-radius:4px; background:#15253b }.gq-chain .done { color:#66dfb0 }.gq-chain .now { color:white; background:#24466e }.gq-chain .failed { color:#ffb3bf }
.gq-pick { display:flex; flex-direction:column; gap:6px }.gq-list { max-height:192px; overflow:auto; border:1px solid #22344d; border-radius:5px; background:#0a1524 }.gq-pick label { display:flex; align-items:center; gap:6px; height:24px; padding:0 5px }.gq-pick label span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.gq-result { display:flex; align-items:center; flex-wrap:wrap; gap:7px; color:#ffc072; font-size:11px }.gq-result.success { color:#66dfb0 }.gq-result span { flex:1 }.gq-result button { font-size:11px }
.gq-individual summary { cursor:pointer; color:#8ea3bd; padding:3px 0 }.gq-action { display:flex; align-items:center; gap:8px; padding:7px 8px; border:1px solid #22344d; border-radius:7px; margin-top:6px; min-width:0 }.gq-action b { flex:0 0 60px }.gq-action span { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; color:#8ea3bd; font-size:11px }.gq-action button { width:28px; padding:0 }.gq-confirm { margin-top:8px }.gq-confirm span { flex:1 }.gq-muted { color:#8ea3bd; margin:0 }
</style>
