import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { postRequest } from '@shared/api'
import { useToast } from '../components/common/useToast'
import { prependMessagesSection } from '../utils/mentionMessages'
import { copyToClipboard } from '../utils/clipboard'

// Base URL used ONLY to build copy-paste mention text (buildConversationMention /
// buildMentText). Mentions are consumed by an AI worker on another machine, so the URL
// MUST be absolute (scheme+host) — a relative value has no host to resolve against.
// In production setup.ps1 writes VITE_API_BASE_URL=/flowgate (relative, correct for the
// SPA's same-origin axios calls) which left mentions host-less (group 0103 B0001: "the
// chat copy mention shows no host anywhere"). When the configured base is relative we
// absolutize it against window.location.origin — the browser copying the mention is on
// the same origin as the server the worker must reach, so origin is the right host.
function getFlowGateBaseUrl(): string {
  const raw =
    (import.meta.env.VITE_FLOWGATE_PUBLIC_URL as string | undefined) ??
    (import.meta.env.VITE_API_BASE_URL as string | undefined) ??
    'http://127.0.0.1:8088/flowgate'
  if (/^https?:\/\//i.test(raw)) return raw
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  return `${origin}${raw.startsWith('/') ? '' : '/'}${raw}`
}

export interface TokenIssueParams {
  project: string
  module?: string
  group: string
  action_scope?: 'new' | 'edit'
  doc_ref?: string | null
  selected_docs?: string[]
  // Continuous work (group 0086 R0001 / NR0003 B안): when continuationTargetSeq is set the
  // minted token starts an unmanned self-chaining run up to that sequence item_seq, and its
  // mention swaps the Q-guard for the delegation/unmanned block. continuationReviewMode pauses
  // the chain after the first step for human Q&A. Sent on /workflow/advance (primary) and
  // /token/issue (fallback).
  continuous?: boolean
  continuationTargetSeq?: number
  continuationReviewMode?: boolean
}

/**
 * Decomposes canonical group_id (project.module.code) into { module, groupCode }.
 * If not in 3-part format, returns groupCode as-is.
 */
export function splitGroupId(canonical: string | null | undefined): { module?: string; groupCode: string } | null {
  if (!canonical) return null
  const parts = canonical.split('.')
  if (parts.length === 3) {
    return { module: parts[1], groupCode: parts[2] }
  }
  return { groupCode: canonical }
}

// TR0044.0010 rev4: a CHAT-ONLY, compact mention for conversation (CH) documents.
// The reviewer asked that the conversation mention-copy produce a mention stripped of Q /
// clarification / reference / doc_type clutter — "strip out Q info and other useless info,
// keep it compact". The standard edit mention (mention_service.build_mention) carries all of
// that, so for CH we build our own minimal one: read the conversation, append one AI
// turn (§6 format), submit the full body via inbox edit. No Q-registration guide, no
// no-choices guard, no recent-docs/template/doc_type sections — just chat.
export interface ConversationMentionParams {
  rawToken: string
  docId: string
  project: string
  module?: string | null
  groupName: string
}

export function buildConversationMention(p: ConversationMentionParams): string {
  const apiBase = `${getFlowGateBaseUrl()}/api/v1`
  const body: Record<string, unknown> = {
    action: 'edit',
    project: p.project,
    ...(p.module ? { module: p.module } : {}),
    group_name: p.groupName,
    doc_id: p.docId,
    // inbox validates edit_reason against {rejected, qna_followup, user_comment,
    // worker_self}; an AI appending its own conversation turn is a self-initiated edit.
    edit_reason: 'worker_self',
    content: '<the full conversation body, with your new turn appended at the end>',
  }
  return [
    '## Conversation (대화)',
    '---',
    'You are a participant in an ongoing conversation. Read the latest messages and',
    'reply naturally and concisely. This is a chat — no document headers, no Q /',
    'clarification registration, no review. Just talk.',
    '',
    `Conversation document: ${p.docId}`,
    `Read the full conversation: GET ${apiBase}/document?doc_id=${encodeURIComponent(p.docId)}`,
    `Authorization: Bearer ${p.rawToken}`,
    '',
    'To reply, append ONE new turn to the END of the existing body in this exact',
    'format, then submit the COMPLETE body (every existing turn + your new one):',
    '',
    '## 🤖 AI · <ISO-8601 timestamp>',
    '<your reply>',
    '',
    `Submit: POST ${apiBase}/inbox`,
    `Authorization: Bearer ${p.rawToken}`,
    '',
    JSON.stringify(body, null, 2),
  ].join('\n')
}

export interface IssuedToken {
  raw_token: string
  token_id: string
  expires_at: string
  scratch_dir: string
  action_scope: string
  doc_ref: string | null
  mention?: string | null
  selected_docs?: string[]
}

export interface RejectionHistoryItem {
  /** P0005/T0006: stable, time-sortable id assigned by the server at reject time.
   *  Optional only for defensive reads of pre-backfill data. */
  rejection_id?: string
  reason: string
  rejected_at: string
  rejected_by: string | null
  // P0005/T0006: AI response recorded against this rejection (nullable until input).
  ai_response?: string | null
  responded_at?: string | null
  response_recorded_by?: string | null
  response_revision_no?: number | null
}

export interface RejectionContext {
  history: RejectionHistoryItem[]
  last: string | null
}

export function useFlowGateToken() {
  const { t } = useI18n()
  const { showToast } = useToast()
  const issuing = ref(false)

  async function issueToken(params: TokenIssueParams): Promise<IssuedToken | null> {
    issuing.value = true
    try {
      // action_scope='new' (or unspecified) + doc_ref → /advance (workflow next step).
      // action_scope='edit' → calls /token/issue directly (current document edit flow).
      const shouldAdvance =
        !!params.doc_ref &&
        (params.action_scope === 'new' || params.action_scope == null)
      if (shouldAdvance) {
        try {
          const advRes = await postRequest<any>(
            `/api/v1/workflow/advance`,
            {
              doc_id: params.doc_ref!,
              ...(params.selected_docs ? { ref_doc_ids: params.selected_docs } : {}),
              ...(params.continuous
                ? {
                    continuous: true,
                    continuation_target_seq: params.continuationTargetSeq,
                    continuation_review_mode: !!params.continuationReviewMode,
                  }
                : {}),
            },
          )
          const d = advRes.data as any
          return {
            raw_token: d.token,
            token_id: d.token_id,
            expires_at: d.expires_at,
            scratch_dir: d.scratch_dir,
            action_scope: d.action_scope ?? 'new',
            doc_ref: d.doc_ref ?? params.doc_ref!,
            mention: d.mention ?? null,
            selected_docs: params.selected_docs,
          }
        } catch (advErr: any) {
          const status = advErr?.response?.status
          // Auth errors should not fall through
          if (status === 401 || status === 403) throw advErr
          // Other advance errors fall back to /token/issue (legacy compat)
        }
      }
      const { continuous, continuationTargetSeq, continuationReviewMode, ...issueBody } = params
      const res = await postRequest<any>('/api/v1/token/issue', {
        ...issueBody,
        ...(continuous && continuationTargetSeq != null
          ? {
              continuation_target_seq: continuationTargetSeq,
              continuation_review_mode: !!continuationReviewMode,
            }
          : {}),
      })
      const d = res.data as any
      return {
        raw_token: d.raw_token,
        token_id: d.token_id,
        expires_at: d.expires_at,
        scratch_dir: d.scratch_dir,
        action_scope: d.action_scope ?? 'new',
        doc_ref: d.doc_ref ?? null,
        mention: d.mention ?? null,
        selected_docs: params.selected_docs,
      }
    } catch (e: any) {
      const status = e?.response?.status
      const msg = e?.response?.data?.detail ?? t('main.flow_gate_token.issue_failed')
      if (status === 401) showToast(t('main.flow_gate_token.login_required'), 'danger')
      else if (status === 403) showToast(t('main.flow_gate_token.permission_denied'), 'danger')
      else showToast(msg, 'danger')
      return null
    } finally {
      issuing.value = false
    }
  }

  // Review request: issue a token bound to doc_id and get a "please review this doc"
  // mention (read → evaluate → submit verdict via inbox action:review). Distinct from
  // issueToken/advance, which hands off CREATING the next document.
  async function requestReview(params: { doc_id: string; ref_doc_ids?: string[] }): Promise<IssuedToken | null> {
    issuing.value = true
    try {
      const res = await postRequest<any>(`/api/v1/documents/review-request`, {
        doc_id: params.doc_id,
        ...(params.ref_doc_ids ? { ref_doc_ids: params.ref_doc_ids } : {}),
      })
      const d = res.data as any
      return {
        raw_token: d.token,
        token_id: d.token_id,
        expires_at: d.expires_at,
        scratch_dir: d.scratch_dir,
        action_scope: d.action_scope ?? 'review',
        doc_ref: d.doc_ref ?? params.doc_id,
        mention: d.mention ?? null,
      }
    } catch (e: any) {
      const status = e?.response?.status
      const msg = e?.response?.data?.detail ?? t('main.flow_gate_token.issue_failed')
      if (status === 401) showToast(t('main.flow_gate_token.login_required'), 'danger')
      else if (status === 403) showToast(t('main.flow_gate_token.permission_denied'), 'danger')
      else showToast(msg, 'danger')
      return null
    } finally {
      issuing.value = false
    }
  }

  // Continuous work (group 0086 R0001 "워크플로 결정부터"): when opts.continuous is set, the
  // run is started before the workflow is decided. The minted workflow_decide token carries
  // the run-to-end sentinel + review-mode flag, and the server self-chains the rest of the
  // run once the decision is saved. continuationReviewMode pauses after the first produced step.
  async function requestWorkflowDecision(
    docId: string,
    opts?: { continuous?: boolean; continuationReviewMode?: boolean },
  ): Promise<IssuedToken | null> {
    issuing.value = true
    try {
      const res = await postRequest<any>('/api/v1/workflow/decision-request', {
        doc_id: docId,
        ...(opts?.continuous
          ? {
              continuous: true,
              continuation_review_mode: !!opts.continuationReviewMode,
            }
          : {}),
      })
      const d = res.data as any
      return {
        raw_token: d.raw_token,
        token_id: d.token_id,
        expires_at: d.expires_at,
        scratch_dir: d.scratch_dir,
        action_scope: d.action_scope ?? 'workflow_decide',
        doc_ref: d.doc_ref ?? docId,
        mention: d.mention ?? null,
      }
    } catch (e: any) {
      const status = e?.response?.status
      const msg = e?.response?.data?.detail
        ?? e?.response?.data?.error
        ?? t('main.flow_gate_token.issue_failed')
      if (status === 401) showToast(t('main.flow_gate_token.login_required'), 'danger')
      else if (status === 403) showToast(t('main.flow_gate_token.permission_denied'), 'danger')
      else showToast(msg, 'danger')
      return null
    } finally {
      issuing.value = false
    }
  }

  // Sequence edit request (R0001 group 0208): issue a token bound to a DECIDED workflow-root
  // doc + a "please edit the pending sequence" mention, and hand it to an AI worker. The worker
  // applies the change autonomously via PATCH /workflow/sequence (locked/completed steps stay
  // immutable). Parallel of requestWorkflowDecision, for the post-decision 시퀀스 수정 path.
  async function requestSequenceEdit(docId: string): Promise<IssuedToken | null> {
    issuing.value = true
    try {
      const res = await postRequest<any>('/api/v1/workflow/sequence-edit-request', {
        doc_id: docId,
      })
      const d = res.data as any
      return {
        raw_token: d.raw_token,
        token_id: d.token_id,
        expires_at: d.expires_at,
        scratch_dir: d.scratch_dir,
        action_scope: d.action_scope ?? 'workflow_sequence_edit',
        doc_ref: d.doc_ref ?? docId,
        mention: d.mention ?? null,
      }
    } catch (e: any) {
      const status = e?.response?.status
      const msg = e?.response?.data?.detail
        ?? e?.response?.data?.error
        ?? t('main.flow_gate_token.issue_failed')
      if (status === 401) showToast(t('main.flow_gate_token.login_required'), 'danger')
      else if (status === 403) showToast(t('main.flow_gate_token.permission_denied'), 'danger')
      else showToast(msg, 'danger')
      return null
    } finally {
      issuing.value = false
    }
  }

  // Builds the rejection section independently — prepend to the server mention or reuse inside the fallback buildMentText
  function buildRejectionSection(ctx: RejectionContext): string {
    if (!ctx.last && ctx.history.length === 0) return ''
    const parts: string[] = []
    parts.push('## Revision Request')
    parts.push('---')
    parts.push('Requesting document revisions for the reason(s) below. Apply the latest rejection first; prior history (if any) is listed for context.')
    parts.push('')
    parts.push('### Last rejection reason (apply first on rework)')
    parts.push(ctx.last ?? '')
    // R0001 #2 / T0004: the final history entry is always identical to `last` (it IS the
    // most recent rejection), so listing the whole history here printed the latest reason
    // twice. Show only the PRIOR entries (history minus its last item) as context. With a
    // single rejection there are no prior entries, so the history block is omitted.
    const prior = ctx.history.slice(0, -1)
    if (prior.length > 0) {
      parts.push('')
      parts.push(`### Prior rejection history (${prior.length} ${prior.length === 1 ? 'item' : 'items'}, chronological)`)
      prior.forEach((item, i) => {
        parts.push(`${i + 1}. [${item.rejected_at}] ${item.reason}`)
      })
    }
    parts.push('')
    return parts.join('\n')
  }

  function buildMentText(token: IssuedToken, selectedDocs?: string[], rejectionContext?: RejectionContext): string {
    const apiBase = `${getFlowGateBaseUrl()}/api/v1`

    // Section 3: reference documents — {doc_ref}: GET {url} format (head first, then selected docs in order)
    const s3Lines: string[] = []
    if (token.doc_ref) {
      s3Lines.push(`${token.doc_ref}: GET ${apiBase}/document?doc_id=${encodeURIComponent(token.doc_ref)}`)
    }
    const docsToShow = selectedDocs ?? token.selected_docs ?? []
    for (const doc of docsToShow) {
      if (doc === token.doc_ref) continue  // remove duplicates
      s3Lines.push(`${doc}: GET ${apiBase}/document?doc_id=${encodeURIComponent(doc)}`)
    }

    const parts: string[] = []

    // Revision request section — insert before mention only when rejection history exists (used in token.mention null fallback path)
    if (rejectionContext) {
      const rejSection = buildRejectionSection(rejectionContext)
      if (rejSection) parts.push(rejSection)
    }

    parts.push('## Reference Documents')
    parts.push('---')
    if (s3Lines.length > 0) parts.push(s3Lines.join('\n'))

    parts.push('')
    parts.push('## Scratch Directory')
    parts.push('---')
    parts.push(token.scratch_dir)

    parts.push('')
    parts.push('## doc_type info')
    parts.push('---')
    parts.push(`GET ${apiBase}/help/doc_type`)

    return parts.join('\n')
  }

  // Build the final mention string from a token (pure — no clipboard/network side effects).
  // Exposed so callers can compute the text INSIDE a deferred-copy producer, keeping the
  // clipboard write inside the click's user activation (B0001 / group 0133 — see
  // utils/clipboard.ts). Mirrors the prior in-line logic of copyMentToClipboard exactly.
  function composeMention(token: IssuedToken, selectedDocs?: string[], rejectionContext?: RejectionContext, appendMessages?: string[]): string {
    // Edit case: prepend only the rejection section to token.mention (server 8-section).
    // If token.mention is null, fall back to client-side buildMentText() (includes rejection section).
    let text: string
    if (rejectionContext && token.mention) {
      const rejSection = buildRejectionSection(rejectionContext)
      text = rejSection ? rejSection + '\n' + token.mention : token.mention
    } else {
      text = token.mention ?? buildMentText(token, selectedDocs ?? token.selected_docs, rejectionContext)
    }
    // Mention-add (R0001 group 0081): prepend the chosen project message(s) as one labeled
    // section at the top so the AI sees its macros first, not buried below the Reminder.
    if (appendMessages && appendMessages.length > 0) text = prependMessagesSection(text, appendMessages, t('main.next_action_modal.mm_section_header'))
    return text
  }

  async function copyMentToClipboard(token: IssuedToken, selectedDocs?: string[], rejectionContext?: RejectionContext, appendMessages?: string[]): Promise<boolean> {
    const text = composeMention(token, selectedDocs, rejectionContext, appendMessages)
    // Honest write (B0001): returns false if the clipboard was not actually set, so callers
    // warn instead of falsely toasting success. Note this path still awaits the token before
    // writing; activation-sensitive call sites should use copyToClipboardDeferred(composeMention).
    return copyToClipboard(text)
  }

  return {
    issuing,
    issueToken,
    requestReview,
    requestWorkflowDecision,
    requestSequenceEdit,
    composeMention,
    copyMentToClipboard,
  }
}
