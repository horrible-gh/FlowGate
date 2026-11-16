import type { AiNotification } from './notificationApi'

/** Only server-confirmed cancellation is shown as stopped; unknown codes stay in tooltips. */
export function aiNotificationState(item: AiNotification): 'success' | 'failure' | 'stopped' | 'unknown' {
  if (item.succeeded === true) return 'success'
  if (item.stop_code === 'cancelled' || item.end_reason === 'cancelled') return 'stopped'
  return item.succeeded === false ? 'failure' : 'unknown'
}
export function aiNotificationTitle(item: AiNotification): string {
  return item.doc_title?.trim() || item.doc_ref?.split('.').slice(-2).join('.') || item.run_id
}
/** No generated title, unread state, or raw outcome code is presented as prose. */
export function aiNotificationSummary(item: AiNotification): string | null {
  return item.last_message_excerpt?.trim() || null
}
export function relativeNotificationTime(value: string, locale: string, now = Date.now()): string | null {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return null
  const seconds = Math.round((timestamp - now) / 1000)
  const [unit, divisor]: [Intl.RelativeTimeFormatUnit, number] = Math.abs(seconds) < 60 ? ['second', 1]
    : Math.abs(seconds) < 3600 ? ['minute', 60] : Math.abs(seconds) < 86400 ? ['hour', 3600] : ['day', 86400]
  return new Intl.RelativeTimeFormat(locale, { numeric: 'auto' }).format(Math.round(seconds / divisor), unit)
}
