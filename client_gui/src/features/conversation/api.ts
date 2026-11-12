// flowgate.v02.0031 — Conversation API adapter. The conversation calls are the contracts the
// v0.1 ConversationView / MainPanel already use (R0001 §9: no Designer-only conversation API);
// flowgate.v02.0032 adds the Conversation production contract (D0006 §5.1): standalone chat
// creation, command / run change activity and command decisions. This file only adds the
// request shapes and the snake_case-to-frontend-model boundary.
//
//   chat list      GET   /api/v1/documents?project_id&group_id&type_code=CH
//   rename         PATCH /api/v1/documents/{doc_id}                 { title }
//   new chat       POST  /api/v1/conversations                      { project_id, group_id, title }
//   AI title       POST  /api/v1/conversations/{doc_id}/auto-title  { provider_id }
//   activity       GET   /api/v1/chat-activity/{doc_id}             commands + run changes, anchored
//   run file diff  GET   /api/v1/chat-activity/{doc_id}/runs/{run_id}/diff?path=
//   decision       POST  /api/v1/chat-commands/{request_id}/decision  { decision }
//   turns          GET   /api/v1/documents/{doc_id}/conversation/turns
//   send           POST  /api/v1/documents/{doc_id}/conversation/turn
//   read position  POST  /api/v1/documents/{doc_id}/conversation/read
//   providers      GET   /api/v1/ai-invoke/providers?project=
//   AI call        POST  /api/v1/ai-invoke/start (action_scope chat) · GET /ai-invoke/{run}
//                  POST  /api/v1/ai-invoke/{run}/cancel · GET /ai-invoke/active?group_id=
//   chat settings  GET/PATCH /api/v1/me/chat-settings
//   per-chat       GET/PATCH /api/v1/documents/{doc_id}/conversation/settings  { provider_id, context_checkpoint_seq, context_checkpoint_kind }
//   mention copy   POST  /api/v1/token/issue                        (action_scope chat)

import { extractApiErrorMessage, getRequest, patchRequest, postRequest } from '@shared/api'
import { randomUuid } from '@shared/utils/uuid'
import type {
  ActivityAnchor,
  AiRunFailure,
  AiRunState,
  AiRunList,
  AttachmentRequestActivity,
  ConversationAttachmentConflict,
  ConversationAttachmentGroup,
  ConversationAttachmentRequest,
  ChatDocSettings,
  ChatDocSettingsPatch,
  ChatProvider,
  ChatSettings,
  ChatSettingsPatch,
  ChatSummary,
  ChatAutoTitle,
  CommandDecision,
  CommandRejectedBy,
  CommandRequestActivity,
  CommandStatus,
  ContextMode,
  ConversationActivity,
  ConversationParticipant,
  ConversationScope,
  ConversationTurn,
  ProviderList,
  RunFileDiff,
  RunFileDiffSide,
  SendAction,
  SourceAccessMode,
  TurnsPage,
  WorkChangeActivity,
  WorkChangeFile,
} from './types'

const API = '/api/v1'
const doc = (docId: string) => `${API}/documents/${encodeURIComponent(docId)}`

/** A failed conversation request: the readable message plus the HTTP status / server code. */
export class ConversationRequestError extends Error {
  constructor(message: string, readonly status: number | null, readonly code: string | null, readonly runId: string | null = null) {
    super(message)
    this.name = 'ConversationRequestError'
  }
}

function messageOf(value: unknown): string | null {
  const message = (value as { message?: unknown } | null | undefined)?.message
  return typeof message === 'string' && message.trim() ? message.trim() : null
}

function codeOf(value: unknown): string | null {
  const code = (value as { code?: unknown } | null | undefined)?.code
  return typeof code === 'string' && code ? code : null
}

/** Whether the response body itself carries `text` (a string anywhere in its first levels). */
function bodyCarries(value: unknown, text: string, depth = 0): boolean {
  if (typeof value === 'string') return value.trim() === text
  if (depth > 3 || !value || typeof value !== 'object') return false
  return Object.values(value as Record<string, unknown>).some((child) => bodyCarries(child, text, depth + 1))
}

/**
 * flowgate.v02.0088 F12: the server's own diagnostic of a failed request — '' without an answer (network /
 * timeout), with an empty body, or when the transport only repeats its own English line. It is told apart
 * by where it came from: a text the response body carries is kept even when the Error repeats it.
 */
