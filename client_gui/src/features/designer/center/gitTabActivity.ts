// flowgate.v02test.0068 — what the Git panel is doing for a Flow, for that Flow's tab: a turning Git mark while a run
// (실행) or an individual 받아오기 / 병합 / 푸시 goes on, then its result — done ✓ (green), 받아오기 passed over ! (yellow),
// stopped / failed ! (red) — with the step and the reason on hover. The Git panel (DesignerGitPanel) emits
// `git-activity`, the right Dock (the one Designer file that assembles the Git part) writes it here, the tab strip
// reads it. The result stays even while its tab is the one shown (the person may be looking at the
// Canvas, not the Git panel). This store is a display cache: the observer restores durable server runs
// for every open Flow after navigation or reload; closing a tab stops observing it.
import { reactive, readonly } from 'vue'
import { fetchGitExecution } from '@/features/git'
/** The Git steps a tab names (the Git part's execution steps; the Designer keeps its own copy of the names). */
export type GitTabStep = 'commit' | 'update' | 'merge' | 'push'

export type GitTabActivityState = 'running' | 'done' | 'skipped' | 'failed'

export interface GitTabActivity {
  readonly state: GitTabActivityState
  /** The step running or stopped at (null = the whole run / unknown). */
  readonly step: GitTabStep | null
  /** The server's code behind a stop or a passed-over 받아오기. */
  readonly code: string | null
}

const store = reactive<Record<string, GitTabActivity>>({})

/** Each Flow's (group's) Git activity by flow id. */
export const gitTabActivity: Readonly<Record<string, GitTabActivity>> = readonly(store) as Readonly<Record<string, GitTabActivity>>

export function setGitTabActivity(flowId: string, activity: GitTabActivity | null): void {
  if (activity) store[flowId] = activity
  else delete store[flowId]
}

/** Drops a finished result (a running mark stays: the run is still going). */
export function clearGitTabResult(flowId: string): void {
  const now = store[flowId]
  if (now && now.state !== 'running') delete store[flowId]
}

/** Drops every Flow not in `open` (closed tabs). */
export function pruneGitTabActivity(open: ReadonlySet<string>): void {
  for (const flowId of Object.keys(store)) if (!open.has(flowId)) delete store[flowId]
}

/** Observe every open Flow, including ones whose Git panel is unmounted. */
export function observeGitTabActivity(openFlows: () => readonly string[]): () => void {
  let disposed = false
  let timer: ReturnType<typeof setTimeout> | null = null
  const poll = async () => {
    await Promise.all(openFlows().map(async (flowId) => {
      try {
        const run = await fetchGitExecution(flowId)
        if (disposed || !openFlows().includes(flowId) || !run) return
        const skipped = run.skipped?.update ?? null
        setGitTabActivity(flowId, {
          state: run.status === 'running' ? 'running' : run.status === 'complete'
            ? skipped ? 'skipped' : 'done' : 'failed',
          step: run.steps[run.cursor] ?? null,
          code: run.error ?? skipped,
        })
      } catch { /* A read failure is not evidence that the server run stopped. */ }
    }))
    if (!disposed) timer = setTimeout(() => { void poll() }, 3000)
  }
  void poll()
  return () => { disposed = true; if (timer !== null) clearTimeout(timer) }
}

type Lang = 'ko' | 'en' | 'ja'
const WORDS: Record<Lang, Record<string, string>> = {
  ko: { running: 'Git 실행 중', done: 'Git 완료', skipped: 'Git 완료 · 받아오기 건너뜀', failed: 'Git 멈춤', commit: '커밋', update: '받아오기', merge: '병합', push: '푸시', code: '코드' },
  en: { running: 'Git running', done: 'Git done', skipped: 'Git done · update skipped', failed: 'Git stopped', commit: 'Commit', update: 'Update', merge: 'Merge', push: 'Push', code: 'code' },
  ja: { running: 'Git 実行中', done: 'Git 完了', skipped: 'Git 完了 · 取り込みスキップ', failed: 'Git 停止', commit: 'コミット', update: '取り込み', merge: 'マージ', push: 'プッシュ', code: 'コード' },
}

/** The tab mark's hover text: state · step · code (e.g. 「Git 멈춤 · 병합 · 코드: merge_conflict」). */
export function gitTabActivityLabel(activity: GitTabActivity, locale: string): string {
  const w = WORDS[locale.startsWith('ko') ? 'ko' : locale.startsWith('ja') ? 'ja' : 'en']
  const parts = [w[activity.state]]
  if (activity.step) parts.push(w[activity.step])
  if (activity.code) parts.push(`${w.code}: ${activity.code}`)
  return parts.join(' · ')
}
