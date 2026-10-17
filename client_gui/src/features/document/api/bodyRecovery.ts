// flowgate.v02.0064 T-B1d — document body diagnosis and recovery (P0006 §2.6~§2.8, §6.2 / §6.3).
//
//   diagnose   GET  /api/v1/documents/body-diagnosis?group_id=… | ?project_id=…   (perm_document_read)
//   recover    POST /api/v1/documents/{doc_id}/regenerate  { expected_method }      (perm_document_update)
//   bulk       POST /api/v1/documents/body-recovery  { selections: [{ doc_id, expected_method }] } (1~50)
//
// The diagnosis writes nothing. A recovery sends the method the diagnosis showed; the server diagnoses
// again inside the Flow guard and refuses (`diagnosis_changed`) when it changed. A refused or failed
// document is never counted as recovered. Only codes are read from errors, never a server sentence.
import { getRequest, postRequest } from '@shared/api'
import { normalizeApiError, normalizeApiInput } from '@shared/apiErrors'

const API = '/api/v1'

/** One request's selection limit (server BULK_RECOVERY_MAX). */
export const bodyRecoveryBulkMax = 50

export type BodyRecoveryMethod =
  | 'fix_storage_root'
  | 'relink_opposite'
  | 'relink_standard'
  | 'restore_revision'
  | 'regenerate_card_draft'
  | 'regenerate_stub'
  | 'read_error_manual'

export type BodyLoss = 'none' | 'possible' | 'lost'

export interface BodyDiagnosisItem {
  readonly docId: string
  readonly code: string
  readonly title: string
  readonly type: string
  readonly status: string | null
  readonly filePath: string
  readonly reason: string
  readonly method: BodyRecoveryMethod
  readonly recoverable: boolean
  readonly bodyLoss: BodyLoss | null
  readonly card: boolean
  readonly evidenceRoot: string | null
}

export interface BodyDiagnosis {
  readonly scope: { readonly kind: 'group' | 'project'; readonly id: string; readonly projectId: string | null }
  readonly storageRoot: {
    readonly effective: string
    readonly source: string
    readonly configured: Readonly<Record<string, string | null>>
  }
  readonly summary: {
    readonly checked: number
    readonly unreadable: number
    readonly recoverable: number
    readonly storageRootSuspect: number
    readonly suspectRoots: readonly string[]
  }
  readonly items: readonly BodyDiagnosisItem[]
  readonly fingerprint: string
}

export type BodyRecoveryRefusalCode =
  | 'not_found'
  | 'file_exists'
  | 'read_error'
  | 'diagnosis_changed'
  | 'storage_root_mismatch'
  | 'group_disposed'
  | 'group_ai_running'
  | 'target_in_use'
  | 'tr2_not_supported'
  | 'internal_error'

export interface BodyRecoveryResult {
  readonly docId: string
  readonly status: 'recovered' | 'refused' | 'failed'
  readonly method: BodyRecoveryMethod | null
  readonly bodyLoss: BodyLoss | null
  /** The refusal code of a refused / failed document. */
  readonly refusal: string | null
}

export interface BodyRecoveryBulk {
  readonly recovered: number
  readonly refused: number
  readonly failed: number
  readonly results: readonly BodyRecoveryResult[]
}

type Raw = Record<string, unknown>

const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const text = (value: unknown): string => (typeof value === 'string' ? value : '')
const textOrNull = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const count = (value: unknown): number => (typeof value === 'number' && Number.isFinite(value) ? value : 0)
const loss = (value: unknown): BodyLoss | null => (value === 'none' || value === 'possible' || value === 'lost' ? value : null)

/** The Flow of a document id (`{flow_id}.{code}`). */
export function bodyRecoveryFlowOf(docId: string): string | null {
  const at = docId.lastIndexOf('.')
  return at > 0 ? docId.slice(0, at) : null
}