function serverDetailOf(error: unknown, answered: boolean, data: Record<string, unknown> | null, code: string | null): string {
  if (!answered) return ''
  const detail = extractApiErrorMessage(error, '').trim() || (messageOf(data?.error) ?? messageOf(data) ?? messageOf(data?.detail) ?? '')
  if (!detail || detail === code) return ''
  const transport = error instanceof Error ? error.message.trim() : ''
  return detail === transport && !bodyCarries(data, detail) ? '' : detail
}

/**
 * flowgate.v02.0088 F12: a check this app makes itself (an answer without its required field, an id that
 * is not a document id) is no server diagnostic, so it carries no words — the session shows its localized
 * notice only. Status and code stay null as before.
 */
function clientCheckFailure(): ConversationRequestError {
  return new ConversationRequestError('', null, null)
}

// flowgate.v02.0088 F12: the message is only the server's own diagnostic ('' when it sent none).
// The session reports a failure as a gui.conversation.notice.* code and passes this message on as
// the notice detail, so a localized notice is never followed by an English general line meaning
// the same thing.
function toRequestError(error: unknown): ConversationRequestError {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response
  const data = (response?.data ?? null) as Record<string, unknown> | null
  // Flat `code` (ai-invoke), the `{ ok: false, error: { code } }` envelope (Conversation
  // contract, D0006 §5.1) or a `detail` object.
  const code = codeOf(data) ?? codeOf(data?.error) ?? codeOf(data?.detail)
  const runId = typeof data?.run_id === 'string' ? data.run_id : null
  return new ConversationRequestError(serverDetailOf(error, response != null, data, code), response?.status ?? null, code, runId)
}

async function call<T>(request: () => Promise<{ data: T }>): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    throw toRequestError(error)
  }
}

/** `project.module.group` → its parts; null when the id is not a v0.1 group id. */
export function splitGroupId(groupId: string): { project: string; module: string; group: string } | null {
  const parts = groupId.split('.')
  if (parts.length < 3 || parts.some((p) => p === '')) return null
  return { project: parts[0], module: parts[1], group: parts.slice(2).join('.') }
}

// ── Chat list / title / new chat ──────────────────────────────────────────────────────
interface RawDocument {
  meta?: unknown
  doc_id: string
  title?: string | null
  type_code?: string | null
  created_at?: string | null
  updated_at?: string | null
}

function autoTitleMeta(value: unknown): ChatAutoTitle | null {
  try {
    const meta = typeof value === 'string' ? JSON.parse(value) : value
    if (!meta || typeof meta !== 'object' || Array.isArray(meta)) return null
    const marker = (meta as Record<string, unknown>).chat_auto_title
    if (!marker || typeof marker !== 'object' || Array.isArray(marker)) return null
    const { state, title } = marker as Record<string, unknown>
    if (typeof state !== 'string' || !['owed', 'claimed', 'done', 'manual', 'failed'].includes(state) || typeof title !== 'string') return null
    return { state: state as ChatAutoTitle['state'], provisionalTitle: title }
  } catch { return null }
}

function toChatSummary(raw: RawDocument): ChatSummary {
  return { id: raw.doc_id, title: raw.title || raw.doc_id, updatedAt: raw.updated_at ?? raw.created_at ?? null,
    ...(raw.meta === undefined ? {} : { autoTitle: autoTitleMeta(raw.meta) }) }
}

function rawDocuments(data: unknown): RawDocument[] {
  const list = Array.isArray(data) ? data : (data as { documents?: unknown } | null)?.documents
  return Array.isArray(list) ? list.filter((d): d is RawDocument => Boolean(d && typeof d.doc_id === 'string')) : []
}

function newestFirst(a: { updatedAt: string | null }, b: { updatedAt: string | null }): number {
  return (b.updatedAt ?? '').localeCompare(a.updatedAt ?? '')
}

/** The CH documents of a Flow, newest first. */
export async function fetchChats(scope: ConversationScope): Promise<ChatSummary[]> {
  const data = await call(
    () => getRequest<unknown>(`${API}/documents`, { project_id: scope.projectId, group_id: scope.groupId, type_code: 'CH', limit: 200 }),
  )
  return rawDocuments(data).map(toChatSummary).sort(newestFirst)
}

export async function renameChat(chatId: string, title: string): Promise<ChatSummary> {
  const data = await call(() => patchRequest<RawDocument>(doc(chatId), { title }))
  return toChatSummary({ ...data, doc_id: data?.doc_id || chatId, title: data?.title ?? title })
}

/**
 * A standalone CH (D0006 §3.10): POST /conversations. The server numbers it in the Flow's
 * group and owns every other decision — no workflow head, sequence slot, `prev_doc_id` or
 * module is computed here, so it works in any workflow state and during an AI run.
 */
