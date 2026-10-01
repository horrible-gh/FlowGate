// Avatar/kind/execution-mode display helpers shared by the AI 관리 list cards and dialogs.

import type { ExecLocation, ProviderExecType, ProviderItem } from '../types'

// The 2x2 execution mode: where (local/remote) x how (CLI/API). Only CLI runs today
// (flowgate.v02.0017 CH0006): the API cells are shown but cannot be picked.
export const EXEC_LOCATIONS: ExecLocation[] = ['local', 'remote']
export const EXEC_TYPES: ProviderExecType[] = ['cli', 'api']

export function isExecTypeSupported(execType: ProviderExecType): boolean {
  return execType === 'cli'
}

export function modeKey(item: Pick<ProviderItem, 'exec_type' | 'exec_location'>): string {
  return `${item.exec_location ?? 'local'}_${item.exec_type}`
}

export function isModeSupported(item: Pick<ProviderItem, 'exec_type'>): boolean {
  return isExecTypeSupported(item.exec_type)
}

export function getInitials(name: string): string {
  if (!name) return 'AI'
  const parts = name.trim().split(/\s+/)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

export function getAvatarClass(name: string, kind?: string): string {
  const n = (name + ' ' + (kind || '')).toLowerCase()
  if (n.includes('sonnet') || n.includes('claude')) return 'av-cs'
  if (n.includes('opus')) return 'av-co'
  if (n.includes('haiku')) return 'av-ch'
  if (n.includes('gpt') || n.includes('openai')) return 'av-og'
  if (n.includes('gemini')) return 'av-gp'
  if (n.includes('llama')) return 'av-l3'
  if (n.includes('terra')) return 'av-te'
  if (n.includes('luna')) return 'av-lu'
  return 'av-cs'
}

export function getKindToneClass(kind: string): string {
  const k = (kind || '').toLowerCase()
  if (k.includes('claude') || k.includes('anthropic')) return 'indigo'
  if (k.includes('openai') || k.includes('gpt')) return 'cyan'
  if (k.includes('custom')) return 'purple'
  return ''
}
