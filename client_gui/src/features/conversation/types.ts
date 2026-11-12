// flowgate.v02.0031 / v02.0032 — Conversation part models. A conversation of record is an existing CH
// document whose body is a list of turns (v0.1 ConversationView, P0003 §0-2); the part keeps
// those turns as they are and adds the frontend shapes it needs to show one timeline.

/** Validated server title obligation. Missing/invalid metadata never implies owed. */
export interface ChatAutoTitle {
  state: 'owed' | 'claimed' | 'done' | 'manual' | 'failed'
  provisionalTitle: string
}

/** One conversation (a CH document) the chat list can switch to. */
export interface ChatSummary {
  autoTitle?: ChatAutoTitle | null
  id: string
  title: string
  updatedAt: string | null
}

/** Where the conversations live: the Designer's active Flow is a v0.1 group. */
export interface ConversationScope {
  projectId: string
  /** v0.1 group id (`project.module.group`) — FlowRef.groupId. */
  groupId: string
}

/** One stored turn (P0003 §0-2 — same fields the server returns). */
export interface ConversationTurn {
  seq: number
  speaker: 'user' | 'ai' | string
  participantKey?: string | null
  /** The AI / model badge (0293 R0001: a badge only, identity is participantKey). */
  displayName?: string | null
  body: string
  /** Server attribution only; null / missing is unknown. */
  sourceRunId?: string | null
  basedOnSeq?: number | null
  staleSinceSeq?: number | null
  createdAt: string | null
}

/** P0003 §0-4. */
export interface ConversationParticipant {
  key: string
  kind: 'user' | 'ai' | string
  displayName: string | null
  lastReadSeq: number
}

/** One page of GET /documents/{id}/conversation/turns. */
export interface TurnsPage {
  turns: ConversationTurn[]
  participants: ConversationParticipant[]
  myReadSeq: number | null
  headSeq: number
  nextAfterSeq: number | null
  prevBeforeSeq: number | null
}

/** Server state of record of a command request (D0006 §3.1) — all eight, never inferred. */
export type CommandStatus =
  | 'pending_approval'
  | 'approved'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'rejected'

/**
 * What a command card shows: the server status, or one of the two optimistic client-only
 * states while this client's own decision is in flight (D0006 §5.5). The optimistic states
 * never come from the server and always fall back to a server status.
 */
export type CommandState = CommandStatus | 'approving' | 'rejecting'

/** Who rejected a request: the user's command policy or the user (D0006 §5.2). */
export type CommandRejectedBy = 'policy' | 'user'

export type AnchorPosition = 'before' | 'after'
/** How the server placed an activity (D0006 §3.9.1). */
export type AnchorState = 'reply' | 'run_start' | 'ambiguous' | 'unresolved'

/**
 * The server-decided place of a command / run change in the conversation: before or after
 * turn `seq` (`0` = before the first turn). `seq` is null while the server could not place
 * it; the timeline then keeps it after the turns instead of guessing from `createdAt`.
 */
export interface ActivityAnchor {
  seq: number | null
  position: AnchorPosition | null
  state: AnchorState | null
}

/** An AI request to run a command, waiting in the timeline for the user (R0001 §5). */
export interface CommandRequestActivity {
  kind: 'command'
  /** request_id */
  id: string
  runId?: string | null
  createdAt: string | null
  /** The command line or a description of what will run. */
  command: string
  cwd?: string | null
  state: CommandState
  /** Cancel / failure reason code (`chat_command_*`, D0006 §5.3); null otherwise. */
  errorCode?: string | null
  /** Set only on a rejected request. */
  rejectedBy?: CommandRejectedBy | null
  exitCode?: number | null
  timedOut?: boolean
  durationMs?: number | null
  /** When the process started; null while it never ran (pending, rejected, withdrawn). */
  startedAt?: string | null
  /**
   * The stored stdout / stderr tails (the server keeps the last 64 KiB of each, redacted).
   * They are only meaningful once `startedAt` is set; the `*Truncated` flags say the start was cut.
   */
  stdout?: string | null
  stderr?: string | null
  stdoutTruncated?: boolean
  stderrTruncated?: boolean
  /** Extra text a source supplies (not used by the HTTP source). */
  detail?: string | null
  anchor?: ActivityAnchor | null
  /** The server refused this user's decision (403): the card keeps its state, without buttons. */
  decisionForbidden?: boolean
}