export interface CreateChatOptions {
  /** flowgate.v02.0047: the chat owes its one AI title on the title it is created with. */
  autoTitle?: boolean
  /**
   * flowgate.v02.0135 T#3: the create intent (`client_request_id`). Every try of one draft sends the same id, so a
   * try whose answer was lost — the server still made the chat — gets that chat back instead of a second one.
   */
  clientRequestId?: string
}

export async function createChat(scope: ConversationScope, title: string, options: CreateChatOptions = {}): Promise<ChatSummary> {
  const body = {
    project_id: scope.projectId,
    group_id: scope.groupId,
    title,
    ...(options.autoTitle ? { auto_title: true } : {}),
    ...(options.clientRequestId ? { client_request_id: options.clientRequestId } : {}),
  }
  const created = await call(
    () => postRequest<RawDocument>(`${API}/conversations`, body),
  )
  if (!created?.doc_id) throw clientCheckFailure()
  return toChatSummary({ ...created, title: created.title ?? title })
}

/**
 * flowgate.v02.0047: the one AI title of a chat created with `autoTitle`, on the project's AI
 * settings and the provider picked in the composer. The server keeps the debt itself: it is
 * paid once, and a title the user set ends it (409 conversation_auto_title_not_owed /
 * conversation_title_changed).
 */
export async function autoTitleChat(chatId: string, providerId: string): Promise<ChatSummary> {
  const data = await call(
    () => postRequest<RawDocument>(`${API}/conversations/${encodeURIComponent(chatId)}/auto-title`, { provider_id: providerId }),
  )
  if (data?.doc_id !== chatId || typeof data?.title !== 'string' || !data.title.trim()) throw clientCheckFailure()
  return toChatSummary(data)
}

// ── Command requests / run changes (D0006 §5.1, §5.5) ─────────────────────────────────
interface RawAnchor {
  anchor_seq?: number | null
  anchor_position?: string | null
  anchor_state?: string | null
}

export interface RawCommand extends RawAnchor {
  request_id: string
  ai_run_id?: string | null
  command?: string | null
  program?: string | null
  args?: string[] | null
  cwd?: string | null
  status?: string | null
  error_code?: string | null
  rejected_by?: string | null
  exit_code?: number | null
  timed_out?: boolean | null
  duration_ms?: number | null
  started_at?: string | null
  stdout_tail?: string | null
  stderr_tail?: string | null
  stdout_truncated?: boolean | null
  stderr_truncated?: boolean | null
  created_at?: string | null
}

export interface RawRunChange extends RawAnchor {
  run_id: string
  files_changed?: number | null
  insertions?: number | null
  deletions?: number | null
  files?: Array<{ path?: string; status?: string | null; insertions?: number | null; deletions?: number | null }> | null
  run_finished_at?: string | null
  created_at?: string | null
}

const COMMAND_STATUSES: readonly CommandStatus[] = [
  'pending_approval',
  'approved',
  'running',
  'succeeded',
  'failed',
  'timed_out',
  'cancelled',
  'rejected',
]

const nullableNumber = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function toAnchor(raw: RawAnchor): ActivityAnchor {
  const position = raw.anchor_position === 'before' || raw.anchor_position === 'after' ? raw.anchor_position : null
  const state =
    raw.anchor_state === 'reply' || raw.anchor_state === 'run_start' || raw.anchor_state === 'ambiguous' || raw.anchor_state === 'unresolved'
      ? raw.anchor_state
      : null
  const seq = nullableNumber(raw.anchor_seq)
  // A half-placed anchor is no placement: the timeline keeps it with the unplaced ones.
  return seq != null && position ? { seq, position, state } : { seq: null, position: null, state }
}

export function toCommand(raw: RawCommand): CommandRequestActivity {
  // An unknown status is shown as failed rather than as something the user can still act on.
  const state = COMMAND_STATUSES.find((s) => s === raw.status) ?? 'failed'
  const rejectedBy: CommandRejectedBy | null = raw.rejected_by === 'policy' || raw.rejected_by === 'user' ? raw.rejected_by : null
  const command = raw.command || [raw.program, ...(raw.args ?? [])].filter(Boolean).join(' ')
  return {
    kind: 'command',
    id: raw.request_id,
    runId: raw.ai_run_id ?? null,
    createdAt: raw.created_at ?? null,
    command,
    cwd: raw.cwd ?? null,
    state,
    errorCode: raw.error_code ?? null,
    rejectedBy: state === 'rejected' ? rejectedBy : null,
    exitCode: nullableNumber(raw.exit_code),
    timedOut: Boolean(raw.timed_out),
    durationMs: nullableNumber(raw.duration_ms),
    startedAt: raw.started_at ?? null,
    stdout: typeof raw.stdout_tail === 'string' ? raw.stdout_tail : null,
    stderr: typeof raw.stderr_tail === 'string' ? raw.stderr_tail : null,
    stdoutTruncated: Boolean(raw.stdout_truncated),
    stderrTruncated: Boolean(raw.stderr_truncated),
    anchor: toAnchor(raw),
  }
}

