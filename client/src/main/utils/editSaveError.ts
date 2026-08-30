const DEFAULT_MAX_LENGTH = 160

export function summarizeEditSaveError(detail: unknown, maxLength = DEFAULT_MAX_LENGTH): string {
  const firstLine = String(detail ?? '').split(/\r?\n/, 1)[0].trim()
  if (firstLine.length <= maxLength) return firstLine
  return `${firstLine.slice(0, Math.max(0, maxLength - 1)).trimEnd()}…`
}
