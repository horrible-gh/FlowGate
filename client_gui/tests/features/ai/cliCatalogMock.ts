// Test double of the server's CLI model catalog and composer (ai_settings_service
// get_cli_model_catalog / compose_cli_preset / parse_cli_preset, flowgate.v02test.0097 F97-2).
// The connection editor only renders what these return; the strings here mirror the server's
// documented responses so the editor can be exercised without a server.
import { vi } from 'vitest'
import * as api from '@/features/ai/api'
import { AiRequestError } from '@/features/ai/api'
import type { CliModelCatalog, CliModelSelection, CliPresetComposeInput } from '@/features/ai/types'

export const CATALOG: CliModelCatalog = {
  claude: {
    versions: ['5.5'], models: ['opus', 'sonnet', 'haiku'], efforts: ['low', 'medium', 'high', 'xhigh'],
    defaults: { version: '5.5', model: 'opus', effort: 'high', executable: 'claude' },
  },
  codex: {
    versions: ['6.2', '6.1'], models: ['luna', 'terra', 'sol', 'astra'], efforts: ['low', 'medium', 'high', 'xhigh'],
    defaults: { version: '6.2', model: 'sol', effort: 'high', executable: 'codex' },
  },
  gemini: {
    versions: ['3.8'], models: ['flash', 'pro'], efforts: ['low', 'medium', 'high'],
    defaults: { version: '3.8', model: 'flash', effort: 'medium', executable: 'agy' },
  },
}

const title = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

export function fakeCompose(input: CliPresetComposeInput) {
  const entry = CATALOG[input.kind]
  if (!entry) throw new AiRequestError('bad kind', 'validation_failed', [{ field: 'kind', reason: 'unsupported_kind' }])
  const version = input.version ?? entry.defaults.version
  const model = input.model ?? entry.defaults.model
  const effort = input.effort ?? entry.defaults.effort
  const executable = (input.executable ?? '').trim() || entry.defaults.executable
  if (!/^[A-Za-z0-9_./\\][A-Za-z0-9_./\\-]*$/.test(executable)) {
    throw new AiRequestError('bad executable', 'validation_failed', [{ field: 'executable', reason: 'invalid_executable' }])
  }
  const minutes = input.timeout_seconds ? Math.ceil(input.timeout_seconds / 60) : 30
  const cli_command = input.kind === 'claude'
    ? `${executable} --model claude-${model}-${version.replace(/\./g, '-')} --effort ${effort} --dangerously-skip-permissions -p -`
    : input.kind === 'codex'
      ? `${executable} --ask-for-approval never --sandbox danger-full-access exec --skip-git-repo-check --json --model gpt-${version}-${model} -c model_reasoning_effort=${effort} -`
      : `${executable} --output-format stream-json --dangerously-skip-permissions --model gemini-${version}-${model}-${effort} --print-timeout ${minutes}m`
  const name = input.kind === 'claude'
    ? `Claude ${title(model)} ${version}`
    : input.kind === 'codex' ? `Codex GPT-${version} ${title(model)}` : `Gemini ${version} ${title(model)}`
  const selection: CliModelSelection = { kind: input.kind, version, model, effort, executable }
  return { cli_command, name, selection }
}

export function fakeParse(command: string, kind?: string) {
  for (const [k, entry] of Object.entries(CATALOG)) {
    if (kind && kind !== k) continue
    const executable = command.split(' ')[0]
    const minutes = /--print-timeout (\d+)m$/.exec(command)?.[1]
    for (const version of entry.versions) for (const model of entry.models) for (const effort of entry.efforts) {
      try {
        const r = fakeCompose({ kind: k, version, model, effort, executable, timeout_seconds: minutes ? Number(minutes) * 60 : undefined })
        if (r.cli_command === command) return { kind: k, selection: r.selection }
      } catch { /* not this kind */ }
    }
  }
  return { kind: 'custom', selection: null }
}

export function mockCliCatalog() {
  return {
    catalog: vi.spyOn(api, 'fetchCliModelCatalog').mockImplementation(async () => JSON.parse(JSON.stringify(CATALOG)) as CliModelCatalog),
    compose: vi.spyOn(api, 'composeCliPreset').mockImplementation(async (input) => fakeCompose(input)),
    parse: vi.spyOn(api, 'parseCliPreset').mockImplementation(async (command, kind) => fakeParse(command, kind)),
  }
}
