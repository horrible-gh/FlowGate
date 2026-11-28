// flowgate.v02test.0081 (0029-T, 시안 v4): a time setting is two boxes — [number][unit ▾] — and is kept in seconds.
// Pure functions behind InspectorDurationInput: the units (hour · minute · second), the unit a value is first shown
// in (the largest one that divides it: 14400 → 4 hours, 150 → 150 seconds), the seconds a number and a unit stand
// for, and the nearest value inside a field's range (with which edge it was moved to).

export type DurationUnit = 'hour' | 'minute' | 'second'

/** The units, largest first, with their seconds. */
export const DURATION_UNITS: readonly (readonly [DurationUnit, number])[] = [
  ['hour', 3600],
  ['minute', 60],
  ['second', 1],
]

export function durationUnitSeconds(unit: DurationUnit): number {
  return DURATION_UNITS.find(([name]) => name === unit)![1]
}

/** The largest unit that divides the seconds (0 and anything not a whole number of seconds: seconds). */
export function durationUnitOf(seconds: number): DurationUnit {
  if (!Number.isSafeInteger(seconds) || seconds <= 0) return 'second'
  return DURATION_UNITS.find(([, size]) => seconds % size === 0)![0]
}

/** The number and unit a value is shown as: in `unit` when it divides the value, else in the largest unit that does. */
export function durationSplit(seconds: number, unit?: DurationUnit | null): { amount: number; unit: DurationUnit } {
  const shown = unit && Number.isSafeInteger(seconds) && seconds % durationUnitSeconds(unit) === 0 ? unit : durationUnitOf(seconds)
  return { amount: Number.isFinite(seconds) ? seconds / durationUnitSeconds(shown) : 0, unit: shown }
}

/**
 * The number typed into the box: a whole number, else undefined (nothing is sent). A sign is allowed (-1, +5): a number
 * below the field's lowest value — a negative one too — is a whole number out of range, moved to the edge by the caller.
 */
export function durationAmountOf(text: string): number | undefined {
  const trimmed = text.trim()
  if (!/^[+-]?[0-9]+$/.test(trimmed)) return undefined
  const number = Number(trimmed)
  return Number.isSafeInteger(number) ? number : undefined
}

/** number × unit = seconds. */
export function durationSeconds(amount: number, unit: DurationUnit): number {
  return amount * durationUnitSeconds(unit)
}

/** Where a value was moved to fit the range: below the lowest → 'min', above the highest → 'max', else null. */
export type DurationClamp = 'min' | 'max' | null

/** The nearest whole number of seconds inside [min, max] and which edge, if any, the value was moved to. */
export function durationClamp(seconds: number, min: number, max: number): { value: number; clamped: DurationClamp } {
  if (seconds < min) return { value: min, clamped: 'min' }
  if (seconds > max) return { value: max, clamped: 'max' }
  return { value: Math.round(seconds), clamped: null }
}

/** A value as parts for its sentence (5400 → [[1, 'hour'], [30, 'minute']]; 0 → [[0, 'second']]). */
export function durationParts(seconds: number): [number, DurationUnit][] {
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const rest = total % 60
  const parts: [number, DurationUnit][] = []
  if (hours) parts.push([hours, 'hour'])
  if (minutes) parts.push([minutes, 'minute'])
  if (rest || parts.length === 0) parts.push([rest, 'second'])
  return parts
}
