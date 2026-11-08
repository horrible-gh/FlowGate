import { flushPromises } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { createHash } from 'node:crypto'
import { ConversationRequestError } from '@/features/conversation/api'
import { CATCHUP_MAX_ROUNDS, FINAL_TURN_READ_ATTEMPTS, RUN_HISTORY_DETAIL_LIMIT, RUN_POLL_MS, createConversationSession, type ConversationSession } from '@/features/conversation/conversationSession'
import type { AiRunState, TurnsPage } from '@/features/conversation/types'
import { createFakeSource, turn } from './fakeConversationSource'

const SCOPE = { projectId: 'p', groupId: 'p.m.0001' }
const A = 'p.m.0001.0002-CH'
const B = 'p.m.0001.0003-CH'
const NORMAL: AiRunState = { runId: 'r1', docRef: A, actionScope: 'chat', groupId: SCOPE.groupId,
  status: 'finished', endReason: 'exited', outcome: 'complete', exitCode: 0 }
const sessions: ConversationSession[] = []
afterEach(() => { for (const s of sessions.splice(0)) s.dispose() })

function clock() {
  const waiting: Array<() => void> = []
  const sleep = vi.fn((_ms: number) => new Promise<void>((resolve) => waiting.push(resolve)))
  return { sleep, async tick() { await flushPromises(); waiting.shift()?.(); await flushPromises() } }
}
function page(turns: TurnsPage['turns'] = [], cursor: number | null = null): TurnsPage {
  return { turns, headSeq: Math.max(0, ...turns.map((t) => t.seq)), nextAfterSeq: cursor, prevBeforeSeq: null,
    participants: [], myReadSeq: null }
}
function codes(session: ConversationSession) {
  return session.state.activities.filter((a) => a.kind === 'status').map((a) => a.code)
}
async function setup(init: Parameters<typeof createFakeSource>[0] = {}, result: AiRunState = NORMAL) {
  const fake = createFakeSource({ chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }],
    turns: { [A]: [turn(1, 'user', 'isolated input')] }, ...init })
  const time = clock()
  fake.source.startChatRun.mockResolvedValue('r1')
  fake.source.fetchRun.mockImplementation(async (id: string) => ({ ...result, runId: id }))
  const session = createConversationSession({ source: fake.source, sleep: time.sleep })
  sessions.push(session)
  await session.setScope(SCOPE)
  await flushPromises()
  return { ...fake, time, session }
}

