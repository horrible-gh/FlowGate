// flowgate.v02.0031 / v02.0032 — Conversation part models. A conversation of record is an existing CH
// document whose body is a list of turns (v0.1 ConversationView, P0003 §0-2); the part keeps
// those turns as they are and adds the frontend shapes it needs to show one timeline.

/** One conversation (a CH document) the chat list can switch to. */
export interface ChatSummary {
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
  /** A known event (`gui.conversation.status.<code>`), or null when `text` says it all. */
  code: string | null
  /** Source-supplied text, or extra detail appended to a known event. */
  text: string | null
}

export type ConversationActivity = CommandRequestActivity | WorkChangeActivity | StatusActivity

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
/** AI source access mode (group 0515/0568): read / edit / edit once. */
export type SourceAccessMode = 'read_only' | 'edit' | 'edit_once'

/** GET/PATCH /me/chat-settings (D0008 / P0009). */
export interface ChatSettings {
  sendAction: SendAction
  contextMode: ContextMode
  contextTurns: number
  sourceAccessMode: SourceAccessMode
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

/** One chat AI run as the composer tracks it. */
export interface AiRunState {
  runId: string
  status: 'running' | 'finished'
  endReason: string | null
}
