import { beforeEach, describe, expect, it, vi } from 'vitest'

// flowgate.v02.0032 — the Conversation production contract in the adapter (D0006 §5.1, §5.5):
// activity list, command decision and run change file diff, and their snake_case answers.
const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())
const patchRequest = vi.hoisted(() => vi.fn())
const extractApiErrorMessage = vi.hoisted(() => vi.fn((_error: unknown, fallback: string) => fallback))

vi.mock('@shared/api', () => ({ getRequest, postRequest, patchRequest, extractApiErrorMessage }))

import { decideCommand, fetchActivities, fetchRunFileDiff, toCommand, toRunChange } from '@/features/conversation/api'

const CHAT = 'flowgate.v02.0032.0011-CH'

const RAW_COMMAND = {
  request_id: 'req-1',
  ai_run_id: 'run-1',
  doc_id: CHAT,
  program: 'npm',
  args: ['run', 'build'],
  command: 'npm run build',
  cwd: 'client_gui',
  status: 'pending_approval',
  decision_source: null,
  rejected_by: null,
  exit_code: null,
  timed_out: false,
  duration_ms: null,
  error_code: null,
  created_at: '2026-10-06T10:00:00Z',
  anchor_seq: 4,
  anchor_position: 'before',
  anchor_state: 'reply',
}

const RAW_CHANGE = {
  run_id: 'run-1',
  doc_id: CHAT,
  files_changed: 2,
  insertions: 12,
  deletions: 3,
  files: [
    { path: 'client_gui/src/a.ts', status: 'M', insertions: 12, deletions: 3 },
    { path: 'assets/logo.png', status: 'A', insertions: null, deletions: null },
  ],
  start_tree: 'aaa',
  end_tree: 'bbb',
  run_finished_at: '2026-10-06T10:05:00Z',
  created_at: '2026-10-06T10:05:01Z',
  anchor_seq: 5,
  anchor_position: 'after',
  anchor_state: 'reply',
}

beforeEach(() => {
  getRequest.mockReset()
  postRequest.mockReset()
  patchRequest.mockReset()
})

describe('command model', () => {
  it('keeps every server state, the reason code and the anchor', () => {
    for (const status of ['pending_approval', 'approved', 'running', 'succeeded', 'failed', 'timed_out', 'cancelled', 'rejected']) {
      expect(toCommand({ ...RAW_COMMAND, status }).state).toBe(status)
    }
    const cancelled = toCommand({ ...RAW_COMMAND, status: 'cancelled', error_code: 'chat_command_approval_wait_expired' })
    expect(cancelled).toMatchObject({
      kind: 'command',
      id: 'req-1',
      runId: 'run-1',
      command: 'npm run build',
      cwd: 'client_gui',
      state: 'cancelled',
      errorCode: 'chat_command_approval_wait_expired',
      rejectedBy: null,
      anchor: { seq: 4, position: 'before', state: 'reply' },
    })
  })

  it('tells a policy rejection from a user rejection', () => {
    expect(toCommand({ ...RAW_COMMAND, status: 'rejected', rejected_by: 'policy' }).rejectedBy).toBe('policy')
    expect(toCommand({ ...RAW_COMMAND, status: 'rejected', rejected_by: 'user' }).rejectedBy).toBe('user')
    // Only a rejected request has a rejecter.
    expect(toCommand({ ...RAW_COMMAND, status: 'cancelled', rejected_by: 'user' }).rejectedBy).toBeNull()
  })

  it('never turns an unknown status into something still decidable', () => {
    expect(toCommand({ ...RAW_COMMAND, status: 'weird' }).state).toBe('failed')
  })

  it('treats a missing or half anchor as unplaced', () => {
    expect(toCommand({ ...RAW_COMMAND, anchor_seq: null, anchor_position: null, anchor_state: 'unresolved' }).anchor).toEqual({
      seq: null,
      position: null,
      state: 'unresolved',
    })
    expect(toCommand({ ...RAW_COMMAND, anchor_seq: 3, anchor_position: null }).anchor?.seq).toBeNull()
  })

  it('builds the command line from program and args when the server sends none', () => {
    expect(toCommand({ ...RAW_COMMAND, command: null }).command).toBe('npm run build')
  })
})

