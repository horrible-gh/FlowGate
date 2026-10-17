// flowgate.v02.0045 S3-5 + S4 — the draft actions of an open Flow (D0004 §2.3 center/edit): validate the
// saved draft, and confirm it as the next revision. One action of a Flow runs at a time; a refusal shows
// its issues (or its code) on the Flow's edit state, and a refusal that means the shown Graph is outdated
// reads the Flow again. flowgate.v02.0059 T#2: moved here from draftController.ts as it was (that file
// keeps its size bound); the controller hands over its edit state, editors and stop.
import type { FlowDraftError } from '../graph/flowGraphSource'
import type { DraftAction, DraftEditState, DraftHost } from './draftController'
import type { DraftEditor } from './draftEditor'

const REREAD_CODES = new Set([
  'flow_owned_by_v01', 'flow_ownership_conflict', 'revision_conflict', 'draft_not_found', 'draft_version_conflict',
])

const codeOf = (error: unknown) => {
  const code = (error as FlowDraftError | null)?.code
  return typeof code === 'string' && code ? code : 'request_failed'
}

export interface DraftActionContext {
  readonly host: DraftHost
  stateOf(flowId: string): DraftEditState
  /** The Flow's draft editor, if its shown Graph is an editable draft. */
  editor(flowId: string): DraftEditor | undefined
  /** Stops editing the Flow (after a confirmation). */
  stop(flowId: string): void
}

export function createDraftActions({ host, stateOf, editor: editorOf, stop }: DraftActionContext) {
  async function act(flowId: string, action: DraftAction, body: (pid: string) => Promise<void>) {
    const pid = host.projectId()
    const state = stateOf(flowId)
    if (pid === null || state.action) return
    state.action = action
    state.notice = null
    try {
      await body(pid)
    } catch (error) {
      const code = codeOf(error)
      const issues = (error as FlowDraftError | null)?.issues
      if (issues?.length) state.check = { confirmable: false, issues: [...issues] }
      else state.notice = { kind: 'error', code }
      if (REREAD_CODES.has(code)) host.reread(flowId)
    } finally {
      state.action = null
    }
  }

  async function savedVersion(flowId: string): Promise<number> {
    const editor = editorOf(flowId)
    if (!editor) throw Object.assign(new Error('not editable'), { code: 'draft_not_editable' })
    if (!(await editor.settled())) throw Object.assign(new Error('not saved'), { code: editor.errorCode() ?? 'save_failed' })
    return editor.version()
  }

  const validate = (flowId: string) => act(flowId, 'validate', async (pid) => {
    const version = await savedVersion(flowId)
    if (version === 0 || !host.source.validateDraft) throw Object.assign(new Error('no draft'), { code: 'draft_not_found' })
    stateOf(flowId).check = await host.source.validateDraft(pid, flowId, version)
  })

  const confirm = (flowId: string) => act(flowId, 'confirm', async (pid) => {
    const version = await savedVersion(flowId)
    if (version === 0 || !host.source.confirmDraft) throw Object.assign(new Error('no draft'), { code: 'draft_not_found' })
    const base = host.graph(flowId)?.draft?.baseRevisionNo ?? null
    const revisionNo = await host.source.confirmDraft(pid, flowId, version, base)
    const state = stateOf(flowId)
    state.check = null
    state.notice = { kind: 'confirmed', revisionNo }
    stop(flowId)
    host.setView(flowId, null)
    host.reread(flowId)
  })

  return { validate, confirm }
}
