// The finished-card retention setting, in one place (group 0452, L0003 §1).
//
// Two documents read this value — the header monitor in main.html and the account screen
// in settings.html — and they must not each carry their own copy of the nine choices, of
// the default, or of the rule that turns a stored number into a TTL. L0003 §2-1 is blunt
// about why: two interpretations means the value on the screen and the value that decides
// when a card disappears drift apart, and nothing reports it.
//
// The trap this file exists to close: `-1` is a full member of the domain ("never
// expires"), not a lower bound. Anything shaped like `if (value < 0) value = DEFAULT`
// silently throws away the choice the user made. Normalization is a MEMBERSHIP test.

/** The stored column name, the API field name and this module's key are one string. */
export const RETENTION_FIELD = 'ai_finished_card_retention_minutes'

/** The closed list, in the order the account screen draws it (L0003 §1-1). */
export const RETENTION_DOMAIN_MINUTES = [-1, 0, 30, 60, 120, 180, 360, 720, 1440] as const

/** Somebody who has never saved gets today's behaviour: 30 minutes. */
export const RETENTION_DEFAULT_MINUTES = 30
/** Sentinel: no expiry by time at all. */
export const RETENTION_NEVER = -1
/** Sentinel: never make a finished card in the first place. */
export const RETENTION_IMMEDIATE = 0

export const MINUTE_MS = 60_000

/** localStorage, not session: the setting belongs to the person, not to one tab. */
export const RETENTION_MIRROR_KEY = 'fg.ai_invoke.retention_minutes'

/** The one address both surfaces call. */
export const UI_SETTINGS_PATH = '/api/v1/me/ui-settings'

export interface UiSettingsResponse {
  ok?: boolean
  settings?: Record<string, unknown>
  is_default?: boolean
  defaults?: Record<string, unknown>
  domain?: Record<string, unknown>
}

function isDomainMember(value: number): boolean {
  return (RETENTION_DOMAIN_MINUTES as readonly number[]).includes(value)
}

/**
 * Repair anything on the way in (L0003 §2-1).
 *
 * Membership, not a clamp — see the file header. `true` is deliberately refused before
 * the membership test, because JavaScript would otherwise compare it equal to 1.
 */
export function normalizeRetentionMinutes(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) return RETENTION_DEFAULT_MINUTES
  return isDomainMember(value) ? value : RETENTION_DEFAULT_MINUTES
}

/**
 * Same rule, for values that arrive as text (localStorage always does).
 *
 * A non-numeric string is not "0": `Number('')` is 0 and `Number(' ')` is 0, and 0 is a
 * real choice that empties the list, so the text has to be checked before it is converted.
 */
export function parseRetentionMinutes(value: unknown): number {
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!/^-?\d+$/.test(trimmed)) return RETENTION_DEFAULT_MINUTES
    return normalizeRetentionMinutes(Number(trimmed))
  }
  return normalizeRetentionMinutes(value)
}

/**
 * Minutes to milliseconds (L0003 §2-2).
 *
 * `RETENTION_NEVER` becomes Infinity here and never reaches arithmetic as `-1`, where it
 * would turn "30 minutes ago" into "30 minutes from now".
 */
export function retentionMs(minutes: number): number {
  if (minutes === RETENTION_NEVER) return Number.POSITIVE_INFINITY
  return minutes * MINUTE_MS
}

/**
 * The last value this browser saw, or null when there is none we can trust.
 *
 * Null and "30" are different answers, and the caller decides what to do with null: the
 * session restore fails OPEN on it (L0003 §2-4) while everything else falls back to 30.
 * Collapsing them here would take that choice away.
 */
export function readRetentionMirror(): number | null {
  if (typeof localStorage === 'undefined') return null
  try {
    const raw = localStorage.getItem(RETENTION_MIRROR_KEY)
    if (raw == null) return null
    const trimmed = raw.trim()
    if (!/^-?\d+$/.test(trimmed)) return null
    const parsed = Number(trimmed)
    return isDomainMember(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** Write the mirror. Only ever called with a value the server has already accepted. */
export function writeRetentionMirror(minutes: number): void {
  if (typeof localStorage === 'undefined') return
  try {
    localStorage.setItem(RETENTION_MIRROR_KEY, String(minutes))
  } catch {
    // Private mode / quota: the mirror is a cache, the server is the truth.
  }
}

/** Pull the retention out of a GET/PATCH envelope, repairing whatever is in there. */
export function retentionFromResponse(body: UiSettingsResponse | null | undefined): number {
  return normalizeRetentionMinutes(body?.settings?.[RETENTION_FIELD])
}

/**
 * The choices to draw, taken from the server's envelope (L0003 §2-8).
 *
 * The screen must not hold its own array of nine values. When the response cannot be
 * read at all, the shared constant is the fallback — that is still one copy, not two.
 */
export function retentionDomainFrom(body: UiSettingsResponse | null | undefined): number[] {
  const shipped = body?.domain?.[RETENTION_FIELD]
  if (!Array.isArray(shipped)) return [...RETENTION_DOMAIN_MINUTES]
  const usable = shipped.filter(
    (value): value is number => typeof value === 'number' && Number.isInteger(value),
  )
  return usable.length > 0 ? usable : [...RETENTION_DOMAIN_MINUTES]
}
