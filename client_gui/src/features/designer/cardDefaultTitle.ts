import { APP_MESSAGES } from '@/app/i18n'

/** Display-only comparison: stored card titles may use any supported language. */
export function isDefaultCardTitle(title: string, kindLabelKey: string): boolean {
  const normalized = title.trim()
  return Object.values(APP_MESSAGES).some((messages) => {
    let label: unknown = { gui: messages }
    for (const part of kindLabelKey.split('.')) {
      if (!label || typeof label !== 'object') return false
      label = (label as Record<string, unknown>)[part]
    }
    return typeof label === 'string' && normalized === label.trim()
  })
}
