<template>
  <div class="gq" data-test="git-quick-actions">
    <section class="one" :class="{ off: !!lockText && !plan }">
      <div class="otop">
        <div class="mseg" role="group" :aria-label="words.scope" data-test="git-scopes">
          <button v-for="item in scopes" :key="item" type="button" :class="{ on: scope === item }" :disabled="busy || paused || waiting" :aria-pressed="scope === item" @click="scope = item">{{ words[item + 'Scope'] }}</button>
        </div>
        <button type="button" class="big" data-test="git-execute" :disabled="disabled || paused" @click="execute(false)"><span v-if="busy && plan" class="spin"><GitIcon name="spin" :size="13" /></span>{{ busy && plan ? words.running : words.execute }}</button>
      </div>
      <template v-if="plan && (busy || paused || (waiting && !openSession))">
        <div class="chain" data-test="git-execution-steps">
          <template v-for="(step, index) in plan.steps" :key="index">
            <span v-if="index" class="ar">→</span>
            <span class="st" :class="stepClass(index)"><span v-if="stepClass(index) === 'now'" class="spin"><GitIcon name="spin" :size="11" /></span><GitIcon v-else-if="stepClass(index) === 'done'" name="ok" :size="11" />{{ words[step] }}</span>
          </template>
        </div>
        <div v-if="paused" class="bar gq-stop" role="status" data-test="git-execution-result">
          <span class="why" data-test="git-stop-reason" :title="plan.error ? words.code + ': ' + plan.error + (plan.detail ? ' · ' + plan.detail : '') : undefined">{{ resultTitle }} · {{ resultText }}</span>
          <button v-for="fix in stopFixes" :key="fix" type="button" class="tb gq-ghost" :data-test="'git-fix-' + fix" @click="runFix(fix)">{{ words['fix_' + fix] }}</button>
          <button v-if="plan.error === 'uncommitted_changes'" type="button" class="tb" :disabled="disabled" @click="commitRemaining">{{ words.commitContinue }}</button>
          <button v-if="plan.error !== 'merge_conflict' || !conflict" type="button" class="tb" data-test="git-retry" :disabled="disabled" @click="execute(true)">{{ plan.error === 'push_rejected' ? words.syncPush : plan.error === 'merge_conflict' ? words.continue : words.retry }}</button>
          <button type="button" class="ib sm gh2" data-test="git-cancel" :title="words.cancelRun" :aria-label="words.cancelRun" @click="wt.clearExecution()"><GitIcon name="x" :size="13" /></button>
        </div>
        <!-- flowgate.v02test.0068: the server run waits on a conflict session (받아오기 or 병합). Say so instead of
             「멈춤」, and host the conflict card right here: the run goes on by itself once the session is resolved. -->
        <div v-else-if="waiting" class="bar gq-stop" role="status" data-test="git-execution-waiting" :data-ai="plan.conflictAi?.state ?? undefined">
          <span v-if="plan.conflictAi?.state === 'running'" class="spin"><GitIcon name="spin" :size="13" /></span>
          <span class="why" :title="waitTitle">{{ waitText }}</span>
        </div>
        <template v-if="hostsCard">
          <GitConflictCard v-if="session.groupCard.value === 'conflict' || session.groupCard.value === 'unsupported'" :unsupported="session.groupCard.value === 'unsupported'" />
          <GitAiResolvingCard v-else-if="session.groupCard.value === 'ai'" />
          <GitReviewCard v-else-if="session.groupCard.value === 'review'" />
        </template>
      </template>
      <template v-else-if="lockText">
        <div class="ad" role="status" data-test="git-lock-reason">{{ lockText }}</div>
        <!-- flowgate.v02test.0068: an open Git session no card shows still blocks every action — say which one and give
             the way out here (abort it) instead of a bare code. The Git station is the footer icon and reading again is
             the header's refresh, so neither is repeated on this line. -->
        <template v-if="openSession">
          <div class="bar gq-session" data-test="git-open-session" :data-session-kind="openSession.sessionKind ?? undefined">
            <span class="why">{{ words.sessionHint }}</span>
            <span v-if="sessionBusy" class="spin"><GitIcon name="spin" :size="13" /></span>
            <button v-else-if="openSession.mergeId !== null" type="button" class="gq-link" :class="{ 'gq-danger': abortAsk }" data-test="git-session-abort" :disabled="session.state.readOnly" @click="abortSession">{{ abortAsk ? words.sessionAbortSure : words.sessionAbort }}</button>
          </div>
          <div v-if="abortRefused" class="gq-refused" role="alert" data-test="git-session-refused">{{ abortRefused }}</div>
        </template>
      </template>
      <template v-else>
        <div v-if="plan" class="res" :class="plan.status === 'complete' ? 'ok' : 'left'" role="status" data-test="git-execution-result" :title="skipTitle"><GitIcon v-if="plan.status === 'complete'" name="ok" :size="13" />{{ resultTitle }}</div>
        <div class="chain" data-test="git-execution-steps">
          <template v-for="(step, index) in idleSteps" :key="step">
            <span v-if="index" class="ar">→</span>
            <span v-if="step === 'commit'" class="st gq-files" :class="{ skip: !selectedCount }" role="button" tabindex="0" :aria-expanded="filesOpen" @click="filesOpen = !filesOpen" @keydown.enter="filesOpen = !filesOpen"><span>{{ words.files }} <b>{{ selectedCount }}</b>/<span data-test="git-worktree-summary-changed">{{ wt.state.status?.counts.changed ?? '—' }}</span></span></span>
            <span v-else-if="step === 'update'" class="st"><span>{{ words.update }}<template v-if="behind">{{ ' ' }}<b>{{ behind }}</b></template></span></span>
            <span v-else-if="step === 'merge'" class="st" :class="{ skip: !mergeTodo }"><span>{{ wt.state.mergeTarget ?? '—' }}<template v-if="ahead !== 0">{{ ' ' }}<b data-test="git-summary-merge-ahead">{{ ahead ?? '—' }}</b></template></span></span>
            <span v-else class="st" :class="{ skip: !pushTodo }"><span>origin<template v-if="unpushed > 0">{{ ' ' }}<b data-test="git-summary-push-ahead">{{ unpushed }}</b></template></span></span>
          </template>
        </div>
        <div v-if="files.length" class="bar"><input class="inp" data-test="git-message" :value="wt.state.message" :disabled="busy" :placeholder="words.message" :aria-label="words.message" @input="wt.setMessage(($event.target as HTMLInputElement).value)" /></div>
        <template v-if="filesOpen && files.length">
          <div v-if="files.length > 8" class="bar"><input v-model="search" class="inp" data-test="git-file-search" :placeholder="words.search" :aria-label="words.search" /><span class="cnt">{{ filteredFiles.length }}/{{ files.length }}</span></div>
          <label class="li gq-all"><input type="checkbox" :checked="selectedCount === files.length" :disabled="busy" @change="toggleAll" /><span class="t">{{ words.all }}</span><span class="r">{{ selectedCount }}/{{ files.length }}</span></label>
          <div class="list" data-test="git-file-list">
            <label v-for="file in filteredFiles" :key="file.path" class="li" :title="file.path">
              <input type="checkbox" :checked="!excluded.includes(file.path)" :disabled="busy" @change="toggle(file.path)" />
              <span class="fl" :class="'l' + letter(file)">{{ letter(file) }}</span><span class="t">{{ file.path }}</span>
            </label>
          </div>
        </template>
      </template>
    </section>

    <div class="fold" role="button" tabindex="0" data-test="git-individual" :aria-expanded="each" @click="each = !each" @keydown.enter="each = !each"><GitIcon :name="each ? 'down' : 'next'" :size="13" />{{ words.individual }}</div>
    <div v-show="each" class="acts" data-test="git-individual-rows">
      <div class="act" :class="{ off: !commitOn && open !== 'commit' }">
        <div class="atop">
          <span class="aico"><GitIcon name="commit" :size="16" /></span><b>{{ words.commit }}</b>
          <span class="ad"><template v-if="rowLock">{{ rowLock }}</template><template v-else-if="dirty">{{ words.changedFiles }} <em>{{ dirty }}</em></template><template v-else>{{ words.noCommit }}</template></span>
          <span v-if="wt.state.busy === 'commit'" class="spin"><GitIcon name="spin" /></span>
          <button v-else type="button" class="ib" :class="{ pri: open === 'commit' }" data-test="git-open-commit" :disabled="!commitOn" :title="words.commit" :aria-label="words.commit" @click="toggleOpen('commit')"><GitIcon name="commit" /></button>
        </div>
        <div v-if="open === 'commit' && commitOn" class="sub">
          <div v-if="files.length > 8" class="bar"><input v-model="search" class="inp" :placeholder="words.search" :aria-label="words.search" /><span class="cnt">{{ filteredFiles.length }}/{{ files.length }}</span></div>
          <label class="li gq-all"><input type="checkbox" :checked="selectedCount === files.length" @change="toggleAll" /><span class="t">{{ words.all }}</span><span class="r">{{ selectedCount }}/{{ files.length }}</span></label>
          <div class="list">
            <label v-for="file in filteredFiles" :key="file.path" class="li" :title="file.path">
              <input type="checkbox" :checked="!excluded.includes(file.path)" @change="toggle(file.path)" />
              <span class="fl" :class="'l' + letter(file)">{{ letter(file) }}</span><span class="t">{{ file.path }}</span>
              <button type="button" class="gq-diff" data-test="git-worktree-entry" data-tab="files" :title="words.fileDetails" :aria-label="words.fileDetails" @click.prevent="emit('details', 'files')"><GitIcon name="diff" :size="12" /></button>
            </label>
          </div>
          <div class="bar">
            <input class="inp" :value="wt.state.message" :placeholder="words.commitMessage" :aria-label="words.commitMessage" @input="wt.setMessage(($event.target as HTMLInputElement).value)" />
            <button type="button" class="ib pri" data-test="git-action-commit" :disabled="disabled || !selectedCount || !wt.state.message.trim()" :title="words.commit" :aria-label="words.commit" @click="commitNow"><GitIcon name="ok" /></button>
          </div>
        </div>
      </div>

      <div class="act" :class="{ off: !updateOn || behind === 0 }">
        <div class="atop">
          <span class="aico"><GitIcon name="fetch" :size="16" /></span><b>{{ words.update }}</b>
          <span class="ad"><template v-if="rowLock">{{ rowLock }}</template><template v-else-if="behind !== 0">{{ words.baseChanged }} <em>{{ behind ?? '—' }}</em><template v-if="dirty"> · <i>{{ words.commitFirst }}</i></template></template><template v-else>{{ words.noFetch }}</template></span>
          <span v-if="wt.state.busy === 'update'" class="spin"><GitIcon name="spin" /></span>
          <button v-else type="button" class="ib" data-test="git-action-update" :disabled="!updateOn" :title="words.update" :aria-label="words.update" @click="wt.updateFromBase()"><GitIcon name="fetch" /></button>
        </div>
      </div>

      <div class="act" :class="{ off: !mergeOn && open !== 'merge' }">
        <div class="atop">
          <span class="aico"><GitIcon name="merge" :size="16" /></span><b>{{ words.merge }}</b>
          <span class="ad"><template v-if="rowLock">{{ rowLock }}</template><template v-else-if="ahead !== 0">{{ words.commits }} <em data-test="git-worktree-summary-merge">{{ ahead ?? '—' }}</em> → {{ wt.state.mergeTarget ?? '—' }}<template v-if="dirty"> · <i>{{ words.dirtyExcluded.replace('{n}', String(dirty)) }}</i></template></template><template v-else><span data-test="git-worktree-summary-merge" hidden>0</span>{{ words.noMerge }}</template></span>
          <span v-if="wt.state.busy === 'merge'" class="spin"><GitIcon name="spin" /></span>
          <button v-else type="button" class="ib" :class="{ pri: open === 'merge' }" data-test="git-open-merge" :disabled="!mergeOn" :title="words.merge" :aria-label="words.merge" @click="toggleOpen('merge')"><GitIcon name="merge" /></button>
        </div>
        <div v-if="open === 'merge' && mergeOn" class="sub">
          <div class="bar"><span class="cnt">{{ words.target }}</span><button type="button" class="pick" :aria-expanded="branchOpen" @click="branchOpen = !branchOpen"><span>{{ wt.state.mergeTarget ?? '—' }}</span><GitIcon name="down" :size="13" /></button></div>
          <template v-if="branchOpen">
            <div v-if="wt.state.mergeTargets.length > 8" class="bar"><input v-model="targetSearch" class="inp" :placeholder="words.targetSearch" :aria-label="words.targetSearch" /><span class="cnt">{{ filteredTargets.length }}/{{ wt.state.mergeTargets.length }}</span></div>
            <div class="list">
              <button v-for="target in filteredTargets" :key="target.name" type="button" class="li" :class="{ sel: target.name === wt.state.mergeTarget }" :disabled="!target.eligible" @click="pickTarget(target.name)"><span class="t">{{ target.name }}</span><span class="r">{{ target.isWorkBase ? words.baseTag : '' }}</span></button>
            </div>
          </template>
          <div class="bar">
            <span class="ad">{{ words.commits }} <em>{{ ahead ?? '—' }}</em> → {{ wt.state.mergeTarget ?? '—' }}</span>
            <button type="button" class="ib" data-test="git-worktree-entry" data-tab="merge" :title="words.mergeDetails" :aria-label="words.mergeDetails" @click="emit('details', 'merge')"><GitIcon name="diff" /></button>
            <button type="button" class="ib pri" data-test="git-action-merge" :disabled="disabled || wt.reasons.value.merge.disabled" :title="words.merge" :aria-label="words.merge" @click="mergeNow"><GitIcon name="ok" /></button>
            <button type="button" class="ib gh2" :title="words.close" :aria-label="words.close" @click="open = null"><GitIcon name="x" /></button>
          </div>
        </div>
      </div>

      <div class="act" :class="{ off: !pushOn && open !== 'push' }">
        <div class="atop">
          <span class="aico"><GitIcon name="push" :size="16" /></span><b>{{ words.push }}</b>
          <span class="ad"><template v-if="rowLock">{{ rowLock }}</template><template v-else-if="unpushed">{{ words.toPush }} <em>{{ unpushed }}</em></template><template v-else>{{ words.noPush }}</template></span>
          <span v-if="wt.state.busy === 'push'" class="spin"><GitIcon name="spin" /></span>
          <button v-else type="button" class="ib" :class="{ pri: open === 'push' }" data-test="git-open-push" :disabled="!pushOn" :title="words.push" :aria-label="words.push" @click="toggleOpen('push')"><GitIcon name="push" /></button>
        </div>
        <div v-if="open === 'push' && pushOn" class="sub">
          <div class="bar">
            <span class="ad">{{ words.commits }} <em>{{ unpushed }}</em> → origin/{{ wt.pushRow.value?.target.targetBranch ?? '—' }}</span>
            <button type="button" class="ib pri" data-test="git-action-push" :disabled="disabled || wt.reasons.value.push.disabled" :title="words.push" :aria-label="words.push" @click="pushNow"><GitIcon name="ok" /></button>
            <button type="button" class="ib gh2" :title="words.close" :aria-label="words.close" @click="open = null"><GitIcon name="x" /></button>
          </div>
        </div>
      </div>
    </div>

    <div class="gq-last">
      <p v-for="line in noticeLines" :key="line.area" class="gq-notice" :class="'is-' + line.tone" role="status" :data-test="'git-quick-notice-' + line.area">{{ words.just }} · <b>{{ line.text }}</b></p>
    </div>
  </div>
