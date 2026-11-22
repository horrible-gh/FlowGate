<template>
  <div class="gq" data-test="git-quick-actions">
    <section class="gq-main">
      <div class="gq-row">
        <div class="gq-scopes" role="group" :aria-label="words.scope">
          <button v-for="item in scopes" :key="item" :disabled="busy || paused" :aria-pressed="scope === item" @click="scope = item">{{ words[item + 'Scope'] }}</button>
        </div>
        <button class="gq-run" data-test="git-execute" :class="{ 'is-busy': busy }" :disabled="disabled || paused" @click="execute(false)"><span v-if="busy" class="gq-spin" aria-hidden="true" />{{ busy ? words.running : '▶ ' + words.execute }}</button>
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
        <span v-for="(step, index) in shownSteps" :key="index" :class="{ done: plan && index < plan.cursor, now: busy && index === plan?.cursor, failed: paused && index === plan?.cursor }">{{ stepMark(index) }}{{ words[step] }}</span>
      </div>
      <template v-if="plan">
        <GitConflictCard v-if="session.groupCard.value === 'conflict' || session.groupCard.value === 'unsupported'" :unsupported="session.groupCard.value === 'unsupported'" />
        <GitAiResolvingCard v-else-if="session.groupCard.value === 'ai'" />
        <GitReviewCard v-else-if="session.groupCard.value === 'review'" />
      </template>
      <div v-if="plan && !busy" class="gq-result" :class="{ success: plan.status === 'complete', stopped: paused }" role="status" data-test="git-execution-result">
        <div class="gq-result-text">
          <b>{{ resultTitle }}</b>
          <span v-if="paused" data-test="git-stop-reason">{{ resultText }}</span>
          <small v-if="paused && plan.error" class="gq-code" data-test="git-stop-code">{{ words.code }}: {{ plan.error }}<template v-if="plan.detail"> · {{ plan.detail }}</template></small>
        </div>
        <div v-if="paused" class="gq-fixes">
          <button v-for="fix in stopFixes" :key="fix" class="gq-fix" :data-test="'git-fix-' + fix" @click="runFix(fix)">{{ words['fix_' + fix] }}</button>
          <button v-if="plan.error === 'uncommitted_changes'" class="gq-fix gq-primary" :disabled="disabled" @click="commitRemaining">{{ words.commitContinue }}</button>
          <button v-if="plan.error !== 'merge_conflict' || !conflict" class="gq-fix gq-primary" data-test="git-retry" :disabled="disabled" @click="execute(true)">{{ plan.error === 'push_rejected' ? words.syncPush : plan.error === 'merge_conflict' ? words.continue : words.retry }}</button>
          <button class="gq-fix" data-test="git-cancel" @click="wt.clearExecution()">{{ words.cancelRun }}</button>
        </div>
        <button v-else class="gq-close" :title="words.close" :aria-label="words.close" @click="wt.clearExecution()">×</button>
      </div>
    </section>
    <details class="gq-individual">
      <summary>{{ words.individual }}</summary>
      <div class="gq-action"><b>{{ words.commit }}</b><span>Stage <span data-test="git-worktree-summary-staged">{{ wt.state.status?.counts.staged ?? '—' }}</span></span><button class="gq-icon" data-test="git-worktree-entry" data-tab="files" :title="words.fileDetails" :aria-label="words.fileDetails" @click="emit('details', 'files')">◫</button><button class="gq-act" data-test="git-action-commit" :class="{ ready: !disabled && !!selectedCount, running: wt.state.busy === 'commit' }" :disabled="disabled || !selectedCount" :title="words.commit" :aria-label="words.commit" @click="wt.execute('commit', selection())"><span v-if="wt.state.busy === 'commit'" class="gq-spin" aria-hidden="true" />⊙ {{ words.commit }}</button></div>
      <div class="gq-action"><b>{{ words.update }}</b><span>{{ dirty ? words.commitFirst : words.base }}</span><button class="gq-act" data-test="git-action-update" :class="{ ready: !disabled && !dirty, running: wt.state.busy === 'update' }" :disabled="disabled || dirty" :title="words.update" :aria-label="words.update" @click="wt.updateFromBase()"><span v-if="wt.state.busy === 'update'" class="gq-spin" aria-hidden="true" />{{ wt.state.busy === 'update' ? words.updating : '↓ ' + words.update }}</button></div>
      <div class="gq-action"><b>{{ words.merge }}</b><button class="gq-icon" data-test="git-worktree-entry" data-tab="merge" :title="words.mergeDetails" :aria-label="words.mergeDetails" @click="emit('details', 'merge')">◫</button><span><span data-test="git-worktree-summary-merge">{{ wt.state.merge?.aheadOfTarget ?? '—' }}</span> → {{ wt.state.mergeTarget }}</span><button class="gq-act" data-test="git-action-merge" :class="{ ready: !disabled && !wt.reasons.value.merge.disabled, running: wt.state.busy === 'merge' }" :disabled="disabled || wt.reasons.value.merge.disabled" :title="words.merge" :aria-label="words.merge" @click="confirm = 'merge'"><span v-if="wt.state.busy === 'merge'" class="gq-spin" aria-hidden="true" />⑂ {{ words.merge }}</button></div>
      <div class="gq-action"><b>{{ words.push }}</b><span>{{ pushCount }}</span><button class="gq-act" data-test="git-action-push" :class="{ ready: !disabled && !wt.reasons.value.push.disabled, running: wt.state.busy === 'push' }" :disabled="disabled || wt.reasons.value.push.disabled" :title="words.push" :aria-label="words.push" @click="confirm = 'push'"><span v-if="wt.state.busy === 'push'" class="gq-spin" aria-hidden="true" />↑ {{ words.push }}</button></div>
      <div v-if="confirm" class="gq-row gq-confirm"><span>{{ words[confirm] }} → {{ confirm === 'merge' ? wt.state.mergeTarget : wt.pushRow.value?.target.targetBranch }}</span><button class="gq-fix gq-primary" :disabled="disabled" :title="words.execute" :aria-label="words.execute" @click="confirmAction">✓ {{ words.execute }}</button><button class="gq-fix" :title="words.close" :aria-label="words.close" @click="confirm = null">×</button></div>
    </details>
    <p v-for="line in noticeLines" :key="line.area" class="gq-notice" :class="'is-' + line.tone" role="status" :data-test="'git-quick-notice-' + line.area">
      <span>{{ line.text }}</span>
      <button class="gq-close" :title="words.close" :aria-label="words.close" @click="wt.dismissNotice(line.area)">×</button>
    </p>
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
type Fix = 'settings' | 'station' | 'status' | 'files' | 'merge'
const emit = defineEmits<{ details: [tab: 'files' | 'merge' | 'status']; settings: []; station: [] }>()
const { worktree: wt } = useGitWorktree()
const { session } = useGitPanel()
const { locale, t } = useI18n()
const ko: Record<string, string> = { targetSearch: '브랜치 찾기', fileDetails: '파일 상세', mergeDetails: '병합 상세', syncPush: '원격 반영 후 푸시', scope: '실행 범위', commitScope: '커밋만', mergeScope: '병합까지', pushScope: '푸시까지', execute: '실행', running: '실행 중…', updating: '받아오는 중…', files: '파일', message: '커밋 메시지 (비우면 AI 작성)', target: '대상', search: '파일 찾기', all: '전체 선택', individual: '개별 실행', commit: '커밋', update: '받아오기', merge: '병합', push: '푸시', continue: '계속', retry: '다시 시도', cancelRun: '취소', complete: '완료', remaining: '완료 · 남은 변경 있음', stoppedAt: '{step}에서 멈춤', stopped: '알 수 없는 이유 · 코드 확인', code: '코드', commitFirst: '커밋 먼저', commitContinue: '커밋하고 계속', base: '베이스 → 작업 브랜치', blocked: '작업 중', close: '닫기', fix_settings: 'Git 설정 열기', fix_station: 'Git 스테이션 열기', fix_status: '상태 보기', fix_files: '파일 보기', fix_merge: '병합 상세' }
const en: Record<string, string> = { targetSearch: 'Find branches', fileDetails: 'File details', mergeDetails: 'Merge details', syncPush: 'Sync remote and push', scope: 'Scope', commitScope: 'Commit', mergeScope: 'Through merge', pushScope: 'Through push', execute: 'Run', running: 'Running…', updating: 'Updating…', files: 'Files', message: 'Commit message (AI when blank)', target: 'Target', search: 'Find files', all: 'Select all', individual: 'Individual actions', commit: 'Commit', update: 'Update', merge: 'Merge', push: 'Push', continue: 'Continue', retry: 'Retry', cancelRun: 'Cancel', complete: 'Complete — the selected scope is fully applied.', remaining: 'Finished, but changes remain.', stoppedAt: 'Stopped at {step}', stopped: 'Stopped at this step. Check the code below.', code: 'Code', commitFirst: 'Commit required', commitContinue: 'Commit all and continue', base: 'Base → work branch', blocked: 'Work in progress', close: 'Close', fix_settings: 'Open Git settings', fix_station: 'Open Git station', fix_status: 'View status', fix_files: 'View files', fix_merge: 'Merge details' }
// Why a run stopped, in words, with what can be done about it on this screen.
const REASONS: Record<string, { ko: string; en: string; fixes?: Fix[] }> = {
  base_dirty: { ko: '베이스에 커밋 안 한 수정 파일 있음', en: 'The base checkout has uncommitted edits, so the update cannot run. Clean up the base changes, then retry.', fixes: ['status'] },
  git_error: { ko: '원격에서 받지 못함 · 인증 정보나 네트워크 확인', en: 'Could not fetch from the remote repository. Check the credentials or the network, then retry.', fixes: ['settings'] },
  base_diverged: { ko: '베이스에 원격에 없는 커밋 있음', en: 'The base branch has commits the remote does not, so it cannot fast-forward. Check the base in the Git station.', fixes: ['station'] },
  group_work_base_unverified: { ko: '작업 베이스 미확인 · 관리자 확인 필요', en: 'The work base is not confirmed yet. An administrator confirms it in the Git station, then retry.', fixes: ['station'] },
  group_work_base_diverged: { ko: '작업 베이스 이력 불일치', en: 'The recorded work base no longer matches the history (reset or rewritten). Check it in the Git station.', fixes: ['station'] },
  group_work_base_source_rewritten: { ko: '작업 베이스가 초기화되거나 이력이 바뀜', en: 'The work base was reset or rewritten, so the update was refused. Check it in the Git station.', fixes: ['station'] },
  group_work_base_record_failed: { ko: '작업 베이스 기록 실패', en: 'The work base record could not be saved. Retry.' },
  merge_conflict_open: { ko: '다른 Flow 충돌이 베이스 사용 중', en: 'Another Flow\'s conflict holds the base. Resolve or abort it first.', fixes: ['station'] },
  git_busy: { ko: '다른 Git 작업 중', en: 'Another Git operation is running. Retry in a moment.' },
  group_lease_active: { ko: 'AI 작업 중', en: 'An AI task holds this Flow. Retry when it ends.' },
  run_already_active: { ko: 'AI 실행 중 · Git 잠김', en: 'An AI run is active, so Git changes are locked. Retry when it ends.' },
  branch_merge_claim_active: { ko: '브랜치 병합 중', en: 'A branch merge is in progress. Retry when it ends.' },
  invalid_state: { ko: '끝나지 않은 받아오기 있음', en: 'An update is still open. Check the status and finish or abort it first.', fixes: ['status'] },
  git_unavailable: { ko: '서버 Git 사용 불가 · 관리자 문의', en: 'Git is not available on the server. Contact an administrator.' },
  group_untracked_conflict: { ko: '작업 파일이 받아오기를 막음', en: 'Files in the workspace block the update. Commit or clean them up, then retry.', fixes: ['files'] },
  network: { ko: '서버 연결 실패', en: 'Could not reach the server. Check the network and retry.' },
  forbidden: { ko: '권한 없음', en: 'You do not have permission for this action.' },
  update_unconfirmed: { ko: '받아오기 결과 확인 안 됨', en: 'The update result could not be confirmed. Check the status and retry.', fixes: ['status'] },
  update_unavailable: { ko: '받아오기 미지원 서버', en: 'This server does not support update.' },
  status_unavailable: { ko: 'Git 상태 읽기 실패', en: 'Could not read the Git status. Check it and retry.', fixes: ['status'] },
  nothing_selected: { ko: '선택한 파일 없음', en: 'No files are selected. Pick files to commit.', fixes: ['files'] },
  nothing_staged: { ko: 'Stage된 파일 없음', en: 'Nothing is staged.', fixes: ['files'] },
  commit_unconfirmed: { ko: '커밋 결과 확인 안 됨', en: 'The commit could not be confirmed. Check the status.', fixes: ['status'] },
  merge_unconfirmed: { ko: '병합 결과 확인 안 됨', en: 'The merge could not be confirmed. Check the merge details.', fixes: ['merge'] },
  push_unconfirmed: { ko: '푸시 결과 확인 안 됨', en: 'The push could not be confirmed. Check the merge details.', fixes: ['merge'] },
  merge_blocked: { ko: '지금 병합 불가', en: 'The merge is blocked now. See why in the merge details.', fixes: ['merge'] },
  push_blocked: { ko: '지금 푸시 불가', en: 'The push is blocked now. See why in the merge details.', fixes: ['merge'] },
  remote_sync_required: { ko: '원격 반영 필요', en: 'The remote must be synced first. Retry.' },
  worktree_changed: { ko: '실행 중 파일이 바뀜', en: 'The files changed during the run. Check them, then retry.', fixes: ['files'] },
  context_changed: { ko: 'Flow가 바뀜', en: 'The Flow changed, so the run stopped.' },
  request_failed: { ko: '요청 실패', en: 'The request failed. Retry.' },
  push_authentication_required: { ko: '푸시 권한 없음 · 인증 정보 확인', en: 'Push permission denied. Check the credentials in Git settings.', fixes: ['settings'] },
  push_protected_branch: { ko: '보호 브랜치 · 푸시 불가', en: 'The branch is protected. Check the repository rules.' },
  push_rejected: { ko: '푸시 거절됨 · 원격에 새 커밋', en: 'The remote rejected the push. Sync the remote changes and push again.' },
  merge_conflict: { ko: '충돌 · 위에서 해결 후 계속', en: 'There is a conflict. Resolve it in the card above, then continue.' },
  uncommitted_changes: { ko: '커밋 안 한 파일 있음', en: 'Uncommitted changes block the update. Commit all and continue.', fixes: ['files'] },
  commit_message_required: { ko: '커밋 메시지 필요 (AI 작성 꺼짐)', en: 'Enter a commit message (AI writing is off).' },
  queued: { ko: '다른 Git 작업 대기 중', en: 'Queued behind another Git operation. Retry in a moment.' },
}
const words = computed(() => locale.value.startsWith('ko') ? ko : en)
const scopes: GitExecutionScope[] = ['commit', 'merge', 'push']
const scope = ref<GitExecutionScope>('push')
const filesOpen = ref(false)
const excluded = ref<string[]>([])
const search = ref('')
const targetSearch = ref('')
const filteredTargets = computed(() => wt.state.mergeTargets.filter(x => x.name === wt.state.mergeTarget || x.name.toLowerCase().includes(targetSearch.value.toLowerCase())).sort((a,b) => Number(b.name === wt.state.mergeTarget)-Number(a.name === wt.state.mergeTarget)))
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
function reasonText(code: string | null | undefined): string | null {
  const r = code ? REASONS[code] : undefined
  return r ? (locale.value.startsWith('ko') ? r.ko : r.en) : null
}
const resultTitle = computed(() => {
  const p = plan.value
  if (!p) return ''
  if (p.status === 'complete' || p.status === 'remaining') return words.value[p.status]
  const step = p.steps[p.cursor]
  return words.value.stoppedAt.replace('{step}', step ? words.value[step] : '—')
})
const resultText = computed(() => reasonText(plan.value?.error) ?? words.value.stopped)
const stopFixes = computed<Fix[]>(() => (plan.value?.error && REASONS[plan.value.error]?.fixes) || [])
// The result of an individual update / merge / push, drawn here as well as in the Git window.
const noticeLines = computed(() => (['apply', 'push'] as const).flatMap(area => {
  const n = wt.state.notices[area]
  if (!n) return []
  const params = n.params ?? {}
  const failedCode = n.code === 'failed' ? String(params.code ?? '') : ''
  const known = reasonText(failedCode) ?? (n.tone === 'error' ? reasonText(n.code) : null)
  const text = known ? (failedCode ? `${known} · ${failedCode}` : known) : t(`gui.git.worktree.notice.${n.code}`, { ...params })
  const tone = n.tone === 'error' ? 'error' : ['merge_conflict', 'queued', 'judgment_changed'].includes(n.code) ? 'warn' : 'ok'
  return [{ area, text, tone }]
}))
function stepMark(index: number): string {
  const p = plan.value
  if (!p) return ''
  if (index < p.cursor) return '✓ '
  if (paused.value && index === p.cursor) return '! '
  return ''
}
function runFix(fix: Fix) {
  if (fix === 'settings') emit('settings')
  else if (fix === 'station') emit('station')
  else emit('details', fix)
}
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
.gq button { cursor:pointer; padding:3px 8px; transition:background .12s, border-color .12s, color .12s }
.gq button:not(:disabled):hover { border-color:#5b8fcf; background:#18304d }
.gq button:disabled { opacity:.35; cursor:not-allowed; filter:grayscale(.6) }
.gq input:not([type=checkbox]), .gq select { flex:1; min-width:80px; padding:4px 7px; max-width:100% }
.gq-scopes { display:flex }.gq-scopes button { border-radius:0; font-size:11px; color:#9fb3cc }.gq-scopes button:first-child { border-radius:5px 0 0 5px }.gq-scopes button:last-child { border-radius:0 5px 5px 0 }
.gq-scopes button[aria-pressed=true] { background:#2f80ed; border-color:#2f80ed; color:white; font-weight:700 }
.gq .gq-run { margin-left:auto; display:inline-flex; align-items:center; gap:6px; background:#2f80ed; border-color:#2f80ed; color:white; font-weight:700; min-width:72px; box-shadow:0 1px 6px rgba(47,128,237,.35) }
.gq .gq-run:not(:disabled):hover { background:#4a93f5; border-color:#4a93f5 }
.gq .gq-run.is-busy { opacity:1; filter:none; background:#24466e; border-color:#2f80ed }
.gq-chain { display:flex; flex-wrap:wrap; gap:5px; color:#8ea3bd; font-size:11px }.gq-chain span { padding:3px 7px; border-radius:4px; background:#15253b; border:1px solid transparent }
.gq-chain .done { color:#66dfb0; border-color:#1f5a46 }.gq-chain .now { color:white; background:#24466e; border-color:#2f80ed }.gq-chain .failed { color:#ffd0d7; background:#4a1d27; border-color:#d9506a; font-weight:700 }
.gq-pick { display:flex; flex-direction:column; gap:6px }.gq-list { max-height:192px; overflow:auto; border:1px solid #22344d; border-radius:5px; background:#0a1524 }.gq-pick label { display:flex; align-items:center; gap:6px; height:24px; padding:0 5px }.gq-pick label span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.gq-result { display:flex; align-items:flex-start; flex-wrap:wrap; gap:8px; color:#ffc072; font-size:12px; padding:8px; border-radius:6px; border:1px solid #6b5426; background:#2a2312 }
.gq-result.success { color:#66dfb0; border-color:#1f5a46; background:#0f2a22 }
.gq-result.stopped { color:#ffd0d7; border-color:#d9506a; background:#2d141b }
.gq-result-text { flex:1; display:flex; flex-direction:column; gap:3px; min-width:160px }.gq-result-text span { color:#f0dfe3; line-height:1.4 }
.gq-code { color:#b99aa2; font-family:ui-monospace, monospace; font-size:10.5px; word-break:break-all }
.gq-fixes { display:flex; flex-wrap:wrap; gap:6px; width:100%; justify-content:flex-end }
.gq .gq-fix { font-size:11px; padding:3px 10px }
.gq .gq-primary { background:#2f80ed; border-color:#2f80ed; color:white; font-weight:700 }
.gq .gq-primary:not(:disabled):hover { background:#4a93f5; border-color:#4a93f5 }
.gq .gq-close { min-height:22px; padding:0 7px; background:transparent; border-color:transparent; color:inherit }
.gq-individual summary { cursor:pointer; color:#8ea3bd; padding:3px 0 }
.gq-action { display:flex; align-items:center; gap:8px; padding:7px 8px; border:1px solid #22344d; border-radius:7px; margin-top:6px; min-width:0; background:#0d1a2b }
.gq-action b { flex:0 0 60px }.gq-action > span { flex:1; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; color:#8ea3bd; font-size:11px }
.gq .gq-icon { width:28px; padding:0 }
.gq .gq-act { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-width:84px; font-size:11px; font-weight:600; color:#9fb3cc }
.gq .gq-act.ready { background:#163a63; border-color:#3f7fcc; color:#e6f1ff }
.gq .gq-act.ready:hover { background:#1e4b80; border-color:#5b9be6 }
.gq .gq-act.running { opacity:1; filter:none; background:#24466e; border-color:#2f80ed; color:white }
.gq-spin { width:10px; height:10px; border:2px solid rgba(255,255,255,.35); border-top-color:white; border-radius:50%; animation:gq-spin .8s linear infinite; flex:none }
@keyframes gq-spin { to { transform:rotate(360deg) } }
.gq-confirm { margin-top:8px }.gq-confirm span { flex:1 }
.gq-notice { display:flex; align-items:center; gap:6px; margin:0; padding:6px 8px; border-radius:6px; font-size:12px; border:1px solid }
.gq-notice span { flex:1 }
.gq-notice.is-ok { color:#66dfb0; border-color:#1f5a46; background:#0f2a22 }
.gq-notice.is-warn { color:#ffc072; border-color:#6b5426; background:#2a2312 }
.gq-notice.is-error { color:#ffb3bf; border-color:#d9506a; background:#2d141b }
.gq-muted { color:#8ea3bd; margin:0 }
</style>
