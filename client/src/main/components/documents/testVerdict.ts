// Shared labels/tones for the specification TS and the TSR test report (flowgate.default.0549
// T0008). Verdict/category/mode VALUES are grammar tokens from the server; only their display
// text is localized here.
import { useI18n } from 'vue-i18n'

export const TEST_VERDICTS = ['PASS', 'FAIL', 'BLOCKED', 'NOT_RUN'] as const
export const TEST_CATEGORIES = ['normal', 'negative', 'boundary', 'regression'] as const
export const TEST_EXECUTION_MODES = ['automated', 'manual', 'external'] as const

const VERDICT_BADGE: Record<string, string> = {
  PASS: 'badge-green',
  FAIL: 'badge-red',
  BLOCKED: 'badge-yellow',
  NOT_RUN: 'badge-gray',
}

export function useTestVerdictLabels() {
  const { t } = useI18n()

  function verdictLabel(value: string | null | undefined): string {
    const key = (value ?? 'NOT_RUN').toUpperCase()
    return (TEST_VERDICTS as readonly string[]).includes(key)
      ? t(`main.test_document.verdict.${key}`)
      : String(value ?? '')
  }

  function verdictBadge(value: string | null | undefined): string {
    return VERDICT_BADGE[(value ?? 'NOT_RUN').toUpperCase()] ?? 'badge-gray'
  }

  function categoryLabel(value: string | null | undefined): string {
    return value && (TEST_CATEGORIES as readonly string[]).includes(value)
      ? t(`main.test_document.category.${value}`)
      : String(value ?? '-')
  }

  function modeLabel(value: string | null | undefined): string {
    return value && (TEST_EXECUTION_MODES as readonly string[]).includes(value)
      ? t(`main.test_document.mode.${value}`)
      : String(value ?? '-')
  }

  return { verdictLabel, verdictBadge, categoryLabel, modeLabel }
}