describe('run change model', () => {
  it('keeps run id, per-file status and nullable numbers, totals and anchor', () => {
    expect(toRunChange(RAW_CHANGE)).toEqual({
      kind: 'change',
      id: 'run-1',
      runId: 'run-1',
      createdAt: '2026-10-06T10:05:01Z',
      files: [
        { path: 'client_gui/src/a.ts', status: 'M', insertions: 12, deletions: 3 },
        { path: 'assets/logo.png', status: 'A', insertions: null, deletions: null },
      ],
      filesChanged: 2,
      insertions: 12,
      deletions: 3,
      anchor: { seq: 5, position: 'after', state: 'reply' },
    })
  })

  it('keeps null totals null', () => {
    const change = toRunChange({ ...RAW_CHANGE, insertions: null, deletions: null })
    expect(change.insertions).toBeNull()
    expect(change.deletions).toBeNull()
  })
})

describe('activity / decision / diff calls', () => {
  it('reads commands and run changes of a CH', async () => {
    getRequest.mockResolvedValue({ data: { ok: true, doc_id: CHAT, commands: [RAW_COMMAND], changes: [RAW_CHANGE] } })
    const list = await fetchActivities(CHAT)
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/chat-activity/${CHAT}`)
    expect(list.map((a) => `${a.kind}:${a.id}`)).toEqual(['command:req-1', 'change:run-1'])
  })

  it('posts the decision value only and returns the updated request', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, request: { ...RAW_COMMAND, status: 'approved', decision_source: 'user' } } })
    const decided = await decideCommand(CHAT, 'req-1', 'approve')
    expect(postRequest).toHaveBeenCalledWith('/api/v1/chat-commands/req-1/decision', { decision: 'approve' })
    expect(decided.state).toBe('approved')
  })

  it('reports a decision refusal with its status and envelope code', async () => {
    postRequest.mockRejectedValue(
      Object.assign(new Error('x'), { response: { status: 409, data: { ok: false, error: { code: 'chat_command_already_decided', message: 'm' } } } }),
    )
    await expect(decideCommand(CHAT, 'req-1', 'reject')).rejects.toMatchObject({ status: 409, code: 'chat_command_already_decided' })
  })

  it('reports a network failure without a status', async () => {
    postRequest.mockRejectedValue(Object.assign(new Error('Network Error'), { isAxiosError: true }))
    await expect(decideCommand(CHAT, 'req-1', 'approve')).rejects.toMatchObject({ status: null })
  })

  it('reads the stored diff of one run change file', async () => {
    getRequest.mockResolvedValue({
      data: {
        ok: true,
        data: {
          run_id: 'run-1',
          path: 'client_gui/src/a.ts',
          status: 'M',
          old: { exists: true, binary: false, truncated: false, size: 4, content: 'a\n' },
          new: { exists: true, binary: false, truncated: false, size: 4, content: 'b\n' },
        },
      },
    })
    const diff = await fetchRunFileDiff(CHAT, 'run-1', 'client_gui/src/a.ts')
    expect(getRequest).toHaveBeenCalledWith(`/api/v1/chat-activity/${CHAT}/runs/run-1/diff`, { path: 'client_gui/src/a.ts' })
    expect(diff).toEqual({
      runId: 'run-1',
      path: 'client_gui/src/a.ts',
      status: 'M',
      old: { exists: true, binary: false, truncated: false, size: 4, content: 'a\n' },
      new: { exists: true, binary: false, truncated: false, size: 4, content: 'b\n' },
    })
  })
})