export function toBodyRecoveryDiagnosis(raw: unknown): BodyDiagnosis {
  const data = record(raw)
  const scope = record(data.scope)
  const root = record(data.storage_root)
  const summary = record(data.summary)
  const configured = record(root.configured)
  return {
    scope: {
      kind: scope.kind === 'project' ? 'project' : 'group',
      id: text(scope.id),
      projectId: textOrNull(scope.project_id),
    },
    storageRoot: {
      effective: text(root.effective),
      source: text(root.source),
      configured: Object.fromEntries(Object.entries(configured).map(([key, value]) => [key, textOrNull(value)])),
    },
    summary: {
      checked: count(summary.checked),
      unreadable: count(summary.unreadable),
      recoverable: count(summary.recoverable),
      storageRootSuspect: count(summary.storage_root_suspect),
      suspectRoots: Array.isArray(summary.suspect_roots) ? summary.suspect_roots.filter((r): r is string => typeof r === 'string') : [],
    },
    items: (Array.isArray(data.items) ? data.items : []).map((value) => {
      const item = record(value)
      return {
        docId: text(item.doc_id),
        code: text(item.code),
        title: text(item.title),
        type: text(item.type),
        status: textOrNull(item.status),
        filePath: text(item.file_path),
        reason: text(item.reason),
        method: text(item.method) as BodyRecoveryMethod,
        recoverable: item.recoverable === true,
        bodyLoss: loss(item.body_loss),
        card: record(item.origin).card === true,
        evidenceRoot: textOrNull(record(item.evidence).root),
      }
    }),
    fingerprint: text(data.fingerprint),
  }
}

/** Diagnose a Flow or a whole project (read only). */
export async function bodyRecoveryDiagnose(scope: { groupId: string } | { projectId: string }): Promise<BodyDiagnosis> {
  const params = 'groupId' in scope ? { group_id: scope.groupId } : { project_id: scope.projectId }
  const response = await getRequest<unknown>(`${API}/documents/body-diagnosis`, params)
  return toBodyRecoveryDiagnosis(response.data)
}

/** What a refused single recovery answered (P0006 §2.7 table), or null when it is not a refusal. */
export function bodyRecoveryRefusalOf(error: unknown): BodyRecoveryRefusalCode | null {
  const { status, code } = normalizeApiError(error)
  const data = normalizeApiInput(error).data
  if (status === 404) return 'not_found'
  if (status === 423 || code === 'GROUP_AI_RUN_LOCKED') return 'group_ai_running'
  if (status !== 409) return null
  if (typeof data.error === 'string' && data.error) return data.error as BodyRecoveryRefusalCode
  // The two 409s that keep their old body without `error`: TR2 and the disposed Flow.
  return /^TR2 /.test(text(data.detail)) ? 'tr2_not_supported' : 'group_disposed'
}

/** Recover one document by the method its diagnosis showed. Refusals are thrown (see bodyRecoveryRefusalOf). */
export async function bodyRecoveryRecover(docId: string, expectedMethod: BodyRecoveryMethod): Promise<BodyRecoveryResult> {
  const response = await postRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}/regenerate`, {
    expected_method: expectedMethod,
  })
  const data = record(response.data)
  return { docId, status: 'recovered', method: text(data.method) as BodyRecoveryMethod, bodyLoss: loss(data.body_loss), refusal: null }
}

export function toBodyRecoveryBulk(raw: unknown): BodyRecoveryBulk {
  const data = record(raw)
  return {
    recovered: count(data.recovered),
    refused: count(data.refused),
    failed: count(data.failed),
    results: (Array.isArray(data.results) ? data.results : []).map((value) => {
      const row = record(value)
      const status = row.status === 'recovered' || row.status === 'refused' ? row.status : 'failed'
      return {
        docId: text(row.doc_id),
        status,
        method: textOrNull(row.method) as BodyRecoveryMethod | null,
        bodyLoss: loss(row.body_loss),
        refusal: status === 'recovered' ? null : textOrNull(record(row.refusal).error) ?? 'internal_error',
      }
    }),
  }
}

/** Selections in request-sized chunks (at most bodyRecoveryBulkMax each), in order. */
export function bodyRecoveryChunks<T>(selections: readonly T[], size = bodyRecoveryBulkMax): T[][] {
  const chunks: T[][] = []
  for (let at = 0; at < selections.length; at += size) chunks.push(selections.slice(at, at + size))
  return chunks
}

/** Recover the selected documents, sending more than 50 in several requests; results keep the order. */
export async function bodyRecoveryBulk(
  selections: readonly { docId: string; expectedMethod: BodyRecoveryMethod }[],
): Promise<BodyRecoveryBulk> {
  const results: BodyRecoveryResult[] = []
  for (const chunk of bodyRecoveryChunks(selections)) {
    const response = await postRequest<unknown>(`${API}/documents/body-recovery`, {
      selections: chunk.map((s) => ({ doc_id: s.docId, expected_method: s.expectedMethod })),
    })
    results.push(...toBodyRecoveryBulk(response.data).results)
  }
  return {
    recovered: results.filter((r) => r.status === 'recovered').length,
    refused: results.filter((r) => r.status === 'refused').length,
    failed: results.filter((r) => r.status === 'failed').length,
    results,
  }
}