export function toRunChange(raw: RawRunChange): WorkChangeActivity {
  const files: WorkChangeFile[] = (raw.files ?? [])
    .filter((f) => f && typeof f.path === 'string' && f.path)
    .map((f) => ({
      path: f.path as string,
      status: f.status ?? null,
      insertions: nullableNumber(f.insertions),
      deletions: nullableNumber(f.deletions),
    }))
  return {
    kind: 'change',
    id: raw.run_id,
    runId: raw.run_id,
    createdAt: raw.created_at ?? raw.run_finished_at ?? null,
    files,
    filesChanged: nullableNumber(raw.files_changed) ?? files.length,
    insertions: nullableNumber(raw.insertions),
    deletions: nullableNumber(raw.deletions),
    anchor: toAnchor(raw),
  }
}

// ── flowgate.chat.0017 CH0002: AI attachment upload requests in the chat ──────────────
type RawRecord = Record<string, unknown>
const rec = (value: unknown): RawRecord => (value && typeof value === 'object' && !Array.isArray(value) ? (value as RawRecord) : {})
const txt = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const nonNegative = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

function toAttachmentConflict(raw: unknown): ConversationAttachmentConflict | null {
  const row = rec(raw)
  const docId = txt(row.doc_id)
  const filename = txt(row.filename)
  if (!docId || !filename) return null
  return { docId, filename, caseOnly: row.case_only === true, size: nonNegative(row.size) ?? 0, sha256: txt(row.content_sha256) }
}

/** One request of `GET /chat-activity` `attachments[].requests`; null without an id or a name. */
export function toAttachmentRequest(raw: unknown): ConversationAttachmentRequest | null {
  const row = rec(raw)
  const id = txt(row.request_id)
  const filename = txt(row.filename)
  if (!id || !filename) return null
  const decision = txt(row.decision)
  const preview = txt(row.preview)
  return {
    id,
    origin: txt(row.origin) ?? 'chat',
    status: txt(row.status) ?? 'pending',
    projectId: txt(row.project_id),
    flowId: txt(row.flow_id),
    nodeKey: txt(row.node_key),
    targetDocId: txt(row.target_doc_id),
    targetTitle: txt(row.target_title),
    runId: txt(row.ai_run_id),
    providerName: txt(row.provider_name),
    filename,
    originalFilename: txt(row.original_filename),
    size: nonNegative(row.size) ?? 0,
    contentType: txt(row.content_type),
    sha256: txt(row.content_sha256),
    note: txt(row.note),
    decision: decision === 'keep_both' || decision === 'overwrite' ? decision : null,
    decidedBy: txt(row.decided_by),
    decidedAt: txt(row.decided_at),
    finalDocId: txt(row.final_doc_id),
    finalFilename: txt(row.final_filename),
    errorCode: txt(row.error_code),
    expiresAt: txt(row.expires_at),
    createdAt: txt(row.created_at),
    preview: preview === 'image' ? 'image' : preview === 'text' ? 'text' : null,
    conflict: toAttachmentConflict(row.conflict),
  }
}

/** `GET /chat-activity` `attachments` -> one group per run. */
export function toAttachmentGroups(value: unknown): ConversationAttachmentGroup[] {
  return (Array.isArray(value) ? value : []).flatMap((raw) => {
    const row = rec(raw)
    const id = txt(row.id)
    if (!id) return []
    const position = txt(row.anchor_position)
    const seq = row.anchor_seq
    return [{
      id,
      runId: txt(row.run_id),
      anchorSeq: typeof seq === 'number' && Number.isInteger(seq) ? seq : null,
      anchorPosition: position === 'before' || position === 'after' ? position : null,
      anchorState: txt(row.anchor_state),
      createdAt: txt(row.created_at),
      pending: nonNegative(row.pending) ?? 0,
      requests: (Array.isArray(row.requests) ? row.requests : []).flatMap((r) => toAttachmentRequest(r) ?? []),
    }]
  })
}

