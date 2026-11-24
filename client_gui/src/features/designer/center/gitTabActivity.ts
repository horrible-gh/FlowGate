// flowgate.v02test.0068 — what the Git part is doing for a Flow, for that Flow's tab: a turning Git mark while a run
// (실행) or an individual 받아오기 / 병합 / 푸시 goes on, then its result — done ✓ (green), 받아오기 passed over or work left
// ! (yellow), stopped / failed ! (red) — with the step and the reason on hover. The tab strip reads it from here.
//
//   the Git panel        DesignerGitPanel emits `git-activity` for the Flow it shows; the right Dock (the one Designer
//                        file that assembles the Git part) writes it here with setGitTabActivity.
//   the server run       a run goes on in the server after the panel lets go of it (another Flow shown, the panel not
//                        mounted, a reload). observeGitTabActivity reads each open tab's latest run through the Project
//                        store once when the tab opens, and polls only the runs still going on that the panel is not
//                        reporting itself — one reader per run, never the panel and the tab both.
//
// A result is news only for a run seen going on in this screen: the first read of a tab shows a run still going on, never
// one that had already ended (an old run of every Flow would light every tab). The shown tab never keeps a result — the
// Git panel's run box says it — so it goes the moment the run ends; another tab keeps it until it is activated or closed,
// and a read of the same run does not bring it back.
import { getCurrentInstance, reactive, readonly } from 'vue'
import { useProjectStore, type FlowGitExecution } from '@/features/project'
/** The Git steps a tab names (the Git part's execution steps; the Designer keeps its own copy of the names). */
export type GitTabStep = 'commit' | 'update' | 'merge' | 'push'

export type GitTabActivityState = 'running' | 'done' | 'skipped' | 'remaining' | 'failed'

export interface GitTabActivity {
  readonly state: GitTabActivityState
  /** The step running or stopped at (null = the whole run / unknown). */
  readonly step: GitTabStep | null
  /** The server's code behind a stop or a passed-over 받아오기. */
  readonly code: string | null
}

/** Slower than the panel's own poll (2.5 s): only runs no panel shows are read here. */
export const GIT_TAB_POLL_MS = 3000

const store = reactive<Record<string, GitTabActivity>>({})
/** Flows whose run the Git panel is reporting now (it reads that run itself). */
const panelRunning = new Set<string>()
/** Flows the panel let go of while their run went on: read on the next tick, the run may still go on in the server. */
const released = new Set<string>()
/** The Flow whose tab is shown (setActiveGitTab): it keeps a running mark, never a result. */
let activeFlow: string | null = null

function put(flowId: string, activity: GitTabActivity): void {
  if (activity.state !== 'running' && flowId === activeFlow) delete store[flowId]
  else store[flowId] = activity
}

/** Each Flow's (group's) Git activity by flow id. */
export const gitTabActivity: Readonly<Record<string, GitTabActivity>> = readonly(store) as Readonly<Record<string, GitTabActivity>>

/** The Git panel's report for a Flow (`git-activity`); null = it no longer reports one. */
export function setGitTabActivity(flowId: string, activity: GitTabActivity | null): void {
  if (activity?.state === 'running') panelRunning.add(flowId)
  else if (panelRunning.delete(flowId) && !activity) released.add(flowId)
  if (activity) put(flowId, activity)
  else delete store[flowId]
}

/** The shown tab (null = none): its result goes now, and a result that ends there later is not kept. */
export function setActiveGitTab(flowId: string | null): void {
  activeFlow = flowId
  if (flowId) clearGitTabResult(flowId)
}

/** Drops a finished result (a running mark stays: the run is still going). */
export function clearGitTabResult(flowId: string): void {
  const now = store[flowId]
  if (now && now.state !== 'running') delete store[flowId]
}

/** Drops every Flow not in `open` (closed tabs). */
export function pruneGitTabActivity(open: ReadonlySet<string>): void {
  for (const flowId of Object.keys(store)) if (!open.has(flowId)) delete store[flowId]
  for (const flowId of [...panelRunning]) if (!open.has(flowId)) panelRunning.delete(flowId)
  for (const flowId of [...released]) if (!open.has(flowId)) released.delete(flowId)
  if (activeFlow && !open.has(activeFlow)) activeFlow = null
}

