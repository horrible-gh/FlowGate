// flowgate.default.0670 T0004 — wire shapes of GET /api/v1/chat-activity/{doc_id}.
// Mirrors chat_command_service.public() and chat_run_changes_service.public().

// flowgate.default.0675 T0004 — the server-decided conversation position of a row.
// anchor_state absent = a server without the anchor contract (legacy placement).
export type AnchorPosition = 'before' | 'after'
export type AnchorState = 'reply' | 'run_start' | 'ambiguous' | 'unresolved'

export interface ActivityAnchor {
  anchor_seq?: number | null
  anchor_position?: AnchorPosition | null
  anchor_state?: AnchorState | null
}

export interface ChatCommand extends ActivityAnchor {
  request_id: string
  ai_run_id: string
  doc_id: string
  program: string
  args: string[]
  command: string
  cwd: string
  timeout_seconds: number
  category: string
  high_impact: boolean
  policy: string
  provider_name?: string | null
  status: string
  decision_source?: string | null
  decided_by?: string | null
  started_at?: string | null
  finished_at?: string | null
  duration_ms?: number | null
  exit_code?: number | null
  timed_out?: boolean
  stdout_tail?: string | null
  stderr_tail?: string | null
  error_code?: string | null
  created_at?: string | null
}

export interface RunChangeFile {
  path: string
  status: string
  insertions: number | null
  deletions: number | null
}

export interface RunChange extends ActivityAnchor {
  run_id: string
  doc_id: string
  run_started_at?: string | null
  run_finished_at?: string | null
  files_changed: number
  insertions: number | null
  deletions: number | null
  files: RunChangeFile[]
  created_at?: string | null
}

// One command or change row as ConversationView places it (0675 T0004).
export type ActivityItem =
  | { kind: 'command'; key: string; createdAt: string; command: ChatCommand }
  | { kind: 'change'; key: string; createdAt: string; change: RunChange }

export const COMMAND_POLICIES = ['always_approve', 'user_approval', 'reject'] as const
export const COMMAND_POLICY_DEFAULT = 'user_approval'
export const OPEN_COMMAND_STATUSES = ['pending_approval', 'approved', 'running']

export function runClock(ts?: string | null): string {
  if (!ts) return ''
  const d = new Date(ts)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
}
