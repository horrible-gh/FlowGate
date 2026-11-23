import { isDefaultMessageTitle } from '@/app/i18n/defaultTitles'

/** Display-only comparison: stored card titles may use any supported language. */
export function isDefaultCardTitle(title: string, kindLabelKey: string): boolean {
  return isDefaultMessageTitle(title, kindLabelKey)
}
