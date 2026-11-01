import { reactive } from 'vue'
import { randomUuid } from '@shared/utils/uuid'
import { applyFirstAiRoleSetup, getMyAiRoles } from '../api'
import { AI_ROLES, type MyAiRoles, type AiRoleValues } from './types'
import { aiRolePayload } from './roleValues'

// flowgate.v02.0113 T#2 rework (0030-M §2): [저장] saves the account's three values and applies them to every AI card of
// the Flow the command was clicked in, in one server request (POST /flows/{flowId}/ai-roles/first-setup). Only its
// success closes the modeless and lets the waiting [다음 단계] / [전체 실행] go on once; a refusal (a card running) or a
// failure keeps it open with the error and runs nothing. One request id per prompt and values: a retry of the same
// values after an unknown outcome is answered by the server once, never applied twice.
export function createFirstRunAiSetup(deps: {
  context: () => string | null
  get?: typeof getMyAiRoles
  apply?: typeof applyFirstAiRoleSetup
  newRequestId?: () => string
}) {
  const state = reactive<{ data: MyAiRoles | null; saving: boolean; error: string | null }>({ data: null, saving: false, error: null })
  let epoch = 0
  let pending: { token: number; context: string; flowId: string; resolve: (ready: boolean) => void } | null = null
  let attempt: { token: number; key: string; requestId: string } | null = null
  let querying = false
  let authorized: { context: string; token: number } | null = null
  function cancel() {
    ++epoch; querying = false; authorized = null; attempt = null
    pending?.resolve(false); pending = null
    state.data = null; state.error = null; state.saving = false
  }
  async function request(flowId: string): Promise<boolean> {
    if (pending || querying) return false
    const context = deps.context()
    if (context === null) return false
    const token = ++epoch; querying = true
    try {
      const answer = await (deps.get ?? getMyAiRoles)()
      if (epoch !== token || deps.context() !== context) return false
      // Only these two server values decide whether to ask for setup.
      if (answer.setupCompleted || answer.selectableCount === 0) { authorized = { context, token }; return true }
      state.data = answer; state.error = null
      return await new Promise<boolean>(resolve => { pending = { token, context, flowId, resolve } })
    } finally { if (epoch === token) querying = false }
  }
  function requestIdFor(token: number, values: AiRoleValues): string {
    const key = JSON.stringify(AI_ROLES.map(role => aiRolePayload(values[role])))
    if (!attempt || attempt.token !== token || attempt.key !== key) attempt = { token, key, requestId: (deps.newRequestId ?? randomUuid)() }
    return attempt.requestId
  }
  async function save(values: AiRoleValues) {
    const waiting = pending
    if (!waiting || state.saving || !AI_ROLES.every(role => values[role] != null)) return
    if (waiting.context !== deps.context()) { cancel(); return }
    state.saving = true; state.error = null
    try {
      const applied = await (deps.apply ?? applyFirstAiRoleSetup)(waiting.flowId, requestIdFor(waiting.token, values), values)
      if (epoch !== waiting.token || waiting.context !== deps.context()) return
      const answer = applied.account ?? await (deps.get ?? getMyAiRoles)()
      if (epoch !== waiting.token || waiting.context !== deps.context()) return
      state.data = answer
      if (answer.setupCompleted) {
        authorized = { context: waiting.context, token: waiting.token }
        pending = null; attempt = null; state.data = null
        waiting.resolve(true)
      }
    } catch (error) {
      if (epoch === waiting.token) state.error = error instanceof Error ? error.message : String(error)
    } finally { if (epoch === waiting.token) state.saving = false }
  }
  return { state, request, save, cancel, current: () => authorized !== null && authorized.token === epoch && authorized.context === deps.context() }
}