/** One changed file of a run change; numbers are null when git has none (binary). */
export interface WorkChangeFile {
  path: string
  /** git name-status letter (`A`, `M`, `D`, `T` …), null when unknown. */
  status: string | null
  insertions: number | null
  deletions: number | null
}

/** File changes one AI run produced, recorded where they happened (R0001 §6, D0006 §3.8). */
export interface WorkChangeActivity {
  kind: 'change'
  /** One run change per run: the run id. */
  id: string
  runId: string
  createdAt: string | null
  files: WorkChangeFile[]
  /** Run totals; insertions / deletions are null when no file has numbers. */
  filesChanged: number
  insertions: number | null
  deletions: number | null
  anchor?: ActivityAnchor | null
}

/** One side of a stored run change file diff (same payload as the Group changes diff). */
export interface RunFileDiffSide {
  exists: boolean
  binary: boolean
  truncated: boolean
  size: number
  content: string | null
}

/** GET /chat-activity/{doc}/runs/{run}/diff?path= — run start / run end content of one path. */
export interface RunFileDiff {
  runId: string
  path: string
  status: string
  old: RunFileDiffSide
  new: RunFileDiffSide
}

export type StatusTone = 'info' | 'success' | 'warning' | 'danger'

/** A state event of the conversation run itself (AI run started / finished / failed …). */
export interface StatusActivity {
  kind: 'status'
  id: string
  createdAt: string | null
  tone: StatusTone
  /** Read-derived run status; never treated as a durable server event. */
  runId?: string
  chatId?: string
  runResult?: AiRunState
  /** A known event (`gui.conversation.status.<code>`), or null when `text` says it all. */
  code: string | null
  /** Source-supplied text, or extra detail appended to a known event. */
  text: string | null
}

/** A same-name attachment already on a request's target document (server `conflict`). */
export interface ConversationAttachmentConflict {
  readonly docId: string
  readonly filename: string
  readonly caseOnly: boolean
  readonly size: number
  readonly sha256: string | null
}

/**
 * flowgate.chat.0017 CH0002: one AI attachment upload request as the chat reads it (`GET /chat-activity` `attachments`).
 * The same shape the card tab's request row takes, so the container's row component draws either.
 */
export interface ConversationAttachmentRequest {
  readonly id: string
  readonly origin: string
  readonly status: string
  readonly projectId: string | null
  readonly flowId: string | null
  readonly nodeKey: string | null
  readonly targetDocId: string | null
  readonly targetTitle: string | null
  readonly runId: string | null
  readonly providerName: string | null
  readonly filename: string
  readonly originalFilename: string | null
  readonly size: number
  readonly contentType: string | null
  readonly sha256: string | null
  readonly note: string | null
  readonly decision: 'keep_both' | 'overwrite' | null
  readonly decidedBy: string | null
  readonly decidedAt: string | null
  readonly finalDocId: string | null
  readonly finalFilename: string | null
  readonly errorCode: string | null
  readonly expiresAt: string | null
  readonly createdAt: string | null
  readonly preview: 'text' | 'image' | null
  readonly conflict: ConversationAttachmentConflict | null
}

/** One run's requests, placed by the server (after the run's last AI reply). */
export interface ConversationAttachmentGroup {
  readonly id: string
  readonly runId: string | null
  readonly anchorSeq: number | null
  readonly anchorPosition: 'before' | 'after' | null
  readonly anchorState: string | null
  readonly createdAt: string | null
  readonly pending: number
  readonly requests: readonly ConversationAttachmentRequest[]
}

/**
 * flowgate.chat.0017 CH0002: the AI attachment upload requests one run made, shown as one block right below that run's
 * AI message (server anchor: after the run's last reply). `id` is the run id (or the request id without a run).
 */
