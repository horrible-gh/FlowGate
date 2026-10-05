// Small display helpers shared by the Environment Settings screens.

/** Two-letter avatar initials ("Jung-Min Shin" → "JS", "admin" → "AD"). */
export function initials(name: string | null | undefined): string {
  const words = (name ?? '').trim().split(/[\s._@-]+/).filter(Boolean)
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase()
  return (words[0] ?? '?').slice(0, 2).toUpperCase()
}

function parse(value: string | null | undefined): Date | null {
  if (!value) return null
  const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(value) ? value : `${value.replace(' ', 'T')}Z`
  const date = new Date(normalized.replace(' ', 'T'))
  return Number.isNaN(date.getTime()) ? null : date
}

/** "방금 전" / "12분 전" / "어제" style relative time; older than a week shows the date. */
export function relativeTime(value: string | null | undefined, locale: string, now: Date = new Date()): string {
  const date = parse(value)
  if (!date) return '—'
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000)
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: 'auto' })
  const abs = Math.abs(seconds)
  if (abs < 60) return rtf.format(0, 'second')
  if (abs < 3600) return rtf.format(Math.round(seconds / 60), 'minute')
  if (abs < 86400) return rtf.format(Math.round(seconds / 3600), 'hour')
  if (abs < 7 * 86400) return rtf.format(Math.round(seconds / 86400), 'day')
  return shortDate(value)
}

/** YYYY-MM-DD of a stored timestamp. */
export function shortDate(value: string | null | undefined): string {
  const date = parse(value)
  if (!date) return '—'
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
}

/** A CSV document (RFC 4180 quoting) for the table export buttons. */
export function toCsv(header: string[], rows: (string | number | null | undefined)[][]): string {
  const cell = (value: string | number | null | undefined) => {
    const text = value == null ? '' : String(value)
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
  }
  return [header, ...rows].map((row) => row.map(cell).join(',')).join('\r\n')
}

export function downloadCsv(filename: string, csv: string): void {
  const blob = new Blob(['﻿', csv], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
  URL.revokeObjectURL(url)
}
