import { reactive } from 'vue'
import { getMyAiRoles, putMyAiRoles } from '../api'
import { AI_ROLES, type MyAiRoles, type AiRoleValues } from './types'

export function createFirstRunAiSetup(deps: {
  context: () => string | null
  get?: typeof getMyAiRoles
  put?: typeof putMyAiRoles
}) {
  const state = reactive<{ data: MyAiRoles | null; saving: boolean; error: string | null }>({ data: null, saving: false, error: null })
  let epoch = 0
  let pending: { token: number; context: string; resolve: (ready: boolean) => void } | null = null
  let querying = false
  let authorized: { context: string; token: number } | null = null
  function cancel() {
    ++epoch; querying = false; authorized = null
    pending?.resolve(false); pending = null
    state.data = null; state.error = null; state.saving = false
  }
  async function request(): Promise<boolean> {
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
      return await new Promise<boolean>(resolve => { pending = { token, context, resolve } })
    } finally { if (epoch === token) querying = false }
  }
  async function save(values: AiRoleValues) {
    const waiting = pending
    if (!waiting || state.saving || !AI_ROLES.every(role => values[role] != null)) return
    if (waiting.context !== deps.context()) { cancel(); return }
    state.saving = true; state.error = null
    try {
      await (deps.put ?? putMyAiRoles)(values)
      if (epoch !== waiting.token || waiting.context !== deps.context()) return
      const answer = await (deps.get ?? getMyAiRoles)()
      if (epoch !== waiting.token || waiting.context !== deps.context()) return
      state.data = answer
      if (answer.setupCompleted) {
        authorized = { context: waiting.context, token: waiting.token }
        pending = null; state.data = null
        waiting.resolve(true)
      }
    } catch (error) {
      if (epoch === waiting.token) state.error = error instanceof Error ? error.message : String(error)
    } finally { if (epoch === waiting.token) state.saving = false }
  }
  return { state, request, save, cancel, current: () => authorized !== null && authorized.token === epoch && authorized.context === deps.context() }
}