export interface AttachmentRequestActivity {
  kind: 'attachments'
  id: string
  runId: string | null
  createdAt: string | null
  pending: number
  requests: readonly ConversationAttachmentRequest[]
  anchor?: ActivityAnchor | null
}

/**
 * Provide/inject key of the request row the container hands in (ConversationPanel `attachmentRequestRow`): a component
 * taking `request` and emitting `decided`. The part itself draws only a plain line without one.
 */
export const ATTACHMENT_REQUEST_ROW = 'fg.conversation.attachmentRequestRow'

/**
 * Provide/inject key of the bulk bar the container hands in (ConversationPanel `attachmentRequestBulk`): a component
 * taking `requests` and `selected` (v-model) and emitting `decided` with `{ results: [{ id, request }] } | null`.
 * Without one a block offers no approve all / reject all (its rows still decide one by one).
 */
export const ATTACHMENT_REQUEST_BULK = 'fg.conversation.attachmentRequestBulk'

/**
 * Provide/inject key of the panel's "read the activities again" (ConversationPanel provides it): a request block calls
 * it after a bulk call — also a failed one, which may have been processed in part — so the rows show the server state.
 */
export const ATTACHMENT_REQUEST_REFRESH = 'fg.conversation.attachmentRequestRefresh'


export type ConversationActivity = CommandRequestActivity | WorkChangeActivity | StatusActivity | AttachmentRequestActivity

/** The activities TimelineActivity draws (attachment request blocks have their own component). */
export type TimelineActivityEntry = CommandRequestActivity | WorkChangeActivity | StatusActivity

/** The decisions the card offers (the wire also has `cancel`, not offered here — D0006 §3.2). */
export type CommandDecision = 'approve' | 'reject'

/** A provider the composer's AI select offers (GET /ai-invoke/providers). */
export interface ChatProvider {
  id: string
  name: string
}

export interface ProviderList {
  providers: ChatProvider[]
  defaultProviderId: string | null
}

export type SendAction = 'copy_mention' | 'invoke_ai' | 'none'
export type ContextMode = 'recent' | 'all'
/** AI source access mode (group 0515/0568, flowgate.v02test.0026): read / execute (commands only) / edit / edit once. */
export type SourceAccessMode = 'read_only' | 'execute' | 'edit' | 'edit_once'

/** GET/PATCH /me/chat-settings (D0008 / P0009). */
export interface ChatSettings {
  sendAction: SendAction
  contextMode: ContextMode
  contextTurns: number
  sourceAccessMode: SourceAccessMode
  sourceAccessScope: ConversationScope | null
  contextTurnsPresets: number[]
  contextTurnsMin: number
  contextTurnsMax: number
  sourceAccessModes: SourceAccessMode[]
}

export interface ChatSettingsPatch {
  send_action?: SendAction
  context_mode?: ContextMode
  context_turns?: number
  source_access_mode?: SourceAccessMode
}

/** flowgate.v02.0135 CH0006: why the run's last connection failed (its fallback_history entry). */
export interface AiRunFailure {
  /** Remote Agent cause (permission_denied, workdir_missing, ...), null when not classified. */
  cause: string | null
  /** The Agent's own (secret-free) words. */
  message: string | null
  /** The Job's working directory on the Agent machine. */
  workdir: string | null
  /** The server's failure line for the connection. */
  detail: string | null
}

/** One chat AI run as the composer tracks it. */
export interface AiRunState {
  runId: string
  status: 'running' | 'finished' | 'unknown'
  endReason: string | null
  outcome?: string | null
  exitCode?: number | null
  docRef?: string | null
  actionScope?: string | null
  groupId?: string | null
  finishedAt?: string | null
  failure?: AiRunFailure | null
}

/** Existing group run-history window (no CH filter or pagination cursor). */
export interface AiRunList {
  items: Array<{ runId: string; docRef: string | null }>
  total: number | null
  hasMore: boolean
}
