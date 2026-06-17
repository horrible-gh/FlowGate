import { useI18n } from 'vue-i18n'
import type { DashboardActivity } from '../stores/dashboard'

// Presentation helpers for inflow-activity rows, shared by the dashboard recent-activity card
// (MainPanel) and the 🔔 notification center (NotificationCenter) so both render the same dot colour,
// localized action label, and relative time (R0001 group 0045 / NR0003 option A).
const ACTIVITY_COLORS: Record<string, string> = {
  document_created: '#2563eb',
  document_edited: '#d97706',
  document_state_changed: '#64748b',
  workflow_state_changed: '#7c3aed',
  question_answered: '#0891b2',
  group_approved: '#16a34a',
}

export function useActivityFormat() {
  const { t, locale } = useI18n()

  function activityColor(activityType: string): string {
    return ACTIVITY_COLORS[activityType] ?? '#94a3b8'
  }

  function activityActionLabel(item: DashboardActivity): string {
    return t(`main.overview.activity_action_${item.activity_type}`)
  }

  function formatDashboardTime(value: string): string {
    const timestamp = Date.parse(value)
    if (!Number.isFinite(timestamp)) return value
    const deltaSeconds = Math.round((timestamp - Date.now()) / 1000)
    const relative = new Intl.RelativeTimeFormat(locale.value, { numeric: 'auto' })
    if (Math.abs(deltaSeconds) < 60) return relative.format(deltaSeconds, 'second')
    const deltaMinutes = Math.round(deltaSeconds / 60)
    if (Math.abs(deltaMinutes) < 60) return relative.format(deltaMinutes, 'minute')
    const deltaHours = Math.round(deltaMinutes / 60)
    if (Math.abs(deltaHours) < 24) return relative.format(deltaHours, 'hour')
    const deltaDays = Math.round(deltaHours / 24)
    if (Math.abs(deltaDays) < 7) return relative.format(deltaDays, 'day')
    return new Intl.DateTimeFormat(locale.value, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(timestamp)
  }

  return { activityColor, activityActionLabel, formatDashboardTime }
}