/** flowgate.chat.0017 CH0002: one run's attachment upload requests -> one timeline block. */
export function toAttachmentActivity(group: ConversationAttachmentGroup): AttachmentRequestActivity {
  return {
    kind: 'attachments',
    id: `attachments:${group.id}`,
    runId: group.runId,
    createdAt: group.createdAt,
    pending: group.pending,
    requests: group.requests,
    anchor: toAnchor({ anchor_seq: group.anchorSeq, anchor_position: group.anchorPosition, anchor_state: group.anchorState }),
  }
}

/** Commands, run changes and attachment requests of one CH, each with its server anchor (reconciled server-side). */
export async function fetchActivities(chatId: string): Promise<ConversationActivity[]> {
  const data = await call(
    () =>
      getRequest<{ commands?: RawCommand[] | null; changes?: RawRunChange[] | null; attachments?: unknown }>(
        `${API}/chat-activity/${encodeURIComponent(chatId)}`,
      ),
  )
  const commands = (data?.commands ?? []).filter((c) => c && typeof c.request_id === 'string').map(toCommand)
  const changes = (data?.changes ?? []).filter((c) => c && typeof c.run_id === 'string').map(toRunChange)
  const attachments = toAttachmentGroups(data?.attachments).map(toAttachmentActivity)
  return [...commands, ...changes, ...attachments]
}

/** The decision body carries the decision only — no group / doc id (D0006 §3.2). */
export async function decideCommand(_chatId: string, commandId: string, decision: CommandDecision): Promise<CommandRequestActivity> {
  const data = await call(
    () => postRequest<{ request?: RawCommand | null }>(`${API}/chat-commands/${encodeURIComponent(commandId)}/decision`, { decision }),
  )
  if (!data?.request?.request_id) throw clientCheckFailure()
  return toCommand(data.request)
}

function toDiffSide(raw: Partial<RunFileDiffSide> | null | undefined): RunFileDiffSide {
  return {
    exists: Boolean(raw?.exists),
    binary: Boolean(raw?.binary),
    truncated: Boolean(raw?.truncated),
    size: Number(raw?.size) || 0,
    content: typeof raw?.content === 'string' ? raw.content : null,
  }
}

/** Run start / run end content of one changed path, from the trees the server stored. */
export async function fetchRunFileDiff(chatId: string, runId: string, path: string): Promise<RunFileDiff> {
  const data = await call(
    () =>
      getRequest<{ data?: { path?: string; status?: string; old?: RunFileDiffSide; new?: RunFileDiffSide } | null }>(
        `${API}/chat-activity/${encodeURIComponent(chatId)}/runs/${encodeURIComponent(runId)}/diff`,
        { path },
      ),
  )
  const body = data?.data
  return { runId, path: body?.path || path, status: body?.status || 'M', old: toDiffSide(body?.old), new: toDiffSide(body?.new) }
}

// ── Turns ────────────────────────────────────────────────────────────────────────────
interface RawTurn {
  source_run_id?: string | null
  seq: number
  speaker: string
  participant_key?: string | null
  display_name?: string | null
  body?: string | null
  based_on_seq?: number | null
  stale_since_seq?: number | null
  created_at?: string | null
}

interface RawParticipant {
  participant_key: string
  kind?: string | null
  display_name?: string | null
  last_read_seq?: number | null
}

interface RawTurnsPage {
  turns?: RawTurn[] | null
  participants?: RawParticipant[] | null
  me?: RawParticipant | null
  head_seq?: number | null
  next_after_seq?: number | null
  prev_before_seq?: number | null
}

export function toTurn(raw: RawTurn): ConversationTurn {
  return {
    seq: Number(raw.seq) || 0,
    speaker: raw.speaker,
    participantKey: raw.participant_key ?? null,
    displayName: raw.display_name ?? null,
    body: raw.body ?? '',
    sourceRunId: textOrNull(raw.source_run_id),
    basedOnSeq: raw.based_on_seq ?? null,
    staleSinceSeq: raw.stale_since_seq ?? null,
    createdAt: raw.created_at ?? null,
  }
}

export function toParticipant(raw: RawParticipant): ConversationParticipant {
  return {
    key: raw.participant_key,
    kind: raw.kind ?? 'user',
    displayName: raw.display_name ?? null,
    lastReadSeq: Number(raw.last_read_seq) || 0,
  }
}

export interface TurnsQuery {
  includeHead?: boolean
  afterSeq?: number
  beforeSeq?: number
  limit: number
}

