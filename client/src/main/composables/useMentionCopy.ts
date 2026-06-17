import { postRequest } from '@shared/api'

// R0001 group 0015 / NR0003 rev4 — persistent "mention copied" header badge (option B).
//
// A "mention" is the work-instruction block a person copies and pastes to an AI worker to
// advance a document (edit / review / next-step / ...). NR0005 found 9 distinct copy sites; each
// maps to one stable `MentionKind` code. The badge label is rendered from the code (DocHeader),
// so it stays locale-correct. The server stores only the code + timestamp.
export type MentionKind =
  | 'edit'              // edit ▾ → Copy mention (scope=edit)
  | 'rework'            // action-bar rework mention-copy
  | 'review'            // review request (review mention)
  | 'vr_correction'     // review request → VR correction mention
  | 'workflow_decision' // workflow decision mention
  | 'next_step'         // next-step mention (NextActionModal / create-next after approval)
  | 'next_step_message' // next-step mention (add message)
  | 'design_handoff'    // design handoff mention
  | 'reject'            // reject mention
  | 'qa_answer'         // Q&A answer mention
  | 'continuous'        // continuous (unmanned) work start mention (R0001 group 0086)

// Window event the DocHeader badge listens for. Mirrors the existing fg:* bridges
// (fg:doc_review_status_changed, fg:q_status_changed) the header already subscribes to.
export const MENTION_COPIED_EVENT = 'fg:mention_copied'

export interface MentionCopiedDetail {
  docId: string
  kind: MentionKind
  copiedAt: string
}

export function useMentionCopy() {
  // Persist a successful mention copy as server user-state and notify the open header.
  //
  // Best-effort by design: the clipboard write has ALREADY succeeded by the time this is called,
  // so a failed persist must never disrupt the user. On failure we still surface the badge this
  // session using a client timestamp; it just will not survive a reload (acceptable degradation).
  async function recordMentionCopy(docId: string | null | undefined, kind: MentionKind): Promise<void> {
    if (!docId) return
    let copiedAt = new Date().toISOString()
    try {
      const res = await postRequest<{ ok: boolean; mention_kind: MentionKind; copied_at: string }>(
        '/api/v1/documents/mention-copy',
        { doc_id: docId, mention_kind: kind },
      )
      const data = (res.data as any) ?? {}
      if (data.copied_at) copiedAt = data.copied_at
    } catch {
      /* best-effort — keep the client timestamp */
    }
    if (typeof window !== 'undefined') {
      const detail: MentionCopiedDetail = { docId, kind, copiedAt }
      window.dispatchEvent(new CustomEvent(MENTION_COPIED_EVENT, { detail }))
    }
  }

  return { recordMentionCopy }
}
