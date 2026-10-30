import { getRequest } from '@shared/api'
import i18n from '@shared/i18n'
import { toGraphError } from './flowGraphApi'
import { num, str, type Raw } from './flowCanvasApi'
export interface AiInvocationCursor { beforeSeq: number; beforeInvId: string }
export interface AiInvocation {
  invId: string; createSeq: number; role: string | null; purpose: string | null; roleSource: string | null
  requestedAiId: string | null; actualAi: { id: string; name: string | null } | null
  providerMatch: string | null; configState: string | null
  requestedModel: { state: string | null; value: string | null; reason: string | null }
  confirmedModel: { state: string | null; values: string[]; coverage: string | null; truncated: boolean; reason: string | null; calls: Record<string, number> | null; badLines: number | null }
  compare: { status: string; mismatchFound: boolean; reconciled: boolean }
  result: string; resultDetail: string | null
  startedAt: string | null; endedAt: string | null; createdAt: string | null
  roundNo: number | null; launchNo: number | null; hopNo: number | null; aiRunId: string | null
  link: { kind: string | null; prevInvId: string | null }
  qa: { itemId: string | null; questionId: string | null; askerAiRunId: string | null; askerInvId: string | null; cardClass: string | null } | null
  target: { flowId: string | null; nodeKey: string | null; graphRunId: string | null; attemptId: string | null; stage: string | null; seq: number | null; docId: string | null; docRevisionNo: number | null } | null
}
export interface AiInvocationsPage { available: boolean; invocations: AiInvocation[]; nextCursor: AiInvocationCursor | null }
export function toAiInvocationsPage(raw: Raw): AiInvocationsPage {
  return {
    available: raw.available === true,
    invocations: (Array.isArray(raw.invocations) ? raw.invocations : []).map((item: Raw) => ({
      invId: String(item.inv_id), createSeq: num(item.create_seq) ?? 0, role: str(item.role), purpose: str(item.purpose), roleSource: str(item.role_source),
      requestedAiId: str(item.requested_ai_id), actualAi: item.actual_ai ? { id: String(item.actual_ai.id), name: str(item.actual_ai.name) } : null,
      providerMatch: str(item.provider_match), configState: str(item.provider_config?.config_state),
      requestedModel: { state: str(item.requested_model?.state), value: str(item.requested_model?.value), reason: str(item.requested_model?.reason) },
      confirmedModel: { state: str(item.confirmed_model?.state), values: Array.isArray(item.confirmed_model?.values) ? item.confirmed_model.values.filter((value: unknown) => typeof value === 'string') : [],
        coverage: str(item.confirmed_model?.coverage), truncated: item.confirmed_model?.truncated === true, reason: str(item.confirmed_model?.reason),
        calls: item.confirmed_model?.calls ? Object.fromEntries(['total','reported','absent','unreadable','failed'].map(key => [key, num(item.confirmed_model.calls[key]) ?? 0])) : null, badLines: num(item.confirmed_model?.bad_lines) },
      compare: { status: String(item.compare?.status ?? ''), mismatchFound: item.compare?.mismatch_found === true, reconciled: item.compare?.reconciled === true },
      result: String(item.result ?? ''), resultDetail: str(item.result_detail), startedAt: str(item.started_at), endedAt: str(item.ended_at), createdAt: str(item.created_at),
      roundNo: num(item.round_no), launchNo: num(item.launch_no), hopNo: num(item.hop_no), aiRunId: str(item.ai_run_id),
      link: { kind: str(item.link?.kind), prevInvId: str(item.link?.prev_inv_id) },
      qa: item.qa ? { itemId: str(item.qa.item_id), questionId: str(item.qa.question_id), askerAiRunId: str(item.qa.asker_ai_run_id), askerInvId: str(item.qa.asker_inv_id), cardClass: str(item.qa.card_class) } : null,
      target: item.target ? { flowId: str(item.target.flow_id), nodeKey: str(item.target.node_key), graphRunId: str(item.target.graph_run_id), attemptId: str(item.target.attempt_id), stage: str(item.target.stage), seq: num(item.target.seq), docId: str(item.target.doc_id), docRevisionNo: num(item.target.doc_revision_no) } : null,
    })),
    nextCursor: raw.next_cursor ? { beforeSeq: raw.next_cursor.before_seq, beforeInvId: raw.next_cursor.before_inv_id } : null,
  }
}
export async function getAiInvocations(flowId: string, nodeKey: string, cursor: AiInvocationCursor | null = null): Promise<AiInvocationsPage> {
  try {
    const response = await getRequest<Raw>(`/api/v1/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/ai-invocations`, {
      limit: 50, ...(cursor ? { before_seq: cursor.beforeSeq, before_inv_id: cursor.beforeInvId } : {}),
    })
    if (response.data.ok === false) throw { response: { data: response.data } }
    return toAiInvocationsPage(response.data)
  } catch (error) { throw toGraphError(error, String(i18n.global.t('gui.inspector.settings.card_error.other'))) }
}