export async function fetchTurns(chatId: string, query: TurnsQuery): Promise<TurnsPage> {
  const params: Record<string, unknown> = { limit: query.limit }
  if (query.includeHead) params.include_head = 1
  if (query.afterSeq != null) params.after_seq = query.afterSeq
  if (query.beforeSeq != null) params.before_seq = query.beforeSeq
  const data = await call(() => getRequest<RawTurnsPage>(`${doc(chatId)}/conversation/turns`, params))
  return {
    turns: (data?.turns ?? []).map(toTurn),
    participants: (data?.participants ?? []).filter((p) => p?.participant_key).map(toParticipant),
    myReadSeq: data?.me ? Number(data.me.last_read_seq) || 0 : null,
    headSeq: Number(data?.head_seq) || 0,
    nextAfterSeq: data?.next_after_seq ?? null,
    prevBeforeSeq: data?.prev_before_seq ?? null,
  }
}

/** Same key shape as the v0.1 composer (`sess_<uuid>`, L0004 §2-17). */
export function newIdempotencyKey(): string {
  return `sess_${randomUuid()}`
}

export interface PostTurnInput {
  body: string
  idempotencyKey: string
  basedOnSeq: number
  traceId?: string
}

export async function postTurn(chatId: string, input: PostTurnInput): Promise<{ turn: ConversationTurn | null; headSeq: number }> {
  const payload = { body: input.body, idempotency_key: input.idempotencyKey, based_on_seq: input.basedOnSeq }
  type Response = { turn?: RawTurn | null; head_seq?: number }
  const path = `${doc(chatId)}/conversation/turn`
  const data = await call(() => input.traceId
    ? postRequest<Response>(path, payload, { headers: { 'X-Conversation-Trace': input.traceId } })
    : postRequest<Response>(path, payload))
  return { turn: data?.turn ? toTurn(data.turn) : null, headSeq: Number(data?.head_seq) || 0 }
}

export async function markRead(chatId: string, lastReadSeq: number): Promise<number | null> {
  const data = await call(
    () => postRequest<{ me?: RawParticipant | null }>(`${doc(chatId)}/conversation/read`, { last_read_seq: lastReadSeq, reason: 'viewed' }),
  )
  return data?.me ? Number(data.me.last_read_seq) || 0 : null
}

// ── Providers ────────────────────────────────────────────────────────────────────────
export async function fetchProviders(projectId: string): Promise<ProviderList> {
  const data = await call(
    () => getRequest<{ providers?: Array<{ id: string; name?: string | null }>; default_provider_id?: string | null }>(
      `${API}/ai-invoke/providers`,
      { project: projectId },
    ),
  )
  const providers: ChatProvider[] = (data?.providers ?? [])
    .filter((p) => p && typeof p.id === 'string')
    .map((p) => ({ id: p.id, name: p.name || p.id }))
  return { providers, defaultProviderId: data?.default_provider_id ?? null }
}

// ── Chat AI run ──────────────────────────────────────────────────────────────────────
/**
 * flowgate.v02.0152: `clientRequestId` names one [AI 호출] press. The session sends the same id again when a /start
 * got no answer, and the server answers that resend with the run the first request admitted (running or finished)
 * instead of starting a second one; 409 `start_pending` means the first request is still being admitted.
 */
export async function startChatRun(chatId: string, providerId: string | null, clientRequestId?: string): Promise<string | null> {
  const parts = splitGroupId(chatId)
  if (!parts) throw clientCheckFailure()
  // A CH id is `project.module.group.seq-TYPE`; the group is the third segment.
  const group = chatId.split('.')[2]
  const data = await call(
    () => postRequest<{ run_id?: string }>(`${API}/ai-invoke/start`, {
      project: parts.project,
      module: parts.module,
      group,
      doc_ref: chatId,
      action_scope: 'chat',
      mode: 'single',
      provider_id: providerId || undefined,
      ...(clientRequestId ? { client_request_id: clientRequestId } : {}),
    }),
  )
  return data?.run_id ?? null
}

interface RawFallbackEntry {
  detail?: string | null
  remote_final?: { cause?: string | null; message?: string | null; workdir?: string | null } | null
}

const textOrNull = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value.trim() : null)

/** flowgate.v02.0135 CH0006: the last failed connection of a run that started none. */
function runFailure(history: unknown): AiRunFailure | null {
  if (!Array.isArray(history) || !history.length) return null
  const last = (history[history.length - 1] ?? {}) as RawFallbackEntry
  const remote = last.remote_final ?? {}
  const failure = {
    cause: textOrNull(remote.cause),
    message: textOrNull(remote.message),
    workdir: textOrNull(remote.workdir),
    detail: textOrNull(last.detail),
  }
  return failure.cause || failure.detail ? failure : null
}