</template>

<script setup lang="ts">
// flowgate.v02test.0068 — the Git tab's run box and individual actions, laid out as the approved
// v16 mockup (mirageglass 9cn8mjxm/v16): scope + 실행, one step chain, the message field, a folded
// 개별 실행 list whose rows are icon · name · state · one icon button, the whole row dimmed when it
// has nothing to do. A blocked tab says why in that place instead of a bare 「작업 중」.
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useGitWorktree } from './gitWorktreeSession'
import { useGitPanel } from './gitSession'
import GitConflictCard from './GitConflictCard.vue'
import GitAiResolvingCard from './GitAiResolvingCard.vue'
import GitReviewCard from './GitReviewCard.vue'
import GitIcon from './GitIcon.vue'
import { executionSteps, type GitConflictAi, type GitExecutionScope } from './gitExecution'
import type { WorktreeFile, WorktreeSelection } from './types'
type Fix = 'settings' | 'station' | 'status' | 'files' | 'merge'
type Row = 'commit' | 'merge' | 'push'
const emit = defineEmits<{ details: [tab: 'files' | 'merge' | 'status']; settings: []; station: [] }>()
const { worktree: wt } = useGitWorktree()
const { session } = useGitPanel()
const { locale, t } = useI18n()
const ko: Record<string, string> = { targetSearch: '브랜치 찾기', fileDetails: '파일 상세', mergeDetails: '병합 상세', syncPush: '원격 반영 후 푸시', scope: '실행 범위', commitScope: '커밋만', mergeScope: '병합까지', pushScope: '푸시까지', execute: '실행', running: '실행 중', files: '파일', message: '커밋 메시지 · 비우면 AI 작성', commitMessage: '커밋 메시지', target: '대상', search: '파일 찾기', all: '전체 선택', individual: '개별 실행', commit: '커밋', update: '받아오기', merge: '병합', push: '푸시', continue: '계속', retry: '다시 시도', cancelRun: '중단', complete: '완료', remaining: '완료 · 남은 변경 있음', stoppedAt: '{step}에서 멈춤', waitConflict: '{step} 충돌 · 아래 카드에서 해결하면 이어서 진행', waitClosed: '{step} 충돌 · 상태 확인 중', waitAi: '{step} 충돌 · AI 해결 중 · 다른 탭에 가도 계속됨', waitAiFailed: '{step} 충돌 · AI 해결 실패 · {why}', aiWhyDropped: '한쪽 변경이 빠짐', aiWhyLines: '빠진 줄 {n}개', aiWhyMarkers: '충돌 표시가 남음', aiWhyEnded: '해결하지 못하고 끝남', aiWhyCancelled: '중지됨', stopped: '알 수 없는 이유', code: '코드', commitFirst: '커밋 필요', commitContinue: '커밋하고 계속', close: '닫기', changedFiles: '변경 파일', noCommit: '커밋할 파일 없음', baseChanged: '베이스 변경', noFetch: '받을 커밋 없음', commits: '커밋', dirtyExcluded: '미커밋 파일 {n} 제외', noMerge: '병합할 커밋 없음', toPush: '푸시할 커밋', noPush: '푸시할 커밋 없음', baseTag: '베이스', just: '방금', lockBusy: '실행 중', lockConflict: '충돌 해결 중', lockUnknown: '지금 Git 작업 불가', sessionOpen: '열린 Git 세션', sessionHint: '이 세션을 끝내야 커밋·받아오기·병합·푸시 가능', sessionAbort: '세션 중단', sessionAbortSure: '중단 확인 · 한 번 더', sessionRefused: '중단 안 됨', fix_settings: 'Git 설정 열기', fix_station: 'Git 스테이션 열기', fix_status: '상태 보기', fix_files: '파일 보기', fix_merge: '병합 상세', updateSkipped: '받아오기 건너뜀' }
const en: Record<string, string> = { targetSearch: 'Find branches', fileDetails: 'File details', mergeDetails: 'Merge details', syncPush: 'Sync remote and push', scope: 'Scope', commitScope: 'Commit', mergeScope: 'Through merge', pushScope: 'Through push', execute: 'Run', running: 'Running', files: 'Files', message: 'Commit message · AI writes if blank', commitMessage: 'Commit message', target: 'Target', search: 'Find files', all: 'Select all', individual: 'Individual actions', commit: 'Commit', update: 'Update', merge: 'Merge', push: 'Push', continue: 'Continue', retry: 'Retry', cancelRun: 'Stop', complete: 'Done', remaining: 'Done · changes remain', stoppedAt: 'Stopped at {step}', waitConflict: '{step} conflict · resolve it in the card below and the run continues', waitClosed: '{step} conflict · checking', waitAi: '{step} conflict · AI resolving · continues in other tabs', waitAiFailed: '{step} conflict · AI failed · {why}', aiWhyDropped: 'one side was dropped', aiWhyLines: '{n} lines missing', aiWhyMarkers: 'conflict markers left', aiWhyEnded: 'ended unresolved', aiWhyCancelled: 'stopped', stopped: 'Unknown reason', code: 'Code', commitFirst: 'commit first', commitContinue: 'Commit and continue', close: 'Close', changedFiles: 'Changed files', noCommit: 'Nothing to commit', baseChanged: 'Base changes', noFetch: 'Nothing to update', commits: 'Commits', dirtyExcluded: '{n} uncommitted excluded', noMerge: 'Nothing to merge', toPush: 'To push', noPush: 'Nothing to push', baseTag: 'base', just: 'Just now', lockBusy: 'Running', lockConflict: 'Resolving conflict', lockUnknown: 'Git actions unavailable now', sessionOpen: 'Open Git session', sessionHint: 'End this session to commit, update, merge or push', sessionAbort: 'Abort session', sessionAbortSure: 'Confirm abort · press again', sessionRefused: 'Abort refused', fix_settings: 'Open Git settings', fix_station: 'Open Git station', fix_status: 'View status', fix_files: 'View files', fix_merge: 'Merge details', updateSkipped: 'Update skipped' }
const ja: Record<string, string> = { targetSearch: 'ブランチを検索', fileDetails: 'ファイル詳細', mergeDetails: 'マージ詳細', syncPush: 'リモートを反映してプッシュ', scope: '実行範囲', commitScope: 'コミットのみ', mergeScope: 'マージまで', pushScope: 'プッシュまで', execute: '実行', running: '実行中', files: 'ファイル', message: 'コミットメッセージ · 空欄なら AI が作成', commitMessage: 'コミットメッセージ', target: '対象', search: 'ファイルを検索', all: 'すべて選択', individual: '個別実行', commit: 'コミット', update: '取り込み', merge: 'マージ', push: 'プッシュ', continue: '続行', retry: '再試行', cancelRun: '中断', complete: '完了', remaining: '完了 · 未反映の変更あり', stoppedAt: '{step}で停止', waitConflict: '{step}の競合 · 下のカードで解決すると続行', waitClosed: '{step}の競合 · 状態を確認中', waitAi: '{step}の競合 · AI が解決中 · 他のタブでも続行', waitAiFailed: '{step}の競合 · AI 解決失敗 · {why}', aiWhyDropped: '片側の変更が欠落', aiWhyLines: '欠落 {n} 行', aiWhyMarkers: '競合マーカーが残存', aiWhyEnded: '解決できずに終了', aiWhyCancelled: '停止', stopped: '原因不明', code: 'コード', commitFirst: 'コミット必要', commitContinue: 'コミットして続行', close: '閉じる', changedFiles: '変更ファイル', noCommit: 'コミットするファイルなし', baseChanged: 'ベース変更', noFetch: '取り込むコミットなし', commits: 'コミット', dirtyExcluded: '未コミット {n} 件を除外', noMerge: 'マージするコミットなし', toPush: 'プッシュするコミット', noPush: 'プッシュするコミットなし', baseTag: 'ベース', just: 'たった今', lockBusy: '実行中', lockConflict: '競合を解決中', lockUnknown: '今は Git 作業不可', sessionOpen: '開いている Git セッション', sessionHint: 'このセッションを終えるとコミット・取り込み・マージ・プッシュ可能', sessionAbort: 'セッション中断', sessionAbortSure: '中断を確認 · もう一度', sessionRefused: '中断できない', fix_settings: 'Git 設定を開く', fix_station: 'Git ステーションを開く', fix_status: '状態を見る', fix_files: 'ファイルを見る', fix_merge: 'マージ詳細', updateSkipped: '取り込みスキップ' }
// Why a run stopped, in words, with what can be done about it on this screen.
const REASONS: Record<string, { ko: string; en: string; ja: string; fixes?: Fix[] }> = {
  base_dirty: { ko: '베이스에 커밋 안 한 수정 파일 있음', en: 'Base has uncommitted edits', ja: 'ベースに未コミットの変更あり', fixes: ['status'] },
  git_error: { ko: '원격에서 받지 못함 · 인증 정보나 네트워크 확인', en: 'Fetch failed · check credentials or network', ja: '取得失敗 · 認証情報かネットワークを確認', fixes: ['settings'] },
  base_diverged: { ko: '베이스에 원격에 없는 커밋 있음', en: 'Base has commits not on remote', ja: 'ベースにリモートにないコミットあり', fixes: ['station'] },
  group_work_base_unverified: { ko: '작업 베이스 미확인 · 관리자 확인 필요', en: 'Work base unconfirmed · admin check needed', ja: '作業ベース未確認 · 管理者の確認が必要', fixes: ['station'] },
  group_work_base_diverged: { ko: '작업 베이스 이력 불일치', en: 'Work base history mismatch', ja: '作業ベースの履歴が不一致', fixes: ['station'] },
  group_work_base_source_rewritten: { ko: '작업 베이스가 초기화되거나 이력이 바뀜', en: 'Work base reset or rewritten', ja: '作業ベースがリセットまたは書き換え済み', fixes: ['station'] },
  group_work_base_record_failed: { ko: '작업 베이스 기록 실패', en: 'Work base record failed', ja: '作業ベースの記録に失敗' },
  merge_conflict_open: { ko: '다른 Flow 충돌이 베이스 사용 중', en: 'Another Flow\'s conflict holds the base', ja: '別 Flow の競合がベースを使用中', fixes: ['station'] },
  git_busy: { ko: '다른 Git 작업 중', en: 'Another Git operation running', ja: '別の Git 作業が実行中' },
  group_lease_active: { ko: 'AI 작업 중', en: 'AI task in progress', ja: 'AI 作業中' },
  run_already_active: { ko: 'AI 실행 중 · Git 잠김', en: 'AI run active · Git locked', ja: 'AI 実行中 · Git ロック中' },
  branch_merge_claim_active: { ko: '브랜치 병합 중', en: 'Branch merge in progress', ja: 'ブランチのマージ中' },
  invalid_state: { ko: '끝나지 않은 받아오기 있음', en: 'Unfinished update open', ja: '未完了の取り込みあり', fixes: ['status'] },
  git_unavailable: { ko: '서버 Git 사용 불가 · 관리자 문의', en: 'Git unavailable on server · contact admin', ja: 'サーバーで Git 使用不可 · 管理者に連絡' },
  group_untracked_conflict: { ko: '작업 파일이 받아오기를 막음', en: 'Workspace files block the update', ja: '作業ファイルが取り込みを妨げている', fixes: ['files'] },
  network: { ko: '서버 연결 실패', en: 'Server unreachable', ja: 'サーバー接続失敗' },
  forbidden: { ko: '권한 없음', en: 'No permission', ja: '権限なし' },
  update_unconfirmed: { ko: '받아오기 결과 확인 안 됨', en: 'Update result unconfirmed', ja: '取り込み結果を確認できない', fixes: ['status'] },
  update_unavailable: { ko: '받아오기 미지원 서버', en: 'Server does not support update', ja: '取り込み非対応のサーバー' },
  status_unavailable: { ko: 'Git 상태 읽기 실패', en: 'Could not read Git status', ja: 'Git 状態の読み取り失敗', fixes: ['status'] },
  merge_result_missing: { ko: '병합 결과 없음 · 병합부터 다시 실행', en: 'No merge result · run the merge again', ja: 'マージ結果なし · マージから再実行', fixes: ['merge'] },
  nothing_selected: { ko: '선택한 파일 없음', en: 'No files selected', ja: '選択したファイルなし', fixes: ['files'] },
  nothing_staged: { ko: 'Stage된 파일 없음', en: 'Nothing staged', ja: 'Stage されたファイルなし', fixes: ['files'] },
  commit_unconfirmed: { ko: '커밋 결과 확인 안 됨', en: 'Commit result unconfirmed', ja: 'コミット結果を確認できない', fixes: ['status'] },
  merge_unconfirmed: { ko: '병합 결과 확인 안 됨', en: 'Merge result unconfirmed', ja: 'マージ結果を確認できない', fixes: ['merge'] },
  push_unconfirmed: { ko: '푸시 결과 확인 안 됨', en: 'Push result unconfirmed', ja: 'プッシュ結果を確認できない', fixes: ['merge'] },
  merge_blocked: { ko: '지금 병합 불가', en: 'Merge blocked now', ja: '今はマージ不可', fixes: ['merge'] },
  push_blocked: { ko: '지금 푸시 불가', en: 'Push blocked now', ja: '今はプッシュ不可', fixes: ['merge'] },
  remote_sync_required: { ko: '원격 반영 필요', en: 'Remote sync required', ja: 'リモートの反映が必要' },
  worktree_changed: { ko: '실행 중 파일이 바뀜', en: 'Files changed during run', ja: '実行中にファイルが変更された', fixes: ['files'] },
  context_changed: { ko: 'Flow가 바뀜', en: 'Flow changed', ja: 'Flow が変わった' },
  request_failed: { ko: '요청 실패', en: 'Request failed', ja: 'リクエスト失敗' },
  push_authentication_required: { ko: '푸시 권한 없음 · 인증 정보 확인', en: 'No push permission · check credentials', ja: 'プッシュ権限なし · 認証情報を確認', fixes: ['settings'] },
  push_protected_branch: { ko: '보호 브랜치 · 푸시 불가', en: 'Protected branch · cannot push', ja: '保護ブランチ · プッシュ不可' },
  push_rejected: { ko: '푸시 거절됨 · 원격에 새 커밋', en: 'Push rejected · remote has new commits', ja: 'プッシュ拒否 · リモートに新しいコミット' },
  merge_conflict: { ko: '충돌 · 위에서 해결 후 계속', en: 'Conflict · resolve above, then continue', ja: '競合 · 上で解決して続行' },
  uncommitted_changes: { ko: '커밋 안 한 파일 있음', en: 'Uncommitted files present', ja: '未コミットのファイルあり', fixes: ['files'] },
  commit_message_required: { ko: '커밋 메시지 필요 (AI 작성 꺼짐)', en: 'Commit message required (AI writing off)', ja: 'コミットメッセージが必要 (AI 作成オフ)' },
  queued: { ko: '다른 Git 작업 대기 중', en: 'Waiting for another Git operation', ja: '別の Git 作業を待機中' },
  session_open: { ko: '충돌·받아오기 처리 중 · 위 카드에서 진행', en: 'Conflict / update in progress · continue in the card above', ja: '競合・取り込みの処理中 · 上のカードで進める' },
  // The run's conflict session (받아오기 or 병합) was aborted instead of resolved.
  merge_aborted: { ko: '충돌 세션 중단됨 · 다시 실행하면 처음부터 확인', en: 'Conflict session aborted · run again to start over', ja: '競合セッション中断 · 再実行で最初から確認' },
}
// flowgate.v02test.0068: the open session's kind (git_merge_session.kind), as the Git tab names it.
const SESSION_KINDS: Record<string, { ko: string; en: string; ja: string }> = {
  merge: { ko: '병합 충돌', en: 'merge conflict', ja: 'マージ競合' },
  group_update: { ko: '받아오기 충돌', en: 'update conflict', ja: '取り込み競合' },
  tr_revert: { ko: 'TR 되돌리기 충돌', en: 'TR revert conflict', ja: 'TR 取り消し競合' },
  tr_reapply: { ko: 'TR 다시 적용 충돌', en: 'TR reapply conflict', ja: 'TR 再適用競合' },
  branch_merge: { ko: '브랜치 병합', en: 'branch merge', ja: 'ブランチのマージ' },
  graph_attempt_merge: { ko: '카드 결과 병합', en: 'card result merge', ja: 'カード結果のマージ' },
}
const lang = computed<'ko' | 'ja' | 'en'>(() => locale.value.startsWith('ko') ? 'ko' : locale.value.startsWith('ja') ? 'ja' : 'en')
const words = computed(() => ({ ko, ja, en })[lang.value])
const scopes: GitExecutionScope[] = ['commit', 'merge', 'push']
const scope = ref<GitExecutionScope>('push')
const filesOpen = ref(false)
const each = ref(false)
const open = ref<Row | null>(null)
const branchOpen = ref(false)
const excluded = ref<string[]>([])
const search = ref('')
const targetSearch = ref('')
const filteredTargets = computed(() => wt.state.mergeTargets.filter(x => x.name === wt.state.mergeTarget || x.name.toLowerCase().includes(targetSearch.value.toLowerCase())).sort((a, b) => Number(b.name === wt.state.mergeTarget) - Number(a.name === wt.state.mergeTarget) || Number(b.isWorkBase) - Number(a.isWorkBase)))
const files = computed(() => wt.state.status?.files.filter(f => f.canStage || f.canUnstage) ?? [])
const filteredFiles = computed(() => files.value.filter(f => f.path.toLowerCase().includes(search.value.toLowerCase())))
const selectedCount = computed(() => files.value.filter(f => !excluded.value.includes(f.path)).length)
const plan = computed(() => wt.state.execution)
const paused = computed(() => plan.value?.status === 'stopped')
// flowgate.v02test.0068: the server run is parked on a conflict session (its merge id) until it is resolved or aborted.
const waiting = computed(() => plan.value?.status === 'running' && plan.value.mergeId != null)
// The run box hosts the group's conflict / AI / review card exactly while a run is running or stopped; otherwise the
// Exception list does (DesignerGitPanel passes the same rule to it as `execution-active`), so it is never shown twice.
const hostsCard = computed(() => plan.value?.status === 'running' || paused.value)
const busy = computed(() => !!wt.state.busy || !!session.state.busy)
const conflict = computed(() => ['conflict', 'ai', 'review', 'unsupported'].includes(session.groupCard.value ?? ''))
const blocker = computed(() => wt.state.status?.blockers[0]?.code ?? wt.state.merge?.graph?.blocker ?? null)
const disabled = computed(() => busy.value || conflict.value || !!blocker.value || session.state.readOnly || wt.state.forbidden || wt.state.statusLoad !== 'ready')
// The counts the mockup shows on each row; null = not read yet (the row then stays usable).
const dirty = computed(() => wt.state.status?.counts.changed ?? 0)
const behind = computed<number | null>(() => wt.state.merge?.workBaseBehind ?? null)
const ahead = computed<number | null>(() => wt.state.merge?.aheadOfTarget ?? null)
const unpushed = computed(() => wt.pushRow.value?.remote.localAheadCount ?? 0)
const mergeTodo = computed(() => (ahead.value ?? 1) + (selectedCount.value ? 1 : 0) + (behind.value ? 1 : 0))
const pushTodo = computed(() => unpushed.value + mergeTodo.value)
const idleSteps = computed(() => executionSteps(scope.value))
function reasonText(code: string | null | undefined): string | null {
  const r = code ? REASONS[code] : undefined
  return r ? r[lang.value] : null
}
// What stops the tab now, said in place of the box contents and each row's state (never a bare 「작업 중」).
// flowgate.v02test.0068: the open session that blocks the tab while no conflict / review card shows it.
const openSession = computed(() => {
  if (conflict.value) return null
  const b = wt.state.status?.blockers.find((x) => x.code === 'session_open')
  return b ? { sessionKind: b.sessionKind ?? null, mergeId: b.mergeId ?? null } : null
})
const sessionKindText = computed(() => {
  const kind = openSession.value?.sessionKind
  return kind ? SESSION_KINDS[kind]?.[lang.value] ?? kind : null
})
const sessionBusy = ref(false)
const abortAsk = ref(false)
const abortRefused = ref('')
async function abortSession() {
  const gid = wt.ticket()?.groupId
  const mergeId = openSession.value?.mergeId
  if (!gid || mergeId == null || sessionBusy.value) return
  if (!abortAsk.value) { abortAsk.value = true; abortRefused.value = ''; return }
  abortAsk.value = false
  sessionBusy.value = true
  try {
    const refused = await session.abortOpenSession({ groupId: gid, mergeId })
    abortRefused.value = refused
      ? `${words.value.sessionRefused} · ${reasonText(refused.code) ?? refused.code}${refused.reason ? ' · ' + refused.reason : ''}`
      : ''
  } finally {
    sessionBusy.value = false
    wt.refresh()
  }
}
watch(() => [wt.ticket()?.groupId, openSession.value?.mergeId] as const, () => { abortAsk.value = false; abortRefused.value = '' })
const lockText = computed(() => {
  if (openSession.value) {
    const id = openSession.value.mergeId !== null ? ` #${openSession.value.mergeId}` : ''
    return `${words.value.sessionOpen} · ${sessionKindText.value ?? words.value.lockUnknown}${id}`
  }
  if (blocker.value) return reasonText(blocker.value) ?? `${words.value.lockUnknown} · ${blocker.value}`
  if (conflict.value) return words.value.lockConflict
  if (busy.value) return words.value.lockBusy
  return ''
})
const rowLock = computed(() => lockText.value && !wt.state.busy ? lockText.value : '')
const commitOn = computed(() => !disabled.value && dirty.value > 0)
// The base count is local knowledge: only a fetch learns of new remote commits, so 받아오기 stays
// usable at 0 (the row is dimmed, as the mockup's 「받을 커밋 없음」).
const updateOn = computed(() => !disabled.value && !dirty.value)
const mergeOn = computed(() => !disabled.value && !wt.reasons.value.merge.disabled)
const pushOn = computed(() => !disabled.value && !wt.reasons.value.push.disabled)
const resultTitle = computed(() => {
  const p = plan.value
  if (!p) return ''
  if (p.status === 'complete' || p.status === 'remaining') return words.value[p.status] + (p.skipped?.update ? ' · ' + words.value.updateSkipped : '')
  const step = p.steps[p.cursor]
  return words.value.stoppedAt.replace('{step}', step ? words.value[step] : '—')
})
// Why 받아오기 was passed over: on hover only, as the stop reason's code.
const skipTitle = computed(() => {
  const code = plan.value?.skipped?.update
  return code ? `${reasonText(code) ?? code} · ${words.value.code}: ${code}` : undefined
})
const waitText = computed(() => {
  const p = plan.value
  const step = p?.steps[p.cursor]
  const name = step ? words.value[step] : '—'
  // No card left: the session closed and the server stops or goes on within seconds (the run is read again). An open
  // session no card shows is the lock line's (its kind and [세션 중단]), not this one.
  const ai = p?.conflictAi
  // flowgate.v02test.0068: the AI resolver is said first — it works on the server whichever tab is open, and when it
  // ended with the conflict left the run box says that it FAILED and why, instead of a wait that never ends.
  if (ai?.state === 'running') return words.value.waitAi.replace('{step}', name)
  if (ai?.state === 'failed') return words.value.waitAiFailed.replace('{step}', name).replace('{why}', aiWhy(ai))
  return (conflict.value ? words.value.waitConflict : words.value.waitClosed).replace('{step}', name)
})
function aiWhy(ai: Readonly<GitConflictAi>): string {
  const w = words.value
  if (ai.code === 'conflict_supersede_invalid' && ai.lineCount) return w.aiWhyLines.replace('{n}', String(ai.lineCount))
  if (ai.code === 'conflict_side_dropped' || ai.code === 'conflict_supersede_invalid') return w.aiWhyDropped
  if (ai.code === 'conflict_markers_remain') return w.aiWhyMarkers
  if (ai.code === 'cancelled') return w.aiWhyCancelled
  return w.aiWhyEnded
}
// On hover: the server's code, the file, and the lines the refused resolution had lost.
const waitTitle = computed(() => {
  const ai = plan.value?.conflictAi
  if (ai?.state !== 'failed') return undefined
  return [`${words.value.code}: ${ai.code ?? '—'}`, ai.path, ...(ai.lines ?? [])].filter(Boolean).join('\n')
})
const resultText = computed(() => reasonText(plan.value?.error) ?? (plan.value?.error ? `${words.value.stopped} · ${plan.value.error}` : words.value.stopped))
const stopFixes = computed<Fix[]>(() => (plan.value?.error && REASONS[plan.value.error]?.fixes) || [])
// The result of an individual update / merge / push, as the mockup's 「방금 · …」 line.
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
function stepClass(index: number): string {
  const p = plan.value
  if (!p) return ''
  if (index < p.cursor) return p.skipped?.[p.steps[index]] ? 'skip' : 'done'
  if (index === p.cursor) return paused.value || waiting.value ? 'bad' : busy.value ? 'now' : ''
  return ''
}
function letter(file: WorktreeFile): string {
  return file.staged ?? file.unstaged ?? (file.untracked || !file.inHead ? 'A' : 'M')
}
function runFix(fix: Fix) {
  if (fix === 'settings') emit('settings')
  else if (fix === 'station') emit('station')
  else emit('details', fix)
}
function toggleOpen(row: Row) { open.value = open.value === row ? null : row; branchOpen.value = false }
function pickTarget(name: string) { wt.setMergeTarget(name); branchOpen.value = false }
function selection(): WorktreeSelection { return excluded.value.length ? { paths: files.value.filter(f => !excluded.value.includes(f.path)).map(f => f.path) } : { all: true } }
function toggle(path: string) { excluded.value = excluded.value.includes(path) ? excluded.value.filter(p => p !== path) : [...excluded.value, path] }
function toggleAll() { excluded.value = selectedCount.value === files.value.length ? files.value.map(f => f.path) : [] }
function execute(resume: boolean) { void wt.execute(scope.value, selection(), resume) }
async function commitRemaining() {
  // Explicitly authorized additional commit; the interrupted plan is retained by the session.
  await wt.execute(scope.value, { all: true }, true, true)
}
function commitNow() { open.value = null; void wt.execute('commit', selection()) }
function mergeNow() {
  open.value = null
  wt.setPushAfterMerge(false); const s = wt.mergeSnapshot(); if (s) void wt.merge(s)
}
function pushNow() {
  open.value = null
  const s = wt.pushSnapshot(); if (s) void wt.pushResult(s)
}
watch(() => wt.ticket()?.groupId, () => { excluded.value = []; search.value = ''; filesOpen.value = false; open.value = null; branchOpen.value = false })
watch(() => wt.state.mergeTarget, () => { if (open.value === 'push') open.value = null })
</script>