describe('0147 six required run/body/read conditions through the real session', () => {
  it('1: a saved reply added after start is normal; settings refresh belongs to that run', async () => {
    const { session, source, data, time } = await setup()
    const settingsBefore = source.fetchChatSettings.mock.calls.length
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'saved answer', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(session.state.turns.at(-1)).toMatchObject({ seq: 2, sourceRunId: 'r1', body: 'saved answer' })
    expect(codes(session)).toEqual([])
    expect(session.running.value).toBe(false)
    expect(source.fetchChatSettings).toHaveBeenCalledTimes(settingsBefore + 1)
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it.each(['select', '409', 'lost-start'] as const)('2/adopt: reply precedes %s adoption, zero added turns', async (adopt) => {
    const { session, source, time } = await setup({ activeRun: adopt === 'select' ? 'r1' : null,
      turns: { [A]: [turn(1, 'user', 'input'), turn(2, 'ai', 'already saved', null, { sourceRunId: 'r1' })] } })
    if (adopt === '409') {
      source.startChatRun.mockRejectedValueOnce(Object.assign(new ConversationRequestError('', 409, 'run_in_progress'), { runId: 'r1' }))
      await session.invokeAi()
    } else if (adopt === 'lost-start') {
      // flowgate.v02.0152: the same press is sent again (same client_request_id) and the server answers with the run
      // it already admitted — r1 here — instead of starting another.
      source.startChatRun.mockRejectedValueOnce(new ConversationRequestError('lost', null, null))
      const first = session.invokeAi()
      await flushPromises()
      await session.invokeAi() // lock prevents a second press during recovery
      await time.tick() // the wait before the resend
      await first
      const keys = source.startChatRun.mock.calls.map((call: unknown[]) => call[2])
      expect(keys).toHaveLength(2)
      expect(keys[0]).toEqual(expect.any(String))
      expect(keys[1]).toBe(keys[0])
    }
    const seqs = session.state.turns.map((t) => [t.seq, t.sourceRunId])
    await time.tick()
    expect(codes(session)).toEqual([])
    expect(session.state.turns.map((t) => [t.seq, t.sourceRunId])).toEqual(seqs)
    expect(session.state.runId).toBeNull()
    expect(source.startChatRun).toHaveBeenCalledTimes(adopt === 'select' ? 0 : adopt === 'lost-start' ? 2 : 1)
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it('0084: an initially unregistered 410 is retried within the adopt grace', async () => {
    const { session, source, time } = await setup({ activeRun: 'r1', turns: { [A]: [
      turn(1, 'user', 'input'), turn(2, 'ai', 'saved before registration', null, { sourceRunId: 'r1' }),
    ] } })
    source.fetchRun.mockRejectedValueOnce(new ConversationRequestError('isolated not registered', 410, 'run_not_found'))
    await time.tick()
    expect(session.state.runId).toBe('r1')
    expect(codes(session)).toEqual([])
    await time.tick()
    expect(source.fetchRun).toHaveBeenCalledTimes(2)
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('3: final GET failure exhausts exactly three attempts and later catch-up repairs it', async () => {
    const { session, source, data, time } = await setup()
    const realRead = source.fetchTurns.getMockImplementation()!
    source.fetchTurns.mockClear().mockRejectedValue(new Error('isolated GET failure'))
    await session.invokeAi()
    await time.tick(); await time.tick(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(FINAL_TURN_READ_ATTEMPTS)
    expect(time.sleep.mock.calls.map((c: number[]) => c[0])).toEqual([RUN_POLL_MS, RUN_POLL_MS, RUN_POLL_MS])
    expect(codes(session)).toEqual(['run_query_unconfirmed'])
    expect(codes(session)).not.toContain('run_no_reply')
    expect(session.running.value).toBe(false)
    data.turns[A].push(turn(2, 'ai', 'late confirmed', null, { sourceRunId: 'r1' }))
    source.fetchTurns.mockImplementation(realRead)
    await session.catchUp()
    expect(codes(session)).toEqual([])
    expect(source.startChatRun).toHaveBeenCalledTimes(1)
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it.each([false, true])('3/recovery: failed final GET then success (saved body=%s)', async (hasBody) => {
    const { session, source, data, time } = await setup()
    const realRead = source.fetchTurns.getMockImplementation()!
    source.fetchTurns.mockClear().mockRejectedValueOnce(new Error('blip')).mockImplementation(realRead)
    if (hasBody) data.turns[A].push(turn(2, 'ai', 'answer', null, { sourceRunId: 'r1' }))
    await session.invokeAi()
    await time.tick(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(2)
    expect(codes(session)).toEqual(hasBody ? [] : ['run_no_reply'])
    expect(session.running.value).toBe(false)
  })

  it.each(['none', 'complete'])('4: partial reply and timeout remain together even with outcome=%s', async (outcome) => {
    const { session, data, time } = await setup({}, { ...NORMAL, endReason: 'timeout', outcome, exitCode: null })
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'partial reply', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(session.state.turns.at(-1)?.body).toBe('partial reply')
    expect(codes(session)).toEqual(['run_failed'])
    expect(session.state.activities.find((a) => a.kind === 'status')?.text).toContain('timeout')
    expect(codes(session)).not.toContain('run_no_reply')
  })

  it.each([
    ['complete', 0, 'run_no_reply'], ['none', 0, 'run_no_reply'], ['none', 7, 'run_no_reply'],
    ['future', 0, 'run_result_unconfirmed'], [null, 0, 'run_result_unconfirmed'], ['complete', null, 'run_result_unconfirmed'],
  ])('5: zero body, exited/%s/%s => %s', async (outcome, exitCode, expected) => {
    const { session, time } = await setup({}, { ...NORMAL, outcome: outcome as string | null, exitCode: exitCode as number | null })
    await session.invokeAi(); await time.tick()
    expect(codes(session)).toEqual([expected])
    expect(session.state.activities.find((a) => a.kind === 'status')?.runResult?.exitCode).toBe(exitCode)
  })

  it.each([false, true])('6: cancelled keeps its branch and partial body=%s', async (hasBody) => {
    const { session, source, time } = await setup({ activeRun: 'r1', turns: { [A]: [turn(1, 'user', 'input'),
      ...(hasBody ? [turn(2, 'ai', 'partial', null, { sourceRunId: 'r1' })] : [])] } },
    { ...NORMAL, endReason: 'cancelled', outcome: 'none', exitCode: null })
    await session.invokeAi()
    source.fetchTurns.mockRejectedValue(new Error('read unavailable'))
    // Already fetched body remains visible even when all three final reads fail.
    await time.tick(); await time.tick(); await time.tick()
    expect(codes(session)).toEqual(['run_cancelled'])
    if (hasBody) expect(session.state.turns.at(-1)?.body).toBe('partial')
    expect(session.running.value).toBe(false)
  })
})

describe('0147 attribution, finite reads, and stale response disposal', () => {
  it('a listed live run finishing its history read before active adoption does not leave a false history warning', async () => {
    const fake = createFakeSource({ chats: [{ id: A, title: 'A', updatedAt: null }],
      turns: { [A]: [turn(1, 'user', 'input'), turn(2, 'ai', 'saved reply', null, { sourceRunId: 'r1' })] },
      runDetails: { r1: { ...NORMAL, status: 'running' } } })
    const time = clock()
    let answer!: (id: string | null) => void
    fake.source.fetchActiveChatRun.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    fake.source.fetchRun.mockResolvedValueOnce({ ...NORMAL, status: 'running', docRef: null, outcome: null, exitCode: null, endReason: null })
      .mockResolvedValue(NORMAL)
    const session = createConversationSession({ source: fake.source, sleep: time.sleep })
    sessions.push(session)
    await session.setScope(SCOPE); await flushPromises()
    expect(fake.source.fetchRun).toHaveBeenCalledWith('r1')
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    answer('r1'); await flushPromises(); await time.tick()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(fake.source.startChatRun).not.toHaveBeenCalled()
  })

  it('tracks the real running wire via exact active CH membership, then finished detail', async () => {
    const { session, source, time } = await setup({ activeRun: 'r1', turns: { [A]: [
      turn(1, 'user', 'input'), turn(2, 'ai', 'saved before adoption', null, { sourceRunId: 'r1' }),
    ] } })
    const running = { ...NORMAL, status: 'running', docRef: null, outcome: null, exitCode: null, endReason: null }
    source.fetchRun.mockResolvedValueOnce(running).mockResolvedValueOnce(running)
    await time.tick(); await time.tick()
    expect(session.state.runId).toBe('r1')
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(2) // open + one authoritative proof
    expect(codes(session)).toEqual([])
    await time.tick()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it.each(['ended-between-reads', 'network', 'different-active'] as const)('unproven live membership %s waits for authoritative finished detail', async (race) => {
    const { session, source, time } = await setup({ activeRun: 'r1', turns: { [A]: [
      turn(1, 'user', 'input'), turn(2, 'ai', 'saved reply', null, { sourceRunId: 'r1' }),
    ] } })
    source.fetchTurns.mockClear()
    source.fetchRun.mockResolvedValueOnce({ ...NORMAL, status: 'running', docRef: null, outcome: null, exitCode: null, endReason: null })
    if (race === 'network') source.fetchActiveChatRun.mockRejectedValueOnce(new Error('isolated active query failed'))
    else source.fetchActiveChatRun.mockResolvedValueOnce(race === 'different-active' ? 'another-run' : null)
    await time.tick()
    expect(session.state.runId).toBe('r1')
    expect(source.fetchTurns).not.toHaveBeenCalled()
    expect(codes(session)).toEqual([])
    await time.tick()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })


  it('other-run growth / blank own turns cannot deliver this run or consume edit-once settings', async () => {
    const { session, source, data, time } = await setup()
    const n = source.fetchChatSettings.mock.calls.length
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'other answer', null, { sourceRunId: 'other' }), turn(3, 'ai', ' \n ', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(codes(session)).toEqual(['run_no_reply'])
    expect(source.fetchChatSettings).toHaveBeenCalledTimes(n)
  })

  it('unattributed legacy AI text is preserved with attribution unconfirmed', async () => {
    const { session, data, time } = await setup()
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'legacy reply'))
    await time.tick()
    expect(codes(session)).toEqual(['run_attribution_unconfirmed'])
    expect(session.state.turns.at(-1)?.body).toBe('legacy reply')
  })

  it.each([
    ['complete', 2, 'run_failed'], ['none', 0, 'run_result_unconfirmed'], ['partial', 0, 'run_result_unconfirmed'], ['future', 0, 'run_result_unconfirmed'],
  ])('body does not mask exited/%s/%s', async (outcome, exitCode, expected) => {
    const { session, data, time } = await setup({}, { ...NORMAL, outcome: String(outcome), exitCode: Number(exitCode) })
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'body', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(codes(session)).toEqual([expected])
    expect(session.state.turns.at(-1)?.body).toBe('body')
  })

  it('reads all pages from zero and finds multiple own turns before the saved read position', async () => {
    const { session, source, time } = await setup()
    source.fetchTurns.mockClear().mockImplementation(async (_id: string, q: { afterSeq?: number }) => q.afterSeq === 0
      ? page([turn(1, 'user', 'input'), turn(2, 'ai', 'part one', null, { sourceRunId: 'r1' })], 2)
      : q.afterSeq === 2 ? page([turn(3, 'ai', 'part two', null, { sourceRunId: 'r1' })], 3) : page())
    await session.invokeAi(); await time.tick()
    expect(source.fetchTurns.mock.calls.map((c: any[]) => c[1].afterSeq)).toEqual([0, 2, 3])
    expect(session.state.turns.map((t) => t.seq)).toEqual([1, 2, 3])
    expect(codes(session)).toEqual([])
  })

  it('a known reply in a long chat needs only the final tail, not unrelated old pages', async () => {
    const { session, source, time } = await setup({ activeRun: 'r1', turns: { [A]: [
      turn(1200, 'ai', 'already stored', null, { sourceRunId: 'r1' }), turn(1201, 'user', 'later input'),
    ] } })
    source.fetchTurns.mockClear().mockImplementation(async (_id: string, q: { afterSeq: number }) => {
      expect(q.afterSeq).toBe(1201)
      return page()
    })
    await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(1)
    expect(codes(session)).toEqual([])
    expect(session.state.turns[0].sourceRunId).toBe('r1')
  })

  it('a retained cursor at the cap is unconfirmed, never an absent reply', async () => {
    const { session, source, time } = await setup()
    source.fetchTurns.mockClear().mockImplementation(async (_id: string, q: { afterSeq: number }) => page([], q.afterSeq + 1))
    await session.invokeAi(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(CATCHUP_MAX_ROUNDS)
    expect(codes(session)).toEqual(['run_query_unconfirmed'])
    expect(session.running.value).toBe(false)
  })

  it('a nonadvancing cursor terminates without retrying forever', async () => {
    const { session, source, time } = await setup()
    source.fetchTurns.mockClear().mockResolvedValue(page([], 0))
    await session.invokeAi(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(1)
    expect(codes(session)).toEqual(['run_query_unconfirmed'])
  })

  it.each(['chat', 'group', 'dispose'] as const)('discards final read retries after %s transition', async (change) => {
    const { session, source, time } = await setup()
    source.fetchTurns.mockClear().mockRejectedValueOnce(new Error('blip'))
    await session.invokeAi(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(1)
    if (change === 'chat') await session.selectChat(B)
    else if (change === 'group') await session.setScope({ projectId: 'q', groupId: 'q.m.0001' })
    else session.dispose()
    const calls = source.fetchTurns.mock.calls.length
    await time.tick(); await time.tick()
    expect(source.fetchTurns).toHaveBeenCalledTimes(calls)
    expect(codes(session)).not.toContain('run_no_reply')
    expect(source.postTurn).not.toHaveBeenCalled()
  })

  it('a delayed run response for another chat never applies a body, status or settings', async () => {
    const { session, source, time } = await setup()
    let answer!: (r: AiRunState) => void
    source.fetchRun.mockImplementationOnce(() => new Promise<AiRunState>((resolve) => { answer = resolve }))
    await session.invokeAi(); await time.tick()
    await session.selectChat(B); await flushPromises()
    const settings = source.fetchChatSettings.mock.calls.length
    answer({ ...NORMAL, endReason: 'timeout' }); await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.turns).toEqual([])
    expect(codes(session)).toEqual([])
    expect(source.fetchChatSettings).toHaveBeenCalledTimes(settings)
  })

  it.each([{ docRef: B }, { docRef: null }, { runId: 'wrong-id' }, { actionScope: 'new' }, { groupId: 'q.m.0001' }])('rejects mismatched run detail %j', async (wrong) => {
    const { session, source, time } = await setup()
    source.fetchRun.mockResolvedValue({ ...NORMAL, ...wrong, endReason: 'timeout' })
    source.fetchTurns.mockClear()
    await session.invokeAi(); await time.tick()
    expect(codes(session)).toEqual(['run_result_unconfirmed'])
    expect(source.fetchTurns).not.toHaveBeenCalled()
    expect(session.running.value).toBe(false)
  })

  it('settings re-read for an old confirmed reply cannot change the newly selected chat', async () => {
    const { session, source, data, time } = await setup()
    let answer!: (settings: any) => void
    source.fetchChatSettings.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'confirmed reply', null, { sourceRunId: 'r1' }))
    await time.tick()
    await session.selectChat(B); await flushPromises()
    expect(session.state.settings.sourceAccessMode).toBe('read_only')
    answer({ ...data.settings, sourceAccessScope: SCOPE, sourceAccessMode: 'edit' })
    await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.settings.sourceAccessMode).toBe('read_only')
  })

  it('a final read that resolves after a chat change is dropped before merge', async () => {
    const { session, source, time } = await setup()
    let answer!: (p: TurnsPage) => void
    source.fetchTurns.mockImplementationOnce(() => new Promise<TurnsPage>((resolve) => { answer = resolve }))
    await session.invokeAi(); await time.tick()
    await session.selectChat(B); await flushPromises()
    answer(page([turn(2, 'ai', 'old chat body', null, { sourceRunId: 'r1' })])); await flushPromises()
    expect(session.state.activeChatId).toBe(B)
    expect(session.state.turns).toEqual([])
    expect(codes(session)).toEqual([])
  })
})

describe('0147 fresh-session restoration from stored run history, no active run', () => {
  it('a run adopted after F5 is still tracked through running to finished without another start', async () => {
    const { session, source, data, time } = await setup({ activeRun: 'r1' })
    source.fetchRun.mockResolvedValueOnce({ ...NORMAL, status: 'running', outcome: null, exitCode: null })
    await time.tick()
    expect(session.state.runId).toBe('r1')
    data.turns[A].push(turn(2, 'ai', 'saved before exit', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(session.state.runId).toBeNull()
    expect(codes(session)).toEqual([])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('restoring older completed runs never overwrites the current active run lock', async () => {
    const { session, source } = await setup({ activeRun: 'live', runDetails: { old: { ...NORMAL, runId: 'old' } } })
    expect(session.state.runId).toBe('live')
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(source.fetchRun).toHaveBeenCalledWith('old')
    expect(source.fetchRun).not.toHaveBeenCalledWith('live')
  })

  it('remote failure diagnostics survive a partial reply without a no-reply label', async () => {
    const failure = { cause: 'workdir_missing', workdir: '/isolated/job', message: 'isolated missing path', detail: 'isolated failed connection' }
    const { session, data, time } = await setup({}, { ...NORMAL, endReason: 'all_providers_failed', outcome: 'none', exitCode: null, failure })
    await session.invokeAi()
    data.turns[A].push(turn(2, 'ai', 'partial reply', null, { sourceRunId: 'r1' }))
    await time.tick()
    expect(codes(session)).toEqual(['run_failed', 'remote_workdir_missing'])
    expect(session.state.activities.find((a) => a.kind === 'status' && a.code === 'remote_workdir_missing')?.text).toContain('/isolated/job')
    expect(session.state.turns.at(-1)?.body).toBe('partial reply')
  })

  it('a failed history detail lookup remains uncertain, then repairs on catch-up', async () => {
    const { session, source } = await setup()
    source.listRuns.mockResolvedValue({ items: [{ runId: 'r1', docRef: A }], total: 1, hasMore: false })
    source.fetchRun.mockRejectedValueOnce(new ConversationRequestError('isolated forbidden', 403, 'permission_denied'))
    await session.selectChat(A)
    expect(codes(session)).toEqual(['run_history_unconfirmed'])
    await session.catchUp()
    expect(codes(session)).toEqual(['run_no_reply'])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it.each([
    ['partial-timeout', 'timeout', 'none', null, 'partial reply', 'run_failed'],
    ['empty-failure', 'all_providers_failed', 'none', null, '', 'run_no_reply'],
    ['cancelled', 'cancelled', 'none', null, 'partial reply', 'run_cancelled'],
    ['normal', 'exited', 'complete', 0, 'normal reply', null],
  ])('%s survives F5, CH and group roundtrips with unchanged storage', async (_name, endReason, outcome, exitCode, body, expected) => {
    const savedTurns = [turn(1, 'user', 'isolated input'), ...(body ? [turn(2, 'ai', String(body), null, { sourceRunId: 'r1' })] : [])]
    const init = { turns: { [A]: savedTurns }, runDetails: { r1: { ...NORMAL, actionScope: null, endReason: String(endReason), outcome: String(outcome), exitCode: exitCode as number | null } },
      activities: { [A]: [{ kind: 'command' as const, id: 'stored-cmd', runId: 'r1', command: 'isolated fixture', createdAt: null, state: 'succeeded' as const,
        exitCode: 0, anchor: { seq: 1, position: 'after' as const, state: 'reply' as const } }] } }
    const hash = () => createHash('sha256').update(JSON.stringify(init)).digest('hex')
    const before = hash()
    const fake = createFakeSource({ chats: [{ id: A, title: 'A', updatedAt: null }, { id: B, title: 'B', updatedAt: null }], ...init })
    fake.source.listChats.mockImplementation(async (scope: { groupId: string }) =>
      fake.data.chats.filter((chat) => chat.id.startsWith(scope.groupId + '.')))
    for (let reload = 0; reload < 2; reload++) {
      const session = createConversationSession({ source: fake.source })
      sessions.push(session)
      await session.setScope(SCOPE); await flushPromises()
      expect(codes(session)).toEqual(expected ? [expected] : [])
      expect(session.running.value).toBe(false)
      await session.selectChat(B); await session.selectChat(A); await flushPromises()
      expect(codes(session)).toEqual(expected ? [expected] : [])
      await session.setScope({ projectId: 'q', groupId: 'q.m.0001' }); await flushPromises()
      expect(session.state.activeChatId).toBeNull()
      expect(session.state.turns).toEqual([])
      expect(codes(session)).toEqual([])
      await session.setScope(SCOPE); await flushPromises()
      expect(codes(session)).toEqual(expected ? [expected] : [])
      await session.catchUp()
      expect(codes(session)).toEqual(expected ? [expected] : [])
      expect(new Set(session.state.activities.map((a) => a.id)).size).toBe(session.state.activities.length)
      session.dispose()
    }
    expect(hash()).toBe(before)
    expect(fake.source.startChatRun).not.toHaveBeenCalled()
    expect(fake.source.postTurn).not.toHaveBeenCalled()
    expect(fake.source.decideCommand).not.toHaveBeenCalled()
    expect(fake.data.activities[A][0]).toEqual(init.activities[A][0])
  })

  it('finds a completed empty failure from the list even with no turn, activity or active run', async () => {
    const { session, source } = await setup({ turns: { [A]: [] }, runDetails: { r1: { ...NORMAL, endReason: 'worker_error', outcome: 'none' } } }, { ...NORMAL, endReason: 'worker_error', outcome: 'none' })
    expect(codes(session)).toEqual(['run_no_reply'])
    expect(source.startChatRun).not.toHaveBeenCalled()
    expect(source.fetchActiveChatRun).toHaveBeenCalledTimes(1)
  })

  it('ignores other CH list items and preserves different runs failures across activity re-reads', async () => {
    const { session, source } = await setup({ runDetails: { r1: { ...NORMAL }, foreign: { ...NORMAL, runId: 'foreign', docRef: B }, r2: { ...NORMAL, runId: 'r2' } } })
    expect(source.fetchRun.mock.calls.map((c: string[]) => c[0])).toEqual(['r1', 'r2'])
    expect(codes(session)).toEqual(['run_no_reply', 'run_no_reply'])
    await session.catchUp(); await session.catchUp()
    expect(codes(session)).toEqual(['run_no_reply', 'run_no_reply'])
    expect(new Set(session.state.activities.map((a) => a.id)).size).toBe(2)
  })

  it.each(['403', 'network', 'truncated'] as const)('history %s is unconfirmed and repairs on same-CH requery', async (failure) => {
    const { session, source } = await setup()
    if (failure === 'truncated') source.listRuns.mockResolvedValue({ items: [], total: 300, hasMore: true })
    else source.listRuns.mockRejectedValue(new ConversationRequestError('isolated failure', failure === '403' ? 403 : null, null))
    await session.selectChat(B); await session.selectChat(A); await flushPromises()
    expect(codes(session)).toEqual(['run_history_unconfirmed'])
    if (failure === 'truncated') expect(source.listRuns.mock.calls.slice(-2).map((c: any[]) => c[1])).toEqual([20, 100])
    source.listRuns.mockResolvedValue({ items: [{ runId: 'r1', docRef: A }], total: 1, hasMore: false })
    await session.selectChat(A)
    expect(codes(session)).toEqual(['run_no_reply'])
    expect(source.startChatRun).not.toHaveBeenCalled()
  })

  it('history details are capped, and a delayed list for the old CH is discarded', async () => {
    const { session, source } = await setup()
    source.listRuns.mockResolvedValue({ items: Array.from({ length: 100 }, (_, i) => ({ runId: `history-${i}`, docRef: A })), total: 100, hasMore: false })
    source.fetchRun.mockClear()
    await session.selectChat(A)
    expect(source.fetchRun).toHaveBeenCalledTimes(RUN_HISTORY_DETAIL_LIMIT)
    expect(codes(session)).toContain('run_history_unconfirmed')
    let answer!: (value: any) => void
    source.listRuns.mockImplementationOnce(() => new Promise((resolve) => { answer = resolve }))
    const old = session.catchUp(); await flushPromises()
    await session.selectChat(B); await flushPromises()
    answer({ items: [{ runId: 'r1', docRef: A }], total: 1, hasMore: false }); await old
    expect(session.state.activeChatId).toBe(B)
    expect(codes(session)).toEqual([])
  })
})