/** Extra detail fields stay optional for sources speaking the older run contract. */
export interface ChatRunState extends AiRunState {
  succeeded?: boolean | null
  stopCode?: string | null
  apiTurnsUsed?: number | null
  providerId?: string | null
}

export async function fetchRun(runId: string): Promise<ChatRunState> {
  const data = await call(
    () => getRequest<{ run_id?: string; status?: string; end_reason?: string | null; outcome?: string | null;
      exit_code?: number | null; doc_ref?: string | null; action_scope?: string | null; group_id?: string | null;
      finished_at?: string | null; fallback_history?: unknown; succeeded?: boolean | null; stop_code?: string | null;
      api_turns_used?: number | null; provider_id?: string | null }>(`${API}/ai-invoke/${encodeURIComponent(runId)}`),
  )
  const endReason = data?.end_reason ?? null
  const failure = endReason === 'all_providers_failed' ? runFailure(data?.fallback_history) : null
  return {
    runId: textOrNull(data?.run_id) ?? runId,
    status: data?.status === 'finished' ? 'finished' :
      ['running', 'cancelling', 'pause_requested'].includes(data?.status ?? '') ? 'running' : 'unknown',
    endReason, outcome: data?.outcome ?? null, exitCode: nullableNumber(data?.exit_code),
    succeeded: typeof data?.succeeded === 'boolean' ? data.succeeded : null,
    stopCode: textOrNull(data?.stop_code), apiTurnsUsed: nullableNumber(data?.api_turns_used), providerId: textOrNull(data?.provider_id),
    docRef: textOrNull(data?.doc_ref), actionScope: textOrNull(data?.action_scope), groupId: textOrNull(data?.group_id),
    finishedAt: textOrNull(data?.finished_at), ...(failure ? { failure } : {}),
  }
}

/** The server clamps this existing API at 100 and has no cursor / CH filter. */
export async function fetchChatRuns(scope: ConversationScope, limit: number): Promise<AiRunList> {
  const data = await call(() => getRequest<{ items?: Array<{ run_id?: string; doc_ref?: string | null }>;
    total?: number; has_more?: boolean }>(`${API}/ai-invoke/runs`, { group_id: scope.groupId, limit: Math.min(100, Math.max(1, limit)) }))
  const items = (data?.items ?? []).filter((item) => typeof item?.run_id === 'string' && item.run_id.trim()).map((item) => ({
    runId: item.run_id!, docRef: textOrNull(item.doc_ref),
  }))
  const total = nullableNumber(data?.total)
  return { items, total, hasMore: data?.has_more !== false || total == null || total > items.length }
}

export async function cancelRun(runId: string): Promise<void> {
  await call(() => postRequest<unknown>(`${API}/ai-invoke/${encodeURIComponent(runId)}/cancel`, {}))
}

/** The chat run still owning this CH document, if any (re-attach after a remount). */
export async function fetchActiveChatRun(chatId: string): Promise<string | null> {
  const groupId = chatId.split('.').slice(0, 3).join('.')
  const data = await call(
    () => getRequest<{ active?: boolean; run_id?: string; status?: string; doc_ref?: string }>(`${API}/ai-invoke/active`, { group_id: groupId }),
  )
  if (!data?.active || !data.run_id || data.doc_ref !== chatId || data.status === 'finished') return null
  return data.run_id
}

// ── Chat settings ────────────────────────────────────────────────────────────────────
interface RawChatSettings {
  source_access_scope?: { project_id?: unknown; group_id?: unknown } | null
  settings?: {
    send_action?: string
    context_mode?: string
    context_turns?: number
    source_access_mode?: string
  } | null
  domain?: {
    context_turns_presets?: number[]
    context_turns_min?: number
    context_turns_max?: number
    source_access_mode?: string[]
  } | null
}

const SEND_ACTIONS: readonly SendAction[] = ['copy_mention', 'invoke_ai', 'none']
const SOURCE_MODES: readonly SourceAccessMode[] = ['read_only', 'edit', 'edit_once']

export const DEFAULT_CHAT_SETTINGS: ChatSettings = {
  sendAction: 'none',
  contextMode: 'recent',
  contextTurns: 20,
  sourceAccessMode: 'read_only',
  sourceAccessScope: null,
  contextTurnsPresets: [5, 10, 15, 20, 30],
  contextTurnsMin: 1,
  contextTurnsMax: 999,
  sourceAccessModes: [...SOURCE_MODES],
}