<style scoped>
/* Values taken from the approved v16 mockup stylesheet (9cn8mjxm/v16) so the tab matches it. */
.gq { display:flex; flex-direction:column; gap:10px; font-size:12.5px; min-width:0; color:#eef5ff }
.gq button { font:inherit }
.one { border:1px solid rgba(75,163,255,.45); background:rgba(75,163,255,.12); border-radius:8px; padding:8px 9px; display:flex; flex-direction:column; gap:7px }
.one.off { border-color:#16263b; background:transparent }
.otop { display:flex; align-items:center; gap:8px }
.mseg { display:flex; border:1px solid #2a3a52; border-radius:6px; overflow:hidden }
.mseg button { border:0; border-right:1px solid #2a3a52; background:transparent; color:#8ea3bd; font-size:11.5px; padding:0 9px; height:26px; cursor:pointer }
.mseg button:last-child { border-right:0 }
.mseg button.on { background:#1d3a63; color:#fff; font-weight:700 }
.mseg button:disabled { cursor:not-allowed }
.big { display:inline-flex; align-items:center; justify-content:center; gap:5px; min-width:64px; height:30px; border-radius:7px; border:1px solid #2f80ed; background:#2f80ed; color:#fff; cursor:pointer; margin-left:auto; padding:0 14px; font-size:12.5px; font-weight:700 }
.big:disabled { opacity:.35; cursor:not-allowed }
.chain { display:flex; align-items:center; gap:6px; font-size:11.5px; color:#b9c9db; flex-wrap:wrap }
.chain .st { display:inline-flex; align-items:center; gap:3px; padding:2px 7px; border-radius:4px; background:#15253b }
.chain .st b { color:#fff }
.chain .st.done { color:#5fe3b3; background:rgba(34,201,140,.12) }
.chain .st.now { color:#fff; background:rgba(75,163,255,.25) }
.chain .st.bad { color:#ffb3bf; background:rgba(255,100,120,.16) }
.chain .st.skip { color:#4a5d77; background:transparent }
.chain .st.skip b { color:inherit }
.chain .ar { color:#4a5d77 }
.gq-files { cursor:pointer }
.bar { display:flex; align-items:center; gap:6px; flex-wrap:wrap }
.inp { flex:1; height:26px; border:1px solid #2a3a52; border-radius:6px; background:#0d1726; color:#eef5ff; padding:0 8px; font:inherit; font-size:12px; min-width:0 }
.inp::placeholder { color:#6f839d }
.cnt { font-size:11px; color:#8ea3bd; white-space:nowrap }
.list { max-height:192px; overflow:auto; border:1px solid #1b2b42; border-radius:6px; background:#08111d }
.li { display:flex; align-items:center; gap:8px; width:100%; height:24px; padding:0 8px; font-size:11.5px; border:0; border-bottom:1px solid #121f31; background:transparent; cursor:pointer; color:#cfdced; text-align:left }
.li:hover { background:#111f33 }
.li.sel { background:rgba(75,163,255,.14); color:#fff }
.li:disabled { opacity:.4; cursor:not-allowed }
.li.gq-all { border:0 }
.li .t { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.li .r { font-size:10.5px; color:#6f839d }
.li input { margin:0; accent-color:#2f80ed }
.fl { width:12px; font-weight:800; font-size:10.5px; text-align:center }
.lM { color:#ffc072 } .lA { color:#22c98c } .lD { color:#ff96a6 }
.gq-diff { display:inline-grid; place-items:center; border:0; background:transparent; color:#6f839d; padding:0; cursor:pointer }
.gq-diff:hover { color:#fff }
.pick { display:flex; align-items:center; gap:6px; height:26px; padding:0 8px; border:1px solid #2a3a52; border-radius:6px; background:#0d1726; color:#eef5ff; cursor:pointer; flex:1; min-width:0; font-size:12px; text-align:left }
.pick span { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap }
.why { font-size:11.5px; color:#ffc072; padding:2px; flex:1; min-width:140px }
.tb { height:24px; padding:0 9px; border-radius:6px; border:1px solid #2f80ed; background:#2f80ed; color:#fff; font-size:11.5px; font-weight:700; cursor:pointer; white-space:nowrap; flex:none }
.tb.gq-ghost { background:#15253b; border-color:#2a3a52; color:#d6e3f2; font-weight:600 }
.tb:disabled { opacity:.35; cursor:not-allowed }
.res { font-size:11.5px; display:flex; align-items:center; gap:5px }
.tb.gq-danger { background:#c43b3b; border-color:#c43b3b }
.gq-session .why { color:#b9c9db }
/* The open session's way out reads like the rest of the panel: thin text, no filled button. */
.gq-link { height:24px; padding:0 4px; border:0; background:transparent; color:#7cc0ff; font-size:11.5px; font-weight:400; cursor:pointer; white-space:nowrap; flex:none }
.gq-link:hover { color:#fff; text-decoration:underline }
.gq-link.gq-danger { color:#ff96a6 }
.gq-link:disabled { opacity:.35; cursor:not-allowed; text-decoration:none }
.gq-refused { font-size:11.5px; color:#ff96a6; padding:0 2px; word-break:break-word }
.res.ok { color:#5fe3b3 } .res.left { color:#ffc072 }
.ad { flex:1; min-width:0; font-size:11.5px; color:#b9c9db; white-space:nowrap; overflow:hidden; text-overflow:ellipsis }
.ad em { font-style:normal; color:#fff; font-weight:700 }
.ad i { font-style:normal; color:#ffc072 }
.fold { display:flex; align-items:center; gap:6px; color:#8ea3bd; font-size:11.5px; cursor:pointer; padding:2px }
.fold:hover { color:#fff }
.acts { display:flex; flex-direction:column; gap:6px }
.act { border:1px solid #22344d; border-radius:8px; background:#0a1422; padding:7px 9px }
.act.off { background:transparent; border-color:#16263b }
.atop { display:flex; align-items:center; gap:9px; min-height:28px }
.aico { display:grid; place-items:center; width:22px; color:#7cc0ff; flex:none }
.act.off .aico { color:#4a5d77 }
.atop b { width:66px; flex:none; font-size:13px }
.act.off .atop b { color:#5d708a }
.act.off .ad { color:#5d708a }
.ib { display:inline-grid; place-items:center; width:28px; height:28px; border-radius:6px; border:1px solid #2a3a52; background:#15253b; color:#d6e3f2; cursor:pointer; flex:none; padding:0 }
.ib:hover { border-color:#4ba3ff }
.ib:disabled { opacity:.35; cursor:not-allowed; border-color:#2a3a52 }
.ib.pri { background:#2f80ed; border-color:#2f80ed; color:#fff }
.ib.gh2 { background:transparent }
.ib.sm { width:24px; height:24px }
.sub { margin-top:7px; display:flex; flex-direction:column; gap:6px }
.spin { animation:gq-spin 1s linear infinite; display:inline-flex }
@keyframes gq-spin { to { transform:rotate(360deg) } }
.gq-last { min-height:16px; display:flex; flex-direction:column; gap:2px }
.gq-notice { margin:0; font-size:11.5px; color:#6f839d; min-height:16px; padding:0 2px }
.gq-notice b { color:#b9c9db; font-weight:600 }
.gq-notice.is-warn b { color:#ffc072 }
.gq-notice.is-error b { color:#ff96a6 }
</style>
