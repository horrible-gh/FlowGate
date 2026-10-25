import type { ProviderItem } from '../types'

export const FIELD_MAX: Record<string, number> = {
  name: 100, cli_command: 4000, api_base_url: 500, api_model: 200, api_key: 1000,
}
export const KINDS = {
  cli: ['claude', 'copilot', 'codex', 'custom'],
  api: ['claude', 'openai', 'custom'],
}
export interface ProviderFieldIssue { field: string; reason: string }

// Match server validation, including Python's Unicode code-point length.
export function validateProvider(p: Pick<ProviderItem, 'name' | 'kind' | 'exec_type'> & Partial<ProviderItem>): ProviderFieldIssue[] {
  const errors: ProviderFieldIssue[] = []
  const check = (field: string, value: string | null | undefined, required?: string, trim = false) => {
    const text = trim ? (value ?? '').trim() : value ?? ''
    if (required && !text.trim()) errors.push({ field, reason: required })
    else if (Array.from(text).length > FIELD_MAX[field]) errors.push({ field, reason: 'too_long' })
  }
  check('name', p.name, 'required', true)
  if (!KINDS[p.exec_type]?.includes(p.kind)) errors.push({ field: 'kind', reason: 'unknown_kind' })
  if (p.exec_type === 'cli') check('cli_command', p.cli_command, 'required_for_cli')
  else {
    check('api_model', p.api_model, 'required_for_api')
    check('api_base_url', p.api_base_url)
    check('api_key', p.api_key)
  }
  return errors
}