export function toChatSettings(raw: RawChatSettings | null | undefined): ChatSettings {
  const s = raw?.settings ?? {}
  const d = raw?.domain ?? {}
  const sendAction = SEND_ACTIONS.find((v) => v === s.send_action) ?? DEFAULT_CHAT_SETTINGS.sendAction
  const contextMode: ContextMode = s.context_mode === 'all' || s.context_mode === 'checkpoint' ? s.context_mode : 'recent'
  const sourceAccessMode = SOURCE_MODES.find((v) => v === s.source_access_mode) ?? DEFAULT_CHAT_SETTINGS.sourceAccessMode
  return {
    sendAction,
    contextMode,
    contextTurns: Number.isInteger(s.context_turns) ? Number(s.context_turns) : DEFAULT_CHAT_SETTINGS.contextTurns,
    sourceAccessMode,
    sourceAccessScope: typeof raw?.source_access_scope?.project_id === 'string' && typeof raw?.source_access_scope?.group_id === 'string'
      ? { projectId: raw.source_access_scope.project_id, groupId: raw.source_access_scope.group_id }
      : null,
    contextTurnsPresets: Array.isArray(d.context_turns_presets) ? d.context_turns_presets : DEFAULT_CHAT_SETTINGS.contextTurnsPresets,
    contextTurnsMin: d.context_turns_min ?? DEFAULT_CHAT_SETTINGS.contextTurnsMin,
    contextTurnsMax: d.context_turns_max ?? DEFAULT_CHAT_SETTINGS.contextTurnsMax,
    // An unknown server value fails closed: filtered out rather than shown unlabeled.
    sourceAccessModes: Array.isArray(d.source_access_mode)
      ? SOURCE_MODES.filter((m) => d.source_access_mode!.includes(m))
      : [],
  }
}

function chatSettingsUrl(scope?: ConversationScope): string {
  return `${API}/me/chat-settings${scope ? `?project_id=${encodeURIComponent(scope.projectId)}&group_id=${encodeURIComponent(scope.groupId)}` : ''}`
}

export async function fetchChatSettings(scope?: ConversationScope): Promise<ChatSettings> {
  return toChatSettings(await call(() => getRequest<RawChatSettings>(chatSettingsUrl(scope))))
}

export async function saveChatSettings(patch: ChatSettingsPatch, scope?: ConversationScope): Promise<ChatSettings> {
  return toChatSettings(await call(() => patchRequest<RawChatSettings>(chatSettingsUrl(scope), patch)))
}

// ── Per-chat settings (flowgate.v02test.0025) ───────────────────────────────────────────
interface RawChatDocSettings {
  settings?: { provider_id?: string | null; context_checkpoint_seq?: number | null; context_checkpoint_kind?: string | null } | null
}

function toChatDocSettings(raw: RawChatDocSettings | null | undefined): ChatDocSettings {
  const s = raw?.settings ?? {}
  const seq = s.context_checkpoint_seq
  const checkpointSeq = Number.isInteger(seq) && Number(seq) >= 1 ? Number(seq) : null
  return {
    providerId: typeof s.provider_id === 'string' && s.provider_id ? s.provider_id : null,
    checkpointSeq,
    checkpointKind: checkpointSeq == null ? null : s.context_checkpoint_kind === 'exclude' ? 'exclude' : 'include',
  }
}

/** The user's AI pick and context checkpoint of one chat. */
export async function fetchChatDocSettings(chatId: string): Promise<ChatDocSettings> {
  return toChatDocSettings(await call(() => getRequest<RawChatDocSettings>(`${doc(chatId)}/conversation/settings`)))
}

/** Saves only the sent fields; answers the stored settings. */
export async function saveChatDocSettings(chatId: string, patch: ChatDocSettingsPatch): Promise<ChatDocSettings> {
  return toChatDocSettings(await call(() => patchRequest<RawChatDocSettings>(`${doc(chatId)}/conversation/settings`, patch)))
}

// ── Mention copy ─────────────────────────────────────────────────────────────────────
/** The compact chat mention the server builds with an edit token (0293, action_scope chat). */
export async function issueChatMention(chatId: string): Promise<string> {
  const parts = splitGroupId(chatId)
  if (!parts) throw clientCheckFailure()
  const data = await call(
    () => postRequest<{ mention?: string | null }>(`${API}/token/issue`, {
      project: parts.project,
      module: parts.module,
      group: chatId.split('.')[2],
      doc_ref: chatId,
      action_scope: 'chat',
    }),
  )
  if (!data?.mention) throw clientCheckFailure()
  return data.mention
}
