// Graph document decisions: preserve the server context verbatim for card-wide CAS.
import { getRequest, postRequest } from '@shared/api'

export type CardDocumentAction = 'approve' | 'reject'
export interface CardDecisionDocument {
  readonly doc_id: string
  readonly revision_no: number
  readonly doc_review_status: string | null
  readonly effective_state?: string | null
}
export interface CardDocumentDecisions {
  readonly flow_id: string
  readonly node_key: string
  readonly context: Readonly<Record<string, unknown>>
  readonly decision_version: number
  readonly documents: Readonly<Record<string, CardDecisionDocument | null>>
  readonly capabilities: Readonly<Record<string, Partial<Record<CardDocumentAction, {
    readonly allowed: boolean
    readonly reason: { readonly code: string } | null
  }>>>>
  readonly current?: CardDocumentDecisions
}
export interface CardDocumentDecisionRequest {
  readonly action: CardDocumentAction
  readonly doc_id: string
  readonly expected_revision: number
  readonly expected_decision_version: number
  readonly expected_context: Readonly<Record<string, unknown>>
  readonly request_key: string
}
const path = (flowId: string, nodeKey: string) =>
  `/api/v1/flows/${encodeURIComponent(flowId)}/nodes/${encodeURIComponent(nodeKey)}/documents`

export async function cardDocumentDecisionsRead(flowId: string, nodeKey: string): Promise<CardDocumentDecisions> {
  const response = await getRequest(`${path(flowId, nodeKey)}/decisions`)
  return response.data
}
export async function cardDocumentDecisionWrite(flowId: string, nodeKey: string, slot: string,
  body: CardDocumentDecisionRequest): Promise<CardDocumentDecisions> {
  const response = await postRequest(`${path(flowId, nodeKey)}/${encodeURIComponent(slot)}/decision`, body)
  return response.data
}