/** A server run as its tab shows it — the same reading as the Git panel's own result of a run. */
export function activityOfRun(run: FlowGitExecution): GitTabActivity {
  const step = run.steps[run.cursor] ?? null
  if (run.status === 'running') return { state: 'running', step, code: null }
  if (run.status === 'stopped') return { state: 'failed', step, code: run.error }
  const skipped = run.skipped.update ?? null
  if (run.status === 'remaining') return { state: 'remaining', step: null, code: skipped }
  return { state: skipped ? 'skipped' : 'done', step: skipped ? 'update' : null, code: skipped }
}

export interface GitTabRunSource {
  /** The Flow's latest durable Git run; null = none yet. */
  loadRun(groupId: string): Promise<FlowGitExecution | null>
}

function storeSource(): GitTabRunSource | null {
  const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
  if (!pinia) return null
  const projects = useProjectStore(pinia)
  return { loadRun: (groupId) => projects.loadFlowGitExecution(groupId) }
}

/** Reads the open tabs' server runs (see the top of this file) until the returned stop is called. Tests pass a source;
 *  otherwise the Project store of the running app (none without Pinia: only the panel reports). */
export function observeGitTabActivity(
  openFlows: () => readonly string[],
  source: GitTabRunSource | null = storeSource(),
  pollMs: number = GIT_TAB_POLL_MS,
): () => void {
  if (!source) return () => {}
  /** The run each open tab last showed (job · status · step), so the same answer is not shown twice. */
  const seen = new Map<string, string>()
  let disposed = false
  let timer: ReturnType<typeof setTimeout> | null = null

  async function read(flowId: string, force: boolean): Promise<void> {
    try {
      const run = await source!.loadRun(flowId)
      if (disposed || !openFlows().includes(flowId)) return
      const key = run ? `${run.jobId}:${run.status}:${run.cursor}` : ''
      if (!force && seen.get(flowId) === key) return
      const first = !seen.has(flowId)
      seen.set(flowId, key)
      if (!run || panelRunning.has(flowId)) return
      // A run that had ended before the tab's first read is an old result, not news of this screen: no mark.
      if (first && !force && run.status !== 'running') return
      put(flowId, activityOfRun(run))
    } catch {
      // A read failure is not evidence that the server run stopped: keep the mark, the next tick asks again.
    }
  }

  async function tick(): Promise<void> {
    const open = openFlows()
    for (const flowId of [...seen.keys()]) if (!open.includes(flowId)) seen.delete(flowId)
    const reads = open.flatMap((flowId) => {
      if (released.delete(flowId)) return [read(flowId, true)]
      const due = !seen.has(flowId) || (store[flowId]?.state === 'running' && !panelRunning.has(flowId))
      return due ? [read(flowId, false)] : []
    })
    await Promise.all(reads)
    if (!disposed) timer = setTimeout(() => { void tick() }, pollMs)
  }

  void tick()
  return () => {
    disposed = true
    if (timer !== null) clearTimeout(timer)
  }
}

type Lang = 'ko' | 'en' | 'ja'
const WORDS: Record<Lang, Record<string, string>> = {
  ko: { running: 'Git 실행 중', done: 'Git 완료', skipped: 'Git 완료 · 받아오기 건너뜀', remaining: 'Git 완료 · 남은 작업 있음', failed: 'Git 멈춤', commit: '커밋', update: '받아오기', merge: '병합', push: '푸시', code: '코드' },
  en: { running: 'Git running', done: 'Git done', skipped: 'Git done · update skipped', remaining: 'Git done · work left', failed: 'Git stopped', commit: 'Commit', update: 'Update', merge: 'Merge', push: 'Push', code: 'code' },
  ja: { running: 'Git 実行中', done: 'Git 完了', skipped: 'Git 完了 · 取り込みスキップ', remaining: 'Git 完了 · 残りあり', failed: 'Git 停止', commit: 'コミット', update: '取り込み', merge: 'マージ', push: 'プッシュ', code: 'コード' },
}

/** The tab mark's hover text: state · step · code (e.g. 「Git 멈춤 · 병합 · 코드: merge_conflict」). */
export function gitTabActivityLabel(activity: GitTabActivity, locale: string): string {
  const w = WORDS[locale.startsWith('ko') ? 'ko' : locale.startsWith('ja') ? 'ja' : 'en']
  const parts = [w[activity.state]]
  if (activity.step) parts.push(w[activity.step])
  if (activity.code) parts.push(`${w.code}: ${activity.code}`)
  return parts.join(' · ')
}
